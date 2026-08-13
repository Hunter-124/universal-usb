"""Bounded CTAPHID bridge and a stateful CTAP2 ES256 authenticator.

The firmware performs USB report reassembly and forwards logical bridge
messages as ``command || payload``.  :class:`FidoBackend` mirrors the command
byte in its response.  The framing helpers in this module are provided for
focused transport tests and tools; the controller daemon should use
:meth:`FidoBackend.handle_bridge`.

This authenticator implements the small WebAuthn surface needed by Universal
USB: getInfo, makeCredential, getAssertion, getNextAssertion, and reset.  User
presence is an explicit synthetic event supplied by :meth:`FidoBackend.touch`.
There is no user-verification implementation.  P-256 key generation and ECDSA
signing are delegated to the system ``openssl`` executable, with no fallback.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import threading
from typing import Any

from . import cbor
from .state import atomic_write_json, read_json, remove_state_file, secure_state_dir


CTAPHID_REPORT_SIZE = 64
CTAPHID_INITIAL_DATA_SIZE = 57
CTAPHID_CONTINUATION_DATA_SIZE = 59
CTAPHID_MAX_MESSAGE_SIZE = 512
CTAPHID_BROADCAST_CID = 0xFFFFFFFF

CTAPHID_PING = 0x81
CTAPHID_INIT = 0x86
CTAPHID_WINK = 0x88
CTAPHID_CBOR = 0x90
CTAPHID_CANCEL = 0x91
CTAPHID_KEEPALIVE = 0xBB
CTAPHID_ERROR = 0xBF

CTAPHID_ERR_INVALID_CMD = 0x01
CTAPHID_ERR_INVALID_PAR = 0x02
CTAPHID_ERR_INVALID_LEN = 0x03
CTAPHID_ERR_INVALID_SEQ = 0x04
CTAPHID_ERR_MSG_TIMEOUT = 0x05
CTAPHID_ERR_CHANNEL_BUSY = 0x06
CTAPHID_ERR_INVALID_CHANNEL = 0x0B
CTAPHID_ERR_OTHER = 0x7F

CTAPHID_KEEPALIVE_PROCESSING = 0x01
CTAPHID_KEEPALIVE_UP_NEEDED = 0x02
CTAPHID_CAPABILITY_WINK = 0x01
CTAPHID_CAPABILITY_CBOR = 0x04

CTAP2_MAKE_CREDENTIAL = 0x01
CTAP2_GET_ASSERTION = 0x02
CTAP2_GET_INFO = 0x04
CTAP2_RESET = 0x07
CTAP2_GET_NEXT_ASSERTION = 0x08

CTAP2_OK = 0x00
CTAP1_ERR_INVALID_COMMAND = 0x01
CTAP1_ERR_INVALID_PARAMETER = 0x02
CTAP1_ERR_INVALID_LENGTH = 0x03
CTAP2_ERR_CBOR_UNEXPECTED_TYPE = 0x11
CTAP2_ERR_INVALID_CBOR = 0x12
CTAP2_ERR_MISSING_PARAMETER = 0x14
CTAP2_ERR_LIMIT_EXCEEDED = 0x15
CTAP2_ERR_CREDENTIAL_EXCLUDED = 0x19
CTAP2_ERR_UNSUPPORTED_ALGORITHM = 0x26
CTAP2_ERR_OPERATION_DENIED = 0x27
CTAP2_ERR_KEY_STORE_FULL = 0x28
CTAP2_ERR_UNSUPPORTED_OPTION = 0x2B
CTAP2_ERR_INVALID_OPTION = 0x2C
CTAP2_ERR_KEEPALIVE_CANCEL = 0x2D
CTAP2_ERR_NO_CREDENTIALS = 0x2E
CTAP2_ERR_NOT_ALLOWED = 0x30
CTAP2_ERR_REQUEST_TOO_LARGE = 0x39
CTAP2_ERR_OTHER = 0x7F

COSE_ALGORITHM_ES256 = -7
COSE_KEY_TYPE_EC2 = 2
COSE_CURVE_P256 = 1

AUTH_DATA_FLAG_UP = 0x01
AUTH_DATA_FLAG_UV = 0x04
AUTH_DATA_FLAG_AT = 0x40

MAX_CREDENTIALS = 64
MAX_ALLOW_LIST = 64
MAX_CREDENTIAL_ID_LENGTH = 1_024
MAX_RP_ID_BYTES = 253
MAX_USER_ID_BYTES = 64
MAX_ENTITY_TEXT_BYTES = 128
MAX_PRIVATE_KEY_BYTES = 4_096
MAX_OPENSSL_OUTPUT = 8_192
OPENSSL_TIMEOUT_SECONDS = 10.0
STATE_VERSION = 1
STATE_FILENAME = "fido.json"

# Project-local, non-vendor AAGUID.  It deliberately does not claim another
# authenticator's certification or identity.
AAGUID = bytes.fromhex("22d7203e74684ff1a0f9257b18f44d47")

_P256_ORDER = int(
    "ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16
)
_P256_SPKI_PREFIX = bytes.fromhex(
    "3059301306072a8648ce3d020106082a8648ce3d03010703420004"
)


class TouchRequired(RuntimeError):
    """The request is valid but awaits one synthetic user-presence event."""


class OpenSSLError(RuntimeError):
    """The system OpenSSL operation failed or returned malformed output."""


class HidFramingError(ValueError):
    """A CTAPHID report violates framing rules."""

    def __init__(self, error_code: int, cid: int, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.cid = cid


class _CtapError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class _CredentialStoreError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HidMessage:
    """One complete logical CTAPHID message."""

    cid: int
    command: int
    payload: bytes


@dataclass(slots=True)
class _Assembly:
    command: int
    length: int
    data: bytearray
    next_sequence: int = 0


class CtaphidFramer:
    """Strictly reassemble bounded 64-byte CTAPHID reports by channel."""

    def __init__(self, *, max_channels: int = 4) -> None:
        if type(max_channels) is not int or max_channels <= 0:
            raise ValueError("max_channels must be a positive integer")
        self._max_channels = max_channels
        self._assemblies: dict[int, _Assembly] = {}

    def cancel(self, cid: int) -> None:
        self._assemblies.pop(cid, None)

    def clear(self) -> None:
        self._assemblies.clear()

    def feed(self, report: bytes | bytearray | memoryview) -> HidMessage | None:
        raw = bytes(report)
        if len(raw) != CTAPHID_REPORT_SIZE:
            raise HidFramingError(
                CTAPHID_ERR_INVALID_LEN, 0, "CTAPHID report must be exactly 64 bytes"
            )
        cid = int.from_bytes(raw[:4], "big")
        if cid == 0:
            raise HidFramingError(
                CTAPHID_ERR_INVALID_CHANNEL, cid, "CTAPHID channel zero is invalid"
            )
        marker = raw[4]
        if marker & 0x80:
            length = int.from_bytes(raw[5:7], "big")
            if length > CTAPHID_MAX_MESSAGE_SIZE:
                raise HidFramingError(
                    CTAPHID_ERR_INVALID_LEN, cid, "CTAPHID message exceeds bridge limit"
                )
            if cid in self._assemblies:
                raise HidFramingError(
                    CTAPHID_ERR_CHANNEL_BUSY, cid, "channel already has an active message"
                )
            amount = min(length, CTAPHID_INITIAL_DATA_SIZE)
            data = bytearray(raw[7 : 7 + amount])
            if amount == length:
                return HidMessage(cid, marker, bytes(data))
            if len(self._assemblies) >= self._max_channels:
                raise HidFramingError(
                    CTAPHID_ERR_CHANNEL_BUSY, cid, "too many active CTAPHID channels"
                )
            self._assemblies[cid] = _Assembly(marker, length, data)
            return None

        assembly = self._assemblies.get(cid)
        if assembly is None:
            raise HidFramingError(
                CTAPHID_ERR_INVALID_SEQ, cid, "continuation has no initial report"
            )
        if marker != assembly.next_sequence:
            del self._assemblies[cid]
            raise HidFramingError(
                CTAPHID_ERR_INVALID_SEQ, cid, "CTAPHID continuation is out of sequence"
            )
        assembly.next_sequence += 1
        if assembly.next_sequence > 0x80:
            del self._assemblies[cid]
            raise HidFramingError(
                CTAPHID_ERR_INVALID_SEQ, cid, "too many CTAPHID continuation reports"
            )
        remaining = assembly.length - len(assembly.data)
        assembly.data.extend(raw[5 : 5 + min(remaining, CTAPHID_CONTINUATION_DATA_SIZE)])
        if len(assembly.data) != assembly.length:
            return None
        del self._assemblies[cid]
        return HidMessage(cid, assembly.command, bytes(assembly.data))


def frame_hid_message(
    cid: int, command: int, payload: bytes | bytearray | memoryview
) -> tuple[bytes, ...]:
    """Split one logical CTAPHID message into zero-padded 64-byte reports."""

    data = bytes(payload)
    if type(cid) is not int or not 1 <= cid <= 0xFFFFFFFF:
        raise ValueError("CTAPHID channel is out of range")
    if type(command) is not int or not 0x80 <= command <= 0xFF:
        raise ValueError("CTAPHID command must have its high bit set")
    if len(data) > CTAPHID_MAX_MESSAGE_SIZE:
        raise ValueError("CTAPHID message exceeds bridge limit")

    initial = bytearray(CTAPHID_REPORT_SIZE)
    initial[:4] = cid.to_bytes(4, "big")
    initial[4] = command
    initial[5:7] = len(data).to_bytes(2, "big")
    amount = min(len(data), CTAPHID_INITIAL_DATA_SIZE)
    initial[7 : 7 + amount] = data[:amount]
    reports = [bytes(initial)]
    offset = amount
    sequence = 0
    while offset < len(data):
        continuation = bytearray(CTAPHID_REPORT_SIZE)
        continuation[:4] = cid.to_bytes(4, "big")
        continuation[4] = sequence
        amount = min(len(data) - offset, CTAPHID_CONTINUATION_DATA_SIZE)
        continuation[5 : 5 + amount] = data[offset : offset + amount]
        reports.append(bytes(continuation))
        offset += amount
        sequence += 1
    return tuple(reports)


def der_signature_to_raw(signature: bytes | bytearray | memoryview) -> bytes:
    """Strictly convert one P-256 DER ECDSA signature to ``r || s``."""

    data = bytes(signature)
    if len(data) < 8 or data[0] != 0x30:
        raise OpenSSLError("OpenSSL returned a malformed ECDSA signature")
    sequence_length, offset = _read_der_length(data, 1)
    if offset + sequence_length != len(data):
        raise OpenSSLError("OpenSSL returned a malformed ECDSA signature")
    r, offset = _read_der_integer(data, offset)
    s, offset = _read_der_integer(data, offset)
    if offset != len(data) or not 1 <= r < _P256_ORDER or not 1 <= s < _P256_ORDER:
        raise OpenSSLError("OpenSSL returned an out-of-range ECDSA signature")
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def raw_signature_to_der(signature: bytes | bytearray | memoryview) -> bytes:
    """Convert a 64-byte P-256 ``r || s`` signature to strict DER."""

    raw = bytes(signature)
    if len(raw) != 64:
        raise ValueError("raw P-256 signature must be exactly 64 bytes")
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    if not 1 <= r < _P256_ORDER or not 1 <= s < _P256_ORDER:
        raise ValueError("raw P-256 signature scalar is out of range")
    body = _der_integer(r) + _der_integer(s)
    return b"\x30" + _der_length(len(body)) + body


def p256_public_key_der(x: bytes, y: bytes) -> bytes:
    """Build the strict SubjectPublicKeyInfo DER form for a P-256 point."""

    if type(x) is not bytes or type(y) is not bytes or len(x) != 32 or len(y) != 32:
        raise ValueError("P-256 coordinates must be 32-byte strings")
    return _P256_SPKI_PREFIX + x + y


def _read_der_length(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise OpenSSLError("truncated DER length")
    initial = data[offset]
    offset += 1
    if initial < 0x80:
        return initial, offset
    width = initial & 0x7F
    if width == 0 or width > 2 or offset + width > len(data):
        raise OpenSSLError("invalid DER length")
    if data[offset] == 0:
        raise OpenSSLError("non-minimal DER length")
    value = int.from_bytes(data[offset : offset + width], "big")
    if value < 0x80:
        raise OpenSSLError("non-minimal DER length")
    return value, offset + width


def _read_der_integer(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data) or data[offset] != 0x02:
        raise OpenSSLError("missing DER INTEGER")
    length, offset = _read_der_length(data, offset + 1)
    end = offset + length
    if length == 0 or length > 33 or end > len(data):
        raise OpenSSLError("invalid DER INTEGER length")
    encoded = data[offset:end]
    if encoded[0] & 0x80:
        raise OpenSSLError("negative DER INTEGER")
    if len(encoded) > 1 and encoded[0] == 0 and not encoded[1] & 0x80:
        raise OpenSSLError("non-minimal DER INTEGER")
    return int.from_bytes(encoded, "big"), end


def _der_length(length: int) -> bytes:
    if length < 0x80:
        return bytes((length,))
    encoded = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes((0x80 | len(encoded),)) + encoded


def _der_integer(value: int) -> bytes:
    encoded = value.to_bytes((value.bit_length() + 7) // 8, "big")
    if encoded[0] & 0x80:
        encoded = b"\x00" + encoded
    return b"\x02" + _der_length(len(encoded)) + encoded


@dataclass(slots=True)
class _AssertionContext:
    rp_id: str
    client_data_hash: bytes
    credential_ids: list[bytes]
    include_user: bool


class FidoBackend:
    """Stateful CTAP2 backend for firmware bridge messages.

    ``state_directory`` is secured through :func:`uusb.state.secure_state_dir`.
    Private keys are never returned by management methods.  A request that
    needs user presence raises :class:`TouchRequired`; callers can keep the
    mailbox operation pending, call :meth:`touch`, and retry the same bytes.
    """

    def __init__(
        self,
        state_directory: str | os.PathLike[str] | None = None,
        *,
        openssl: str = "openssl",
        max_credentials: int = MAX_CREDENTIALS,
    ) -> None:
        if not openssl or "\x00" in openssl:
            raise ValueError("openssl executable must be a non-empty path")
        if type(max_credentials) is not int or not 1 <= max_credentials <= MAX_CREDENTIALS:
            raise ValueError(f"max_credentials must be between 1 and {MAX_CREDENTIALS}")
        self._state_path = secure_state_dir(state_directory) / STATE_FILENAME
        self._openssl = openssl
        self._max_credentials = max_credentials
        self._lock = threading.RLock()
        self._touched = False
        self._pending_assertions: _AssertionContext | None = None
        self._next_channel = 1

    @property
    def state_path(self) -> Path:
        return self._state_path

    def touch(self) -> bool:
        """Supply one synthetic UP event, consumed by one completed operation."""

        with self._lock:
            self._touched = True
            return True

    def status(self) -> dict[str, Any]:
        """Return non-secret backend status suitable for RPC output."""

        with self._lock:
            credentials = self._load_credentials()
            return {
                "credential_count": len(credentials),
                "max_credentials": self._max_credentials,
                "touch_pending": self._touched,
                "assertions_pending": (
                    len(self._pending_assertions.credential_ids)
                    if self._pending_assertions is not None
                    else 0
                ),
                "user_presence": "synthetic",
                "user_verification": False,
                "algorithm": "ES256",
            }

    def list(self) -> list[dict[str, Any]]:
        """List public credential metadata without private key material."""

        with self._lock:
            result: list[dict[str, Any]] = []
            for credential in self._load_credentials():
                result.append(
                    {
                        "id": _base64url(credential["credential_id"]),
                        "rp_id": credential["rp_id"],
                        "rp_name": credential["rp_name"],
                        "user_id": _base64url(credential["user_id"]),
                        "user_name": credential["user_name"],
                        "user_display_name": credential["user_display_name"],
                        "discoverable": credential["discoverable"],
                        "signature_counter": credential["signature_counter"],
                    }
                )
            return result

    def list_credentials(self) -> list[dict[str, Any]]:
        return self.list()

    def delete(self, credential_id: bytes | str) -> bool:
        """Delete one credential by raw or unpadded base64url identifier."""

        identifier = _parse_credential_identifier(credential_id)
        with self._lock:
            credentials = self._load_credentials()
            kept = [item for item in credentials if item["credential_id"] != identifier]
            if len(kept) == len(credentials):
                return False
            self._write_credentials(kept)
            if self._pending_assertions is not None:
                self._pending_assertions.credential_ids = [
                    value
                    for value in self._pending_assertions.credential_ids
                    if value != identifier
                ]
                if not self._pending_assertions.credential_ids:
                    self._pending_assertions = None
            return True

    def delete_credential(self, credential_id: bytes | str) -> bool:
        return self.delete(credential_id)

    def reset(self) -> None:
        """Immediately clear FIDO state for the management RPC surface."""

        with self._lock:
            remove_state_file(self._state_path)
            self._pending_assertions = None
            self._touched = False

    def handle_hid_message(self, data: bytes | bytearray | memoryview) -> bytes:
        """Compatibility name for :meth:`handle_bridge` logical messages."""

        return self.handle_bridge(data)

    def handle_bridge(self, data: bytes | bytearray | memoryview) -> bytes:
        """Handle one bounded ``CTAPHID command || payload`` bridge message.

        :class:`TouchRequired` intentionally propagates so the daemon can keep
        the request pending and let firmware emit keepalives.  All completed
        forwarded commands mirror their command byte.
        """

        raw = bytes(data)
        if not raw:
            return bytes((CTAPHID_ERROR, CTAPHID_ERR_INVALID_LEN))
        if len(raw) > CTAPHID_MAX_MESSAGE_SIZE:
            return bytes((CTAPHID_ERROR, CTAPHID_ERR_INVALID_LEN))
        command = raw[0]
        payload = raw[1:]
        with self._lock:
            if command == CTAPHID_INIT:
                if len(payload) != 8:
                    return bytes((CTAPHID_ERROR, CTAPHID_ERR_INVALID_LEN))
                cid = self._allocate_channel()
                response = (
                    payload
                    + cid.to_bytes(4, "big")
                    + bytes(
                        (
                            2,
                            1,
                            0,
                            0,
                            CTAPHID_CAPABILITY_WINK | CTAPHID_CAPABILITY_CBOR,
                        )
                    )
                )
                return bytes((command,)) + response
            if command == CTAPHID_PING:
                return raw
            if command == CTAPHID_WINK:
                if payload:
                    return bytes((CTAPHID_ERROR, CTAPHID_ERR_INVALID_LEN))
                return bytes((command,))
            if command == CTAPHID_CBOR:
                if not payload:
                    return bytes((command, CTAP1_ERR_INVALID_LENGTH))
                response = self._handle_ctap(payload)
                if len(response) + 1 > CTAPHID_MAX_MESSAGE_SIZE:
                    response = bytes((CTAP2_ERR_LIMIT_EXCEEDED,))
                return bytes((command,)) + response
            if command == CTAPHID_CANCEL:
                if payload:
                    return bytes((CTAPHID_ERROR, CTAPHID_ERR_INVALID_LEN))
                self._pending_assertions = None
                return bytes((command,))
            if command in (CTAPHID_KEEPALIVE, CTAPHID_ERROR):
                return bytes((CTAPHID_ERROR, CTAPHID_ERR_INVALID_CMD))
            return bytes((CTAPHID_ERROR, CTAPHID_ERR_INVALID_CMD))

    def handle_command(self, command: int, payload: bytes = b"") -> bytes:
        """Handle a command and return its payload without the mirrored byte."""

        response = self.handle_bridge(bytes((command,)) + bytes(payload))
        if response and response[0] == command:
            return response[1:]
        return response

    def _allocate_channel(self) -> int:
        cid = self._next_channel
        self._next_channel += 1
        if self._next_channel in (0, CTAPHID_BROADCAST_CID):
            self._next_channel = 1
        return cid

    def _handle_ctap(self, request: bytes) -> bytes:
        if len(request) > CTAPHID_MAX_MESSAGE_SIZE - 1:
            return bytes((CTAP2_ERR_REQUEST_TOO_LARGE,))
        command = request[0]
        encoded_parameters = request[1:]
        try:
            if command == CTAP2_GET_INFO:
                if encoded_parameters:
                    raise _CtapError(CTAP1_ERR_INVALID_LENGTH)
                self._pending_assertions = None
                value = self._get_info()
            elif command == CTAP2_MAKE_CREDENTIAL:
                value = self._make_credential(self._decode_parameter_map(encoded_parameters))
            elif command == CTAP2_GET_ASSERTION:
                value = self._get_assertion(self._decode_parameter_map(encoded_parameters))
            elif command == CTAP2_GET_NEXT_ASSERTION:
                if encoded_parameters:
                    raise _CtapError(CTAP1_ERR_INVALID_LENGTH)
                value = self._get_next_assertion()
            elif command == CTAP2_RESET:
                if encoded_parameters:
                    raise _CtapError(CTAP1_ERR_INVALID_LENGTH)
                self._ctap_reset()
                value = None
            else:
                raise _CtapError(CTAP1_ERR_INVALID_COMMAND)
            if value is None:
                return bytes((CTAP2_OK,))
            encoded = cbor.dumps(
                value,
                max_depth=12,
                max_items=256,
                max_bytes=CTAPHID_MAX_MESSAGE_SIZE - 2,
                max_string_bytes=CTAPHID_MAX_MESSAGE_SIZE - 2,
            )
            return bytes((CTAP2_OK,)) + encoded
        except TouchRequired:
            raise
        except _CtapError as error:
            return bytes((error.status,))
        except cbor.CBORLimitError:
            return bytes((CTAP2_ERR_LIMIT_EXCEEDED,))
        except (cbor.CBORError, UnicodeError):
            return bytes((CTAP2_ERR_INVALID_CBOR,))
        except (OpenSSLError, OSError, subprocess.SubprocessError, _CredentialStoreError):
            return bytes((CTAP2_ERR_OTHER,))
        except Exception:
            return bytes((CTAP2_ERR_OTHER,))

    def _decode_parameter_map(self, encoded: bytes) -> dict[Any, Any]:
        if not encoded:
            raise _CtapError(CTAP2_ERR_MISSING_PARAMETER)
        try:
            value = cbor.loads(
                encoded,
                max_depth=10,
                max_items=256,
                max_bytes=CTAPHID_MAX_MESSAGE_SIZE - 2,
                max_string_bytes=CTAPHID_MAX_MESSAGE_SIZE - 2,
            )
        except cbor.CBORLimitError as error:
            raise _CtapError(CTAP2_ERR_LIMIT_EXCEEDED) from error
        except cbor.CBORError as error:
            raise _CtapError(CTAP2_ERR_INVALID_CBOR) from error
        if type(value) is not dict:
            raise _CtapError(CTAP2_ERR_CBOR_UNEXPECTED_TYPE)
        return value

    def _get_info(self) -> dict[int, Any]:
        return {
            1: ["FIDO_2_0"],
            2: [],
            3: AAGUID,
            4: {
                "rk": True,
                "up": True,
                "uv": False,
                "plat": False,
            },
            5: CTAPHID_MAX_MESSAGE_SIZE - 1,
            10: [{"alg": COSE_ALGORITHM_ES256, "type": "public-key"}],
        }

    def _make_credential(self, request: dict[Any, Any]) -> dict[int, Any]:
        client_data_hash = _required_bytes(request, 1, exact_length=32)
        rp = _required_map(request, 2)
        user = _required_map(request, 3)
        parameters = _required_list(request, 4)
        rp_id = _required_text(rp, "id", MAX_RP_ID_BYTES)
        rp_name = _optional_text(rp, "name", MAX_ENTITY_TEXT_BYTES)
        user_id = _required_bytes(user, "id", maximum=MAX_USER_ID_BYTES)
        if not user_id:
            raise _CtapError(CTAP1_ERR_INVALID_PARAMETER)
        user_name = _optional_text(user, "name", MAX_ENTITY_TEXT_BYTES)
        user_display_name = _optional_text(
            user, "displayName", MAX_ENTITY_TEXT_BYTES
        )
        _select_es256(parameters)

        options = _optional_map(request, 7)
        discoverable = _option(options, "rk", False)
        if _option(options, "uv", False):
            raise _CtapError(CTAP2_ERR_UNSUPPORTED_OPTION)
        if "up" in options and not _option(options, "up", True):
            raise _CtapError(CTAP2_ERR_INVALID_OPTION)
        if 8 in request or 9 in request:
            raise _CtapError(CTAP2_ERR_UNSUPPORTED_OPTION)

        credentials = self._load_credentials()
        exclude = _credential_ids(request[5]) if 5 in request else []
        if any(
            item["rp_id"] == rp_id and item["credential_id"] in exclude
            for item in credentials
        ):
            self._check_touch()
            self._consume_touch()
            self._pending_assertions = None
            raise _CtapError(CTAP2_ERR_CREDENTIAL_EXCLUDED)
        if len(credentials) >= self._max_credentials:
            raise _CtapError(CTAP2_ERR_KEY_STORE_FULL)
        self._check_touch()

        private_key, x, y = self._generate_key()
        credential_id = secrets.token_bytes(32)
        credential = {
            "credential_id": credential_id,
            "rp_id": rp_id,
            "rp_name": rp_name,
            "user_id": user_id,
            "user_name": user_name,
            "user_display_name": user_display_name,
            "discoverable": discoverable,
            "private_key": private_key,
            "x": x,
            "y": y,
            "signature_counter": 0,
        }
        self._write_credentials(credentials + [credential])

        cose_key = {
            1: COSE_KEY_TYPE_EC2,
            3: COSE_ALGORITHM_ES256,
            -1: COSE_CURVE_P256,
            -2: x,
            -3: y,
        }
        attested_data = (
            AAGUID
            + len(credential_id).to_bytes(2, "big")
            + credential_id
            + cbor.dumps(cose_key, max_bytes=256, max_string_bytes=64)
        )
        auth_data = (
            hashlib.sha256(rp_id.encode("utf-8")).digest()
            + bytes((AUTH_DATA_FLAG_UP | AUTH_DATA_FLAG_AT,))
            + (0).to_bytes(4, "big")
            + attested_data
        )
        self._pending_assertions = None
        self._consume_touch()
        return {1: "none", 2: auth_data, 3: {}}

    def _get_assertion(self, request: dict[Any, Any]) -> dict[int, Any]:
        rp_id = _required_text(request, 1, MAX_RP_ID_BYTES)
        client_data_hash = _required_bytes(request, 2, exact_length=32)
        options = _optional_map(request, 5)
        if _option(options, "uv", False):
            raise _CtapError(CTAP2_ERR_UNSUPPORTED_OPTION)
        if "up" in options and not _option(options, "up", True):
            raise _CtapError(CTAP2_ERR_INVALID_OPTION)
        if 6 in request or 7 in request:
            raise _CtapError(CTAP2_ERR_UNSUPPORTED_OPTION)

        credentials = self._load_credentials()
        allow_list_present = 3 in request
        if allow_list_present:
            allowed = _credential_ids(request[3])
            by_id = {item["credential_id"]: item for item in credentials}
            candidates = [
                by_id[identifier]
                for identifier in allowed
                if identifier in by_id and by_id[identifier]["rp_id"] == rp_id
            ]
        else:
            candidates = [
                item
                for item in credentials
                if item["rp_id"] == rp_id and item["discoverable"]
            ]
        if not candidates:
            raise _CtapError(CTAP2_ERR_NO_CREDENTIALS)
        self._check_touch()

        value = self._build_assertion(
            credentials,
            candidates[0]["credential_id"],
            rp_id,
            client_data_hash,
            include_user=not allow_list_present,
            number_of_credentials=len(candidates),
        )
        remaining = [item["credential_id"] for item in candidates[1:]]
        self._pending_assertions = (
            _AssertionContext(rp_id, client_data_hash, remaining, not allow_list_present)
            if remaining
            else None
        )
        self._consume_touch()
        return value

    def _get_next_assertion(self) -> dict[int, Any]:
        context = self._pending_assertions
        if context is None or not context.credential_ids:
            raise _CtapError(CTAP2_ERR_NOT_ALLOWED)
        credential_id = context.credential_ids[0]
        credentials = self._load_credentials()
        if not any(item["credential_id"] == credential_id for item in credentials):
            self._pending_assertions = None
            raise _CtapError(CTAP2_ERR_NOT_ALLOWED)
        value = self._build_assertion(
            credentials,
            credential_id,
            context.rp_id,
            context.client_data_hash,
            include_user=context.include_user,
            number_of_credentials=None,
        )
        del context.credential_ids[0]
        if not context.credential_ids:
            self._pending_assertions = None
        return value

    def _build_assertion(
        self,
        credentials: list[dict[str, Any]],
        credential_id: bytes,
        rp_id: str,
        client_data_hash: bytes,
        *,
        include_user: bool,
        number_of_credentials: int | None,
    ) -> dict[int, Any]:
        credential = next(
            item for item in credentials if item["credential_id"] == credential_id
        )
        if credential["signature_counter"] == 0xFFFFFFFF:
            raise _CtapError(CTAP2_ERR_LIMIT_EXCEEDED)
        counter = credential["signature_counter"] + 1
        auth_data = (
            hashlib.sha256(rp_id.encode("utf-8")).digest()
            + bytes((AUTH_DATA_FLAG_UP,))
            + counter.to_bytes(4, "big")
        )
        signature = self._sign(credential["private_key"], auth_data + client_data_hash)
        credential["signature_counter"] = counter
        self._write_credentials(credentials)

        result: dict[int, Any] = {
            1: {"id": credential_id, "type": "public-key"},
            2: auth_data,
            3: signature,
        }
        if include_user:
            user: dict[str, Any] = {"id": credential["user_id"]}
            if credential["user_name"]:
                user["name"] = credential["user_name"]
            if credential["user_display_name"]:
                user["displayName"] = credential["user_display_name"]
            result[4] = user
        if number_of_credentials is not None and number_of_credentials > 1:
            result[5] = number_of_credentials
        return result

    def _ctap_reset(self) -> None:
        self._check_touch()
        remove_state_file(self._state_path)
        self._pending_assertions = None
        self._consume_touch()

    def _check_touch(self) -> None:
        if not self._touched:
            raise TouchRequired("synthetic user presence is required")

    def _consume_touch(self) -> None:
        self._touched = False

    def _generate_key(self) -> tuple[bytes, bytes, bytes]:
        private_key = self._run_openssl(
            ("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"),
            b"",
        )
        if (
            not private_key.startswith(b"-----BEGIN PRIVATE KEY-----\n")
            or not private_key.endswith(b"-----END PRIVATE KEY-----\n")
            or len(private_key) > MAX_PRIVATE_KEY_BYTES
        ):
            raise OpenSSLError("OpenSSL returned an unexpected private key")
        public_der = self._run_openssl(
            ("pkey", "-pubout", "-outform", "DER"), private_key
        )
        if len(public_der) != len(_P256_SPKI_PREFIX) + 64 or not public_der.startswith(
            _P256_SPKI_PREFIX
        ):
            raise OpenSSLError("OpenSSL returned a non-P-256 public key")
        point = public_der[len(_P256_SPKI_PREFIX) :]
        return private_key, point[:32], point[32:]

    def _sign(self, private_key: bytes, data: bytes) -> bytes:
        if not hasattr(os, "memfd_create"):
            raise OpenSSLError("secure in-memory OpenSSL key input is unavailable")
        descriptor = os.memfd_create("uusb-fido-key", flags=getattr(os, "MFD_CLOEXEC", 0))
        try:
            view = memoryview(private_key)
            written = 0
            while written < len(view):
                amount = os.write(descriptor, view[written:])
                if amount <= 0:
                    raise OpenSSLError("could not stage the private key")
                written += amount
            os.lseek(descriptor, 0, os.SEEK_SET)
            signature = self._run_openssl(
                ("dgst", "-sha256", "-sign", f"/proc/self/fd/{descriptor}"),
                data,
                pass_fds=(descriptor,),
            )
        finally:
            os.close(descriptor)
        raw = der_signature_to_raw(signature)
        canonical = raw_signature_to_der(raw)
        if canonical != signature:
            raise OpenSSLError("OpenSSL returned a non-canonical DER signature")
        return signature

    def _run_openssl(
        self,
        arguments: tuple[str, ...],
        input_data: bytes,
        *,
        pass_fds: tuple[int, ...] = (),
    ) -> bytes:
        try:
            completed = subprocess.run(
                (self._openssl, *arguments),
                input=input_data,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=OPENSSL_TIMEOUT_SECONDS,
                pass_fds=pass_fds,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise OpenSSLError("OpenSSL operation failed") from error
        if completed.returncode != 0 or len(completed.stdout) > MAX_OPENSSL_OUTPUT:
            raise OpenSSLError("OpenSSL operation failed")
        return completed.stdout

    def _load_credentials(self) -> list[dict[str, Any]]:
        value = read_json(
            self._state_path,
            {"version": STATE_VERSION, "credentials": []},
        )
        if type(value) is not dict or value.get("version") != STATE_VERSION:
            raise _CredentialStoreError("invalid FIDO state version")
        records = value.get("credentials")
        if type(records) is not list or len(records) > self._max_credentials:
            raise _CredentialStoreError("invalid FIDO credential collection")
        result = [_decode_record(record) for record in records]
        identifiers = [item["credential_id"] for item in result]
        if len(set(identifiers)) != len(identifiers):
            raise _CredentialStoreError("duplicate FIDO credential identifier")
        return result

    def _write_credentials(self, credentials: list[dict[str, Any]]) -> None:
        if len(credentials) > self._max_credentials:
            raise _CredentialStoreError("FIDO credential limit exceeded")
        atomic_write_json(
            self._state_path,
            {
                "version": STATE_VERSION,
                "credentials": [_encode_record(item) for item in credentials],
            },
        )


def _required_map(mapping: dict[Any, Any], key: Any) -> dict[Any, Any]:
    if key not in mapping:
        raise _CtapError(CTAP2_ERR_MISSING_PARAMETER)
    value = mapping[key]
    if type(value) is not dict:
        raise _CtapError(CTAP2_ERR_CBOR_UNEXPECTED_TYPE)
    return value


def _optional_map(mapping: dict[Any, Any], key: Any) -> dict[Any, Any]:
    if key not in mapping:
        return {}
    value = mapping[key]
    if type(value) is not dict:
        raise _CtapError(CTAP2_ERR_CBOR_UNEXPECTED_TYPE)
    return value


def _required_list(mapping: dict[Any, Any], key: Any) -> list[Any]:
    if key not in mapping:
        raise _CtapError(CTAP2_ERR_MISSING_PARAMETER)
    value = mapping[key]
    if type(value) is not list:
        raise _CtapError(CTAP2_ERR_CBOR_UNEXPECTED_TYPE)
    return value


def _required_bytes(
    mapping: dict[Any, Any],
    key: Any,
    *,
    exact_length: int | None = None,
    maximum: int | None = None,
) -> bytes:
    if key not in mapping:
        raise _CtapError(CTAP2_ERR_MISSING_PARAMETER)
    value = mapping[key]
    if type(value) is not bytes:
        raise _CtapError(CTAP2_ERR_CBOR_UNEXPECTED_TYPE)
    if exact_length is not None and len(value) != exact_length:
        raise _CtapError(CTAP1_ERR_INVALID_PARAMETER)
    if maximum is not None and len(value) > maximum:
        raise _CtapError(CTAP2_ERR_LIMIT_EXCEEDED)
    return value


def _required_text(mapping: dict[Any, Any], key: Any, maximum_bytes: int) -> str:
    if key not in mapping:
        raise _CtapError(CTAP2_ERR_MISSING_PARAMETER)
    value = mapping[key]
    if type(value) is not str:
        raise _CtapError(CTAP2_ERR_CBOR_UNEXPECTED_TYPE)
    encoded = value.encode("utf-8", "strict")
    if not encoded or len(encoded) > maximum_bytes or "\x00" in value:
        raise _CtapError(CTAP1_ERR_INVALID_PARAMETER)
    return value


def _optional_text(mapping: dict[Any, Any], key: Any, maximum_bytes: int) -> str:
    if key not in mapping:
        return ""
    value = mapping[key]
    if type(value) is not str:
        raise _CtapError(CTAP2_ERR_CBOR_UNEXPECTED_TYPE)
    if len(value.encode("utf-8", "strict")) > maximum_bytes or "\x00" in value:
        raise _CtapError(CTAP2_ERR_LIMIT_EXCEEDED)
    return value


def _option(options: dict[Any, Any], name: str, default: bool) -> bool:
    if name not in options:
        return default
    value = options[name]
    if type(value) is not bool:
        raise _CtapError(CTAP2_ERR_CBOR_UNEXPECTED_TYPE)
    return value


def _select_es256(parameters: list[Any]) -> None:
    if not parameters or len(parameters) > 16:
        raise _CtapError(CTAP2_ERR_UNSUPPORTED_ALGORITHM)
    for parameter in parameters:
        if type(parameter) is not dict:
            raise _CtapError(CTAP2_ERR_CBOR_UNEXPECTED_TYPE)
        credential_type = parameter.get("type")
        algorithm = parameter.get("alg")
        if type(credential_type) is not str or type(algorithm) is not int:
            raise _CtapError(CTAP2_ERR_CBOR_UNEXPECTED_TYPE)
        if credential_type == "public-key" and algorithm == COSE_ALGORITHM_ES256:
            return
    raise _CtapError(CTAP2_ERR_UNSUPPORTED_ALGORITHM)


def _credential_ids(value: Any) -> list[bytes]:
    if type(value) is not list:
        raise _CtapError(CTAP2_ERR_CBOR_UNEXPECTED_TYPE)
    if len(value) > MAX_ALLOW_LIST:
        raise _CtapError(CTAP2_ERR_LIMIT_EXCEEDED)
    result: list[bytes] = []
    seen: set[bytes] = set()
    for descriptor in value:
        if type(descriptor) is not dict:
            raise _CtapError(CTAP2_ERR_CBOR_UNEXPECTED_TYPE)
        if descriptor.get("type") != "public-key":
            continue
        identifier = descriptor.get("id")
        if type(identifier) is not bytes:
            raise _CtapError(CTAP2_ERR_CBOR_UNEXPECTED_TYPE)
        if not identifier or len(identifier) > MAX_CREDENTIAL_ID_LENGTH:
            raise _CtapError(CTAP1_ERR_INVALID_PARAMETER)
        if identifier not in seen:
            result.append(identifier)
            seen.add(identifier)
    return result


def _encode_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "credential_id": _base64(record["credential_id"]),
        "rp_id": record["rp_id"],
        "rp_name": record["rp_name"],
        "user_id": _base64(record["user_id"]),
        "user_name": record["user_name"],
        "user_display_name": record["user_display_name"],
        "discoverable": record["discoverable"],
        "private_key": _base64(record["private_key"]),
        "x": _base64(record["x"]),
        "y": _base64(record["y"]),
        "signature_counter": record["signature_counter"],
    }


def _decode_record(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        raise _CredentialStoreError("invalid FIDO credential record")
    try:
        credential_id = _unbase64(value["credential_id"], MAX_CREDENTIAL_ID_LENGTH)
        user_id = _unbase64(value["user_id"], MAX_USER_ID_BYTES)
        private_key = _unbase64(value["private_key"], MAX_PRIVATE_KEY_BYTES)
        x = _unbase64(value["x"], 32)
        y = _unbase64(value["y"], 32)
        rp_id = value["rp_id"]
        rp_name = value["rp_name"]
        user_name = value["user_name"]
        user_display_name = value["user_display_name"]
        discoverable = value["discoverable"]
        signature_counter = value["signature_counter"]
    except (KeyError, TypeError, ValueError) as error:
        raise _CredentialStoreError("invalid FIDO credential record") from error
    if len(credential_id) != 32:
        raise _CredentialStoreError("invalid FIDO credential identifier")
    if not user_id or len(x) != 32 or len(y) != 32:
        raise _CredentialStoreError("invalid FIDO credential material")
    if (
        not private_key.startswith(b"-----BEGIN PRIVATE KEY-----\n")
        or not private_key.endswith(b"-----END PRIVATE KEY-----\n")
    ):
        raise _CredentialStoreError("invalid FIDO private key encoding")
    if (
        type(rp_id) is not str
        or not 0 < len(rp_id.encode("utf-8")) <= MAX_RP_ID_BYTES
        or "\x00" in rp_id
    ):
        raise _CredentialStoreError("invalid FIDO relying party")
    for text in (rp_name, user_name, user_display_name):
        if (
            type(text) is not str
            or len(text.encode("utf-8")) > MAX_ENTITY_TEXT_BYTES
            or "\x00" in text
        ):
            raise _CredentialStoreError("invalid FIDO entity text")
    if type(discoverable) is not bool:
        raise _CredentialStoreError("invalid FIDO discoverable flag")
    if type(signature_counter) is not int or not 0 <= signature_counter <= 0xFFFFFFFF:
        raise _CredentialStoreError("invalid FIDO signature counter")
    return {
        "credential_id": credential_id,
        "rp_id": rp_id,
        "rp_name": rp_name,
        "user_id": user_id,
        "user_name": user_name,
        "user_display_name": user_display_name,
        "discoverable": discoverable,
        "private_key": private_key,
        "x": x,
        "y": y,
        "signature_counter": signature_counter,
    }


def _base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unbase64(value: Any, maximum: int) -> bytes:
    if type(value) is not str or len(value) > ((maximum + 2) // 3) * 4:
        raise _CredentialStoreError("invalid base64 field")
    try:
        result = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise _CredentialStoreError("invalid base64 field") from error
    if len(result) > maximum:
        raise _CredentialStoreError("decoded field exceeds limit")
    return result


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _parse_credential_identifier(value: bytes | str) -> bytes:
    if type(value) is bytes:
        if not value or len(value) > MAX_CREDENTIAL_ID_LENGTH:
            raise ValueError("credential identifier is out of range")
        return value
    if type(value) is not str or not value or len(value) > 1_400:
        raise ValueError("credential identifier is invalid")
    padding = "=" * (-len(value) % 4)
    try:
        result = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error) as error:
        raise ValueError("credential identifier is invalid") from error
    if not result or len(result) > MAX_CREDENTIAL_ID_LENGTH:
        raise ValueError("credential identifier is out of range")
    return result


__all__ = [
    "AAGUID",
    "AUTH_DATA_FLAG_AT",
    "AUTH_DATA_FLAG_UP",
    "AUTH_DATA_FLAG_UV",
    "COSE_ALGORITHM_ES256",
    "COSE_CURVE_P256",
    "COSE_KEY_TYPE_EC2",
    "CTAPHID_BROADCAST_CID",
    "CTAPHID_CANCEL",
    "CTAPHID_CBOR",
    "CTAPHID_CONTINUATION_DATA_SIZE",
    "CTAPHID_ERROR",
    "CTAPHID_ERR_CHANNEL_BUSY",
    "CTAPHID_ERR_INVALID_CHANNEL",
    "CTAPHID_ERR_INVALID_CMD",
    "CTAPHID_ERR_INVALID_LEN",
    "CTAPHID_ERR_INVALID_PAR",
    "CTAPHID_ERR_INVALID_SEQ",
    "CTAPHID_ERR_MSG_TIMEOUT",
    "CTAPHID_ERR_OTHER",
    "CTAPHID_INIT",
    "CTAPHID_INITIAL_DATA_SIZE",
    "CTAPHID_KEEPALIVE",
    "CTAPHID_KEEPALIVE_PROCESSING",
    "CTAPHID_KEEPALIVE_UP_NEEDED",
    "CTAPHID_MAX_MESSAGE_SIZE",
    "CTAPHID_PING",
    "CTAPHID_REPORT_SIZE",
    "CTAPHID_WINK",
    "CTAP2_ERR_CREDENTIAL_EXCLUDED",
    "CTAP2_ERR_INVALID_CBOR",
    "CTAP2_ERR_KEEPALIVE_CANCEL",
    "CTAP2_ERR_LIMIT_EXCEEDED",
    "CTAP2_ERR_NO_CREDENTIALS",
    "CTAP2_ERR_NOT_ALLOWED",
    "CTAP2_ERR_OTHER",
    "CTAP2_GET_ASSERTION",
    "CTAP2_GET_INFO",
    "CTAP2_GET_NEXT_ASSERTION",
    "CTAP2_MAKE_CREDENTIAL",
    "CTAP2_OK",
    "CTAP2_RESET",
    "CtaphidFramer",
    "FidoBackend",
    "HidFramingError",
    "HidMessage",
    "OpenSSLError",
    "TouchRequired",
    "der_signature_to_raw",
    "frame_hid_message",
    "p256_public_key_der",
    "raw_signature_to_der",
]
