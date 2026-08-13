"""Thread-safe owner for fixed-size raw disk images."""

from __future__ import annotations

import errno
import fcntl
import math
import os
import stat
import threading
import time
from enum import Enum, auto
from pathlib import Path
from types import TracebackType
from typing import Final

BLOCK_SIZE: Final = 512
UINT32_MAX: Final = (1 << 32) - 1
MAX_DETACH_DEADLINE_SECONDS: Final = 30.0


class MediaState(Enum):
    """Externally observable raw-image lifecycle state."""

    CLOSED = auto()
    ATTACHED = auto()
    DETACHING = auto()


class MediaError(RuntimeError):
    """Base class for raw-image failures."""


class MediaStateError(MediaError):
    """The requested operation is invalid in the current lifecycle state."""


class MediaValidationError(MediaError):
    """The supplied image is not a valid fixed-block raw image."""


class MediaLockError(MediaError):
    """The image is already locked by another owner."""


class MediaBoundsError(MediaError):
    """A block address or block payload is invalid."""


class MediaReadOnlyError(MediaError):
    """A write was attempted against a read-only attachment."""


class MediaBusyError(MediaError):
    """The sole operation slot is already occupied."""


class MediaRemovalPreventedError(MediaError):
    """The attached consumer has prevented media removal."""


class MediaDetachTimeout(MediaError):
    """An in-flight operation did not finish before the detach deadline."""


class MediaIOError(MediaError):
    """An operating-system image operation failed."""


class RawImage:
    """Own one locked raw image and expose exact 512-byte block operations.

    At most one block or synchronization operation is in flight. Detach first
    enters ``DETACHING``, rejecting new work, and waits only for the caller's
    finite, bounded deadline. Attach publishes no image metadata until open,
    validation, and advisory locking have all succeeded.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._state = MediaState.CLOSED
        self._fd = -1
        self._path: str | None = None
        self._block_count = 0
        self._read_write = False
        self._removal_prevented = False
        self._operation_active = False
        self._epoch = 0

    @property
    def state(self) -> MediaState:
        with self._condition:
            return self._state

    @property
    def epoch(self) -> int:
        with self._condition:
            return self._epoch

    @property
    def path(self) -> str | None:
        with self._condition:
            return self._path

    @property
    def block_count(self) -> int:
        with self._condition:
            return self._block_count

    @property
    def read_write(self) -> bool:
        with self._condition:
            return self._read_write

    @property
    def removal_prevented(self) -> bool:
        with self._condition:
            return self._removal_prevented

    def attach(
        self,
        path: str | os.PathLike[str],
        read_write: bool = False,
    ) -> None:
        """Validate, exclusively lock, and atomically publish an image."""
        if not isinstance(read_write, bool):
            raise TypeError("read_write must be a bool")
        image_path = os.fspath(Path(path))

        with self._condition:
            if self._state is not MediaState.CLOSED:
                raise MediaStateError("an image is already attached")

            flags = os.O_RDWR if read_write else os.O_RDONLY
            flags |= getattr(os, "O_CLOEXEC", 0)
            fd = -1
            try:
                fd = os.open(image_path, flags)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    if error.errno in (errno.EACCES, errno.EAGAIN):
                        raise MediaLockError("image is already locked") from error
                    raise MediaIOError(f"cannot lock image: {error}") from error
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise MediaValidationError("image must be a regular file")
                if info.st_size <= 0:
                    raise MediaValidationError("image must not be empty")
                if info.st_size % BLOCK_SIZE:
                    raise MediaValidationError(
                        f"image size must be divisible by {BLOCK_SIZE}"
                    )
                block_count = info.st_size // BLOCK_SIZE
                if block_count > UINT32_MAX:
                    raise MediaValidationError("image exceeds the uint32 block limit")
            except MediaError:
                if fd >= 0:
                    os.close(fd)
                raise
            except OSError as error:
                if fd >= 0:
                    os.close(fd)
                raise MediaIOError(f"cannot attach image: {error}") from error

            self._fd = fd
            self._path = image_path
            self._block_count = block_count
            self._read_write = read_write
            self._removal_prevented = False
            self._operation_active = False
            self._state = MediaState.ATTACHED
            self._epoch += 1

    def read_block(self, lba: int) -> bytes:
        """Read exactly one block, or raise without fabricating data."""
        fd, block_count, _ = self._begin_operation()
        try:
            offset = self._checked_offset(lba, block_count)
            try:
                first = os.pread(fd, BLOCK_SIZE, offset)
            except OSError as error:
                raise MediaIOError(f"block read failed: {error}") from error
            if len(first) == BLOCK_SIZE:
                return first
            if not first:
                raise MediaIOError("block read ended before a complete block")

            result = bytearray(BLOCK_SIZE)
            result[: len(first)] = first
            completed = len(first)
            while completed < BLOCK_SIZE:
                try:
                    chunk = os.pread(fd, BLOCK_SIZE - completed, offset + completed)
                except OSError as error:
                    raise MediaIOError(f"block read failed: {error}") from error
                if not chunk:
                    raise MediaIOError("block read ended before a complete block")
                result[completed : completed + len(chunk)] = chunk
                completed += len(chunk)
            return bytes(result)
        finally:
            self._end_operation()

    def write_block(
        self,
        lba: int,
        data: bytes | bytearray | memoryview,
    ) -> None:
        """Write exactly one block and fsync it before acknowledging success."""
        fd, block_count, read_write = self._begin_operation()
        try:
            if not read_write:
                raise MediaReadOnlyError("image is attached read-only")
            offset = self._checked_offset(lba, block_count)
            try:
                view = memoryview(data)
                if view.ndim != 1 or not view.c_contiguous:
                    raise TypeError
                view = view.cast("B")
            except (TypeError, ValueError) as error:
                raise MediaBoundsError("block data must be a contiguous byte buffer") from error
            if len(view) != BLOCK_SIZE:
                raise MediaBoundsError(f"block data must be exactly {BLOCK_SIZE} bytes")

            completed = 0
            while completed < BLOCK_SIZE:
                try:
                    written = os.pwrite(fd, view[completed:], offset + completed)
                except OSError as error:
                    raise MediaIOError(f"block write failed: {error}") from error
                if written <= 0:
                    raise MediaIOError("block write made no progress")
                completed += written
            self._fsync(fd, "block write synchronization failed")
        finally:
            self._end_operation()

    def synchronize(self) -> None:
        """Durably synchronize the current image."""
        fd, _, _ = self._begin_operation()
        try:
            self._fsync(fd, "image synchronization failed")
        finally:
            self._end_operation()

    def prevent_removal(self, prevent: bool) -> None:
        """Set or clear the consumer's PREVENT/ALLOW removal state."""
        if not isinstance(prevent, bool):
            raise TypeError("prevent must be a bool")
        with self._condition:
            self._require_attached()
            self._removal_prevented = prevent

    def detach(self, deadline_seconds: float) -> None:
        """Synchronize and detach within a finite, bounded wait deadline."""
        deadline = self._validated_deadline(deadline_seconds)
        self._detach(deadline, honor_prevention=True)

    def close(self) -> None:
        """Synchronize and close, ignoring PREVENT for owner cleanup."""
        with self._condition:
            if self._state is MediaState.CLOSED:
                return
        self._detach(MAX_DETACH_DEADLINE_SECONDS, honor_prevention=False)

    def __enter__(self) -> RawImage:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()

    def _begin_operation(self) -> tuple[int, int, bool]:
        with self._condition:
            self._require_attached()
            if self._operation_active:
                raise MediaBusyError("another media operation is already active")
            self._operation_active = True
            return self._fd, self._block_count, self._read_write

    def _end_operation(self) -> None:
        with self._condition:
            self._operation_active = False
            self._condition.notify_all()

    def _require_attached(self) -> None:
        if self._state is MediaState.DETACHING:
            raise MediaStateError("image is detaching")
        if self._state is not MediaState.ATTACHED:
            raise MediaStateError("no image is attached")

    @staticmethod
    def _checked_offset(lba: int, block_count: int) -> int:
        if isinstance(lba, bool) or not isinstance(lba, int):
            raise MediaBoundsError("block address must be an integer")
        if lba < 0 or lba >= block_count:
            raise MediaBoundsError("block address is outside the image")
        return lba * BLOCK_SIZE

    @staticmethod
    def _validated_deadline(deadline_seconds: float) -> float:
        if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float)):
            raise ValueError("detach deadline must be a number")
        deadline = float(deadline_seconds)
        if not math.isfinite(deadline) or not 0 <= deadline <= MAX_DETACH_DEADLINE_SECONDS:
            raise ValueError(
                f"detach deadline must be between 0 and {MAX_DETACH_DEADLINE_SECONDS} seconds"
            )
        return deadline

    def _detach(self, deadline_seconds: float, *, honor_prevention: bool) -> None:
        expires_at = time.monotonic() + deadline_seconds
        with self._condition:
            self._require_attached()
            if honor_prevention and self._removal_prevented:
                raise MediaRemovalPreventedError("media removal is prevented")
            self._state = MediaState.DETACHING
            while self._operation_active:
                remaining = expires_at - time.monotonic()
                if remaining <= 0:
                    self._state = MediaState.ATTACHED
                    self._condition.notify_all()
                    raise MediaDetachTimeout("media operation exceeded detach deadline")
                self._condition.wait(remaining)
            fd = self._fd

        try:
            self._fsync(fd, "detach synchronization failed")
        except MediaIOError:
            with self._condition:
                if self._state is MediaState.DETACHING and self._fd == fd:
                    self._state = MediaState.ATTACHED
                    self._condition.notify_all()
            raise

        close_error: OSError | None = None
        try:
            os.close(fd)
        except OSError as error:
            close_error = error
        finally:
            with self._condition:
                self._fd = -1
                self._path = None
                self._block_count = 0
                self._read_write = False
                self._removal_prevented = False
                self._state = MediaState.CLOSED
                self._epoch += 1
                self._condition.notify_all()
        if close_error is not None:
            raise MediaIOError(f"image close failed: {close_error}") from close_error

    @staticmethod
    def _fsync(fd: int, message: str) -> None:
        try:
            os.fsync(fd)
        except OSError as error:
            raise MediaIOError(f"{message}: {error}") from error
