"""Bounded one-request/one-response JSON RPC over a private Unix socket."""

from __future__ import annotations

import json
import os
import socket
import stat
import struct
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

DEFAULT_TIMEOUT: Final = 10.0
MAX_MESSAGE_BYTES: Final = 1024 * 1024
_SOCKET_DIRECTORY: Final = "universal-usb"
_SOCKET_NAME: Final = "control.sock"


class RpcError(RuntimeError):
    """Base class for local RPC failures."""


class RpcUnavailable(RpcError):
    """The daemon socket could not be reached."""


class RpcTimeout(RpcUnavailable):
    """The RPC deadline expired."""


class RpcProtocolError(RpcError):
    """A peer sent a malformed or out-of-contract message."""


class RpcPermissionError(RpcError):
    """A socket, directory, or peer failed the private-UID policy."""


class RpcRemoteError(RpcError):
    """A structured error returned by the daemon."""

    def __init__(self, code: int | str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def socket_path(runtime_dir: str | os.PathLike[str] | None = None) -> Path:
    """Return the one controller socket path without creating anything."""

    if runtime_dir is None:
        value = os.environ.get("XDG_RUNTIME_DIR")
        if not value:
            raise RpcUnavailable("XDG_RUNTIME_DIR is not set")
        runtime = Path(value)
    else:
        runtime = Path(runtime_dir)
    if not runtime.is_absolute():
        raise RpcPermissionError("runtime directory must be an absolute path")
    return runtime / _SOCKET_DIRECTORY / _SOCKET_NAME


def prepare_socket_directory(path: Path, *, uid: int | None = None) -> Path:
    """Create or validate the private mode-0700 directory containing *path*."""

    owner = os.geteuid() if uid is None else uid
    directory = path.parent
    try:
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    except FileExistsError:
        pass
    except OSError as error:
        raise RpcPermissionError(f"cannot create RPC directory: {error}") from error
    try:
        info = directory.lstat()
    except OSError as error:
        raise RpcPermissionError(f"cannot inspect RPC directory: {error}") from error
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RpcPermissionError("RPC directory must be a real directory")
    if info.st_uid != owner:
        raise RpcPermissionError("RPC directory is not owned by the daemon UID")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise RpcPermissionError("RPC directory permissions must be 0700")
    return directory

def validate_client_socket(path: Path, *, uid: int | None = None) -> None:
    """Reject connection paths that do not satisfy the private socket policy."""

    owner = os.geteuid() if uid is None else uid
    try:
        directory = path.parent.lstat()
        endpoint = path.lstat()
    except FileNotFoundError as error:
        raise RpcUnavailable("daemon socket does not exist") from error
    except OSError as error:
        raise RpcUnavailable(f"cannot inspect daemon socket: {error}") from error
    if (
        stat.S_ISLNK(directory.st_mode)
        or not stat.S_ISDIR(directory.st_mode)
        or directory.st_uid != owner
        or stat.S_IMODE(directory.st_mode) != 0o700
    ):
        raise RpcPermissionError("daemon socket directory must be owned mode 0700")
    if (
        stat.S_ISLNK(endpoint.st_mode)
        or not stat.S_ISSOCK(endpoint.st_mode)
        or endpoint.st_uid != owner
        or stat.S_IMODE(endpoint.st_mode) != 0o600
    ):
        raise RpcPermissionError("daemon endpoint must be an owned mode-0600 socket")


def open_server_socket(
    path: str | os.PathLike[str] | None = None,
    *,
    backlog: int = 16,
) -> socket.socket:
    """Bind a private mode-0600 server socket, replacing only a stale owned socket."""

    if isinstance(backlog, bool) or not isinstance(backlog, int) or backlog < 1:
        raise ValueError("backlog must be a positive integer")
    target = socket_path() if path is None else Path(path)
    uid = os.geteuid()
    prepare_socket_directory(target, uid=uid)
    try:
        existing = target.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise RpcPermissionError(f"cannot inspect RPC socket: {error}") from error
    else:
        if stat.S_ISLNK(existing.st_mode) or not stat.S_ISSOCK(existing.st_mode):
            raise RpcPermissionError("refusing to replace a non-socket RPC path")
        if existing.st_uid != uid:
            raise RpcPermissionError("refusing to replace another UID's RPC socket")
        try:
            target.unlink()
        except OSError as error:
            raise RpcPermissionError(f"cannot remove stale RPC socket: {error}") from error

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(os.fspath(target))
        os.chmod(target, 0o600, follow_symlinks=False)
        info = target.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid:
            raise RpcPermissionError("bound RPC path failed socket ownership validation")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise RpcPermissionError("RPC socket permissions must be 0600")
        server.listen(backlog)
    except Exception:
        server.close()
        try:
            info = target.lstat()
            if stat.S_ISSOCK(info.st_mode) and info.st_uid == uid:
                target.unlink()
        except OSError:
            pass
        raise
    return server


def accept_same_uid(
    server: socket.socket,
    *,
    uid: int | None = None,
) -> tuple[socket.socket, int]:
    """Accept one connection and reject peers whose effective UID differs."""

    connection, _ = server.accept()
    expected = os.geteuid() if uid is None else uid
    try:
        peer_uid = _peer_uid(connection)
        if peer_uid != expected:
            raise RpcPermissionError(
                f"RPC peer UID {peer_uid} does not match daemon UID {expected}"
            )
    except Exception:
        connection.close()
        raise
    return connection, peer_uid


def receive_request(connection: socket.socket) -> tuple[int | str, str, dict[str, Any]]:
    """Read and validate one exact request envelope."""

    value = _decode_line(_receive_line(connection))
    if not isinstance(value, dict) or set(value) != {"id", "method", "params"}:
        raise RpcProtocolError("request must contain exactly id, method, and params")
    request_id = _validate_id(value["id"])
    method = value["method"]
    params = value["params"]
    if not isinstance(method, str) or not method or len(method) > 128:
        raise RpcProtocolError("request method must be a non-empty bounded string")
    if not isinstance(params, dict):
        raise RpcProtocolError("request params must be an object")
    return request_id, method, params


def send_result(connection: socket.socket, request_id: int | str, result: Any) -> None:
    """Send one exact success envelope."""

    _send_message(connection, {"id": _validate_id(request_id), "result": result})


def send_error(
    connection: socket.socket,
    request_id: int | str,
    code: int | str,
    message: str,
) -> None:
    """Send one exact error envelope without exception or secret details."""

    if isinstance(code, bool) or not (
        isinstance(code, int) and 2 <= code <= 8
        or isinstance(code, str) and 0 < len(code) <= 128
    ):
        raise ValueError("error code must be exit 2..8 or a non-empty bounded string")
    if not isinstance(message, str) or not message or len(message) > 4096:
        raise ValueError("error message must be a non-empty bounded string")
    _send_message(
        connection,
        {"id": _validate_id(request_id), "error": {"code": code, "message": message}},
    )


class RpcClient:
    """Synchronous client with one absolute deadline per call."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("RPC timeout must be positive")
        self.path = socket_path() if path is None else Path(path)
        self.timeout = float(timeout)
        self._next_id = 1

    def call(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        if not isinstance(method, str) or not method or len(method) > 128:
            raise ValueError("RPC method must be a non-empty bounded string")
        validate_client_socket(self.path)
        payload = {} if params is None else dict(params)
        request_id = self._next_id
        self._next_id += 1
        deadline = time.monotonic() + self.timeout
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(_remaining(deadline))
            connection.connect(os.fspath(self.path))
            connection.settimeout(_remaining(deadline))
            _send_message(
                connection,
                {"id": request_id, "method": method, "params": payload},
            )
            connection.settimeout(_remaining(deadline))
            response = _decode_line(_receive_line(connection))
        except socket.timeout as error:
            raise RpcTimeout("RPC deadline expired") from error
        except (FileNotFoundError, ConnectionRefusedError, ConnectionResetError) as error:
            raise RpcUnavailable(f"daemon unavailable: {error}") from error
        except OSError as error:
            raise RpcUnavailable(f"daemon communication failed: {error}") from error
        finally:
            connection.close()
        return _parse_response(response, request_id)


def rpc_call(
    method: str,
    params: Mapping[str, Any] | None = None,
    *,
    path: str | os.PathLike[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Convenience wrapper for a single RPC call."""

    return RpcClient(path, timeout=timeout).call(method, params)


def _parse_response(value: Any, request_id: int | str) -> Any:
    if not isinstance(value, dict):
        raise RpcProtocolError("response must be an object")
    keys = set(value)
    if keys == {"id", "result"}:
        pass
    elif keys == {"id", "error"}:
        error = value["error"]
        if not isinstance(error, dict) or set(error) != {"code", "message"}:
            raise RpcProtocolError("response error must contain exactly code and message")
        code = error["code"]
        message = error["message"]
        if isinstance(code, bool) or not (
            isinstance(code, int) and 2 <= code <= 8
            or isinstance(code, str) and 0 < len(code) <= 128
        ):
            raise RpcProtocolError("response error code is invalid")
        if not isinstance(message, str) or not message or len(message) > 4096:
            raise RpcProtocolError("response error message is invalid")
        if value["id"] != request_id:
            raise RpcProtocolError("response id does not match request id")
        raise RpcRemoteError(code, message)
    else:
        raise RpcProtocolError("response must contain exactly id/result or id/error")
    if value["id"] != request_id:
        raise RpcProtocolError("response id does not match request id")
    return value["result"]


def _peer_uid(connection: socket.socket) -> int:
    if not hasattr(socket, "SO_PEERCRED"):
        raise RpcPermissionError("this platform does not expose Unix peer credentials")
    try:
        credentials = connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
        )
        _, uid, _ = struct.unpack("3i", credentials)
    except (OSError, struct.error) as error:
        raise RpcPermissionError("cannot obtain RPC peer credentials") from error
    return uid


def _validate_id(value: Any) -> int | str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise RpcProtocolError("RPC id must be an integer or string")
    if isinstance(value, str) and (not value or len(value) > 128):
        raise RpcProtocolError("string RPC id must be non-empty and bounded")
    return value


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RpcTimeout("RPC deadline expired")
    return remaining


def _receive_line(connection: socket.socket) -> bytes:
    data = bytearray()
    while True:
        chunk = connection.recv(min(65536, MAX_MESSAGE_BYTES + 1 - len(data)))
        if not chunk:
            raise RpcProtocolError("RPC peer closed before the newline terminator")
        newline = chunk.find(b"\n")
        if newline >= 0:
            data.extend(chunk[:newline])
            if newline != len(chunk) - 1:
                raise RpcProtocolError("RPC peer sent data after the one response")
            if len(data) > MAX_MESSAGE_BYTES:
                raise RpcProtocolError("RPC message exceeds the response bound")
            return bytes(data)
        data.extend(chunk)
        if len(data) > MAX_MESSAGE_BYTES:
            raise RpcProtocolError("RPC message exceeds the response bound")


def _decode_line(data: bytes) -> Any:
    try:
        text = data.decode("utf-8")
        return json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RpcProtocolError("RPC message is not valid UTF-8 JSON") from error


def _send_message(connection: socket.socket, value: Any) -> None:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as error:
        raise RpcProtocolError("RPC value is not JSON encodable") from error
    if len(encoded) - 1 > MAX_MESSAGE_BYTES:
        raise RpcProtocolError("RPC message exceeds the request bound")
    connection.sendall(encoded)
