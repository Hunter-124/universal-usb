"""Strict persistent OpenOCD 0.12 Tcl-pipe transport."""

from __future__ import annotations

import os
from pathlib import Path
import re
import select
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from typing import BinaryIO

TCL_FRAME_END = b"\x1a"
_WORD_TOKEN = re.compile(r"(?:0x[0-9A-Fa-f]+|[0-9]+)\Z")


class OpenOCDError(RuntimeError):
    """Base error for OpenOCD session failures."""


class OpenOCDUnavailable(OpenOCDError):
    """OpenOCD could not be started or exited unexpectedly."""


class OpenOCDTimeout(OpenOCDError):
    """An OpenOCD operation exceeded its fixed deadline."""


class OpenOCDMalformedResponse(OpenOCDError):
    """OpenOCD returned data outside the strict Tcl RPC grammar."""


class OpenOCDFlashError(OpenOCDError):
    """One-shot program/verify/reset failed."""


class OpenOCDTransport:
    """Own exactly one lazily started Tcl-pipe child at a time.

    Normal mailbox traffic never issues halt/resume commands. A failed RPC
    invalidates and reaps that exact child; the failed operation is never
    replayed. A later operation starts a new session.
    """

    def __init__(
        self,
        log_path: str | os.PathLike[str],
        *,
        executable: str = "openocd",
        rpc_timeout: float = 1.0,
        flash_timeout: float = 30.0,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        run_factory: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if rpc_timeout <= 0 or flash_timeout <= 0:
            raise ValueError("OpenOCD deadlines must be positive")
        self.log_path = os.fspath(Path(log_path).absolute())
        self.executable = executable
        self.rpc_timeout = rpc_timeout
        self.flash_timeout = flash_timeout
        self._popen = popen_factory
        self._run = run_factory
        self._monotonic = monotonic
        self._child: subprocess.Popen[bytes] | None = None
        self._lock = threading.RLock()

    @property
    def persistent_argv(self) -> tuple[str, ...]:
        return (
            self.executable,
            "-l", self.log_path,
            "-f", "interface/stlink.cfg",
            "-f", "target/stm32f1x.cfg",
            "-c", "gdb_port disabled",
            "-c", "telnet_port disabled",
            "-c", "tcl_port pipe",
            "-c", "init",
        )

    def start(self) -> None:
        with self._lock:
            if self._child is not None and self._child.poll() is None:
                return
            if self._child is not None:
                self._reap_child(self._child)
                self._child = None
            try:
                child = self._popen(
                    self.persistent_argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                )
            except (OSError, ValueError) as error:
                raise OpenOCDUnavailable(f"cannot start OpenOCD: {error}") from error
            if child.stdin is None or child.stdout is None:
                self._reap_child(child)
                raise OpenOCDUnavailable("OpenOCD Tcl pipes were not created")
            self._child = child

    def close(self) -> None:
        with self._lock:
            child, self._child = self._child, None
            if child is not None:
                self._reap_child(child)

    def __enter__(self) -> "OpenOCDTransport":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def read_words(self, address: int, count: int, *, timeout: float | None = None) -> tuple[int, ...]:
        self._validate_word_range(address, count)
        response = self._rpc(
            f"read_memory 0x{address:08x} 32 {count}", timeout=timeout
        )
        try:
            return parse_read_memory_response(response, count)
        except OpenOCDMalformedResponse:
            with self._lock:
                self._invalidate_current()
            raise

    def write_words(self, address: int, words: Iterable[int], *, timeout: float | None = None) -> None:
        values = tuple(words)
        self._validate_word_range(address, len(values))
        for value in values:
            if not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
                raise ValueError("OpenOCD write value is outside uint32 range")
        rendered = " ".join(f"0x{value:08x}" for value in values)
        response = self._rpc(
            f"write_memory 0x{address:08x} 32 {{{rendered}}}", timeout=timeout
        )
        if response.strip(b" \t\r\n"):
            with self._lock:
                self._invalidate_current()
            raise OpenOCDMalformedResponse("write_memory returned unexpected data")

    def read_bytes(self, address: int, length: int, *, timeout: float | None = None) -> bytes:
        if length <= 0 or length % 4:
            raise ValueError("mailbox byte reads must be a positive multiple of four")
        words = self.read_words(address, length // 4, timeout=timeout)
        return b"".join(value.to_bytes(4, "little") for value in words)

    def write_bytes(self, address: int, data: bytes | bytearray | memoryview,
                    *, timeout: float | None = None) -> None:
        raw = bytes(data)
        if not raw or len(raw) % 4:
            raise ValueError("mailbox byte writes must be a positive multiple of four")
        words = (int.from_bytes(raw[index:index + 4], "little")
                 for index in range(0, len(raw), 4))
        self.write_words(address, words, timeout=timeout)

    def target_state(self, *, timeout: float | None = None) -> str:
        """Return the exact OpenOCD target state without changing core execution."""
        response = self._rpc("stm32f1x.cpu curstate", timeout=timeout)
        try:
            state = response.decode("ascii", "strict").strip()
        except UnicodeDecodeError as error:
            with self._lock:
                self._invalidate_current()
            raise OpenOCDMalformedResponse("target state is not ASCII") from error
        if state not in {"running", "halted", "reset", "debug-running", "unknown"}:
            with self._lock:
                self._invalidate_current()
            raise OpenOCDMalformedResponse(f"unexpected target state {state!r}")
        return state

    def flash(self, elf_path: str | os.PathLike[str]) -> None:
        try:
            absolute = Path(elf_path).expanduser().resolve(strict=True)
        except OSError as error:
            raise OpenOCDFlashError(f"firmware ELF is unavailable: {error}") from error
        if not absolute.is_file():
            raise OpenOCDFlashError("firmware ELF is not a regular file")
        with self._lock:
            child, self._child = self._child, None
            if child is not None:
                self._reap_child(child)
            argv = (
                self.executable,
                "-l", self.log_path,
                "-f", "interface/stlink.cfg",
                "-f", "target/stm32f1x.cfg",
                "-c", f"program {absolute} verify reset",
                "-c", "shutdown",
            )
            try:
                completed = self._run(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=self.flash_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise OpenOCDFlashError("OpenOCD flash deadline expired") from error
            except OSError as error:
                raise OpenOCDFlashError(f"cannot run OpenOCD flasher: {error}") from error
            if completed.returncode != 0:
                raise OpenOCDFlashError(
                    f"OpenOCD program/verify/reset exited {completed.returncode}"
                )

    def _rpc(self, command: str, *, timeout: float | None) -> bytes:
        deadline_seconds = self.rpc_timeout if timeout is None else timeout
        if deadline_seconds <= 0:
            raise ValueError("OpenOCD RPC deadline must be positive")
        with self._lock:
            self.start()
            child = self._child
            assert child is not None and child.stdin is not None and child.stdout is not None
            try:
                self._reject_pending_output(child.stdout)
                encoded = command.encode("ascii", "strict") + TCL_FRAME_END
                child.stdin.write(encoded)
                child.stdin.flush()
                return self._read_frame(child.stdout, self._monotonic() + deadline_seconds)
            except OpenOCDError:
                self._invalidate_current()
                raise
            except (BrokenPipeError, OSError, ValueError) as error:
                self._invalidate_current()
                raise OpenOCDUnavailable(f"OpenOCD Tcl pipe failed: {error}") from error

    def _reject_pending_output(self, stdout: BinaryIO) -> None:
        """Reject bytes present before a command; they belong to no current RPC."""
        descriptor = stdout.fileno()
        try:
            readable, _, _ = select.select((descriptor,), (), (), 0.0)
        except (OSError, ValueError) as error:
            raise OpenOCDUnavailable(
                f"cannot inspect OpenOCD Tcl pipe: {error}"
            ) from error
        if readable:
            stale = os.read(descriptor, 4096)
            if not stale:
                raise OpenOCDUnavailable("OpenOCD Tcl pipe reached EOF")
            raise OpenOCDMalformedResponse(
                "stale OpenOCD output preceded the current Tcl request"
            )

    def _read_frame(self, stdout: BinaryIO, deadline: float) -> bytes:
        buffer = bytearray()
        descriptor = stdout.fileno()
        while True:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise OpenOCDTimeout("OpenOCD Tcl response deadline expired")
            try:
                readable, _, _ = select.select((descriptor,), (), (), remaining)
            except (OSError, ValueError) as error:
                raise OpenOCDUnavailable(f"cannot wait for OpenOCD Tcl response: {error}") from error
            if not readable:
                raise OpenOCDTimeout("OpenOCD Tcl response deadline expired")
            chunk = os.read(descriptor, 4096)
            if not chunk:
                raise OpenOCDUnavailable("OpenOCD Tcl pipe reached EOF")
            marker = chunk.find(TCL_FRAME_END)
            if marker < 0:
                buffer.extend(chunk)
                if len(buffer) > 1024 * 1024:
                    raise OpenOCDMalformedResponse("OpenOCD Tcl response is unbounded")
                continue
            buffer.extend(chunk[:marker])
            if chunk[marker + 1:]:
                raise OpenOCDMalformedResponse("extra bytes followed the Tcl response frame")
            return bytes(buffer)

    def _invalidate_current(self) -> None:
        child, self._child = self._child, None
        if child is not None:
            self._reap_child(child)

    @staticmethod
    def _reap_child(child: subprocess.Popen[bytes]) -> None:
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        if child.poll() is not None:
            child.wait()
            return
        child.terminate()
        try:
            child.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()

    @staticmethod
    def _validate_word_range(address: int, count: int) -> None:
        if not isinstance(address, int) or address < 0 or address > 0xFFFFFFFF:
            raise ValueError("OpenOCD address is outside uint32 range")
        if address % 4:
            raise ValueError("OpenOCD word address is not aligned")
        if not isinstance(count, int) or count <= 0:
            raise ValueError("OpenOCD word count must be positive")
        if count > 0x40000000 or address + count * 4 > 0x100000000:
            raise ValueError("OpenOCD word range exceeds address space")


def parse_read_memory_response(response: bytes, expected_count: int) -> tuple[int, ...]:
    """Parse exactly one canonical ASCII Tcl list of uint32 values."""
    if expected_count <= 0:
        raise ValueError("expected word count must be positive")
    try:
        text = response.decode("ascii", "strict")
    except UnicodeDecodeError as error:
        raise OpenOCDMalformedResponse("read_memory response is not ASCII") from error
    stripped = text.strip(" \t\r\n")
    if not stripped:
        raise OpenOCDMalformedResponse("read_memory response is empty")
    tokens = re.split(r"[ \t\r\n]+", stripped)
    if len(tokens) != expected_count:
        raise OpenOCDMalformedResponse(
            f"read_memory returned {len(tokens)} words, expected {expected_count}"
        )
    values: list[int] = []
    for token in tokens:
        if _WORD_TOKEN.fullmatch(token) is None:
            raise OpenOCDMalformedResponse("read_memory response contains a non-integer")
        value = int(token, 16 if token.startswith("0x") else 10)
        if value > 0xFFFFFFFF:
            raise OpenOCDMalformedResponse("read_memory word exceeds uint32 range")
        values.append(value)
    return tuple(values)
