"""Secure, durable JSON state storage for the controller."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any


_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600
_OPEN_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_OPEN_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_OPEN_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_OPEN_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class StateError(RuntimeError):
    """Base failure for controller state storage."""


class StateSecurityError(StateError):
    """A state path is unsafe or has the wrong file type."""


class StateFormatError(StateError):
    """A state file does not contain valid JSON."""


def secure_state_dir(path: str | os.PathLike[str] | None = None) -> Path:
    """Create or verify the private controller state directory.

    The default follows ``XDG_STATE_HOME`` and otherwise uses
    ``~/.local/state/universal-usb``. The returned directory itself is never allowed to
    be a symbolic link, and its mode is corrected to exactly 0700.
    """
    if path is None:
        xdg_state_home = os.environ.get("XDG_STATE_HOME")
        base = (
            Path(xdg_state_home).expanduser()
            if xdg_state_home
            else Path.home() / ".local" / "state"
        )
        directory = base / "universal-usb"
    else:
        directory = Path(path).expanduser()

    try:
        directory.mkdir(mode=_DIRECTORY_MODE, parents=True, exist_ok=True)
    except OSError as error:
        raise StateSecurityError("state root is not a usable directory") from error

    descriptor = _open_directory(directory)
    try:
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != _DIRECTORY_MODE:
            os.fchmod(descriptor, _DIRECTORY_MODE)
            os.fsync(descriptor)
    except OSError as error:
        raise StateSecurityError("could not enforce state root permissions") from error
    finally:
        os.close(descriptor)
    return directory


def read_json(path: str | os.PathLike[str], default: Any) -> Any:
    """Read one private JSON state file, returning *default* if it is absent."""
    state_path = Path(path)
    if not state_path.name:
        raise ValueError("state path must name a file")
    secure_state_dir(state_path.parent)
    descriptor = _open_regular(state_path)
    if descriptor is None:
        return default
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            try:
                return json.load(stream)
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise StateFormatError("state file does not contain valid JSON") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def atomic_write_json(path: str | os.PathLike[str], value: Any) -> None:
    """Atomically and durably replace one private JSON state file."""
    state_path = Path(path)
    if not state_path.name:
        raise ValueError("state path must name a file")

    # Serialize before touching the existing state so unsupported values cannot
    # leave behind a temporary file or alter a valid file.
    encoded = (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )

    directory = secure_state_dir(state_path.parent)
    existing = _open_regular(state_path)
    if existing is not None:
        os.close(existing)

    directory_descriptor = _open_directory(directory)
    temporary_descriptor = -1
    temporary_name: str | None = None
    replaced = False
    try:
        temporary_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{state_path.name}.", dir=directory
        )
        os.fchmod(temporary_descriptor, _FILE_MODE)
        view = memoryview(encoded)
        while view:
            written = os.write(temporary_descriptor, view)
            if written <= 0:
                raise StateError("state write made no progress")
            view = view[written:]
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = -1

        os.replace(temporary_name, state_path)
        replaced = True
        os.fsync(directory_descriptor)
    finally:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        if temporary_name is not None and not replaced:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        os.close(directory_descriptor)


def remove_state_file(path: str | os.PathLike[str]) -> None:
    """Durably remove a private regular state file if it exists."""
    state_path = Path(path)
    if not state_path.name:
        raise ValueError("state path must name a file")
    directory = secure_state_dir(state_path.parent)
    descriptor = _open_regular(state_path)
    if descriptor is None:
        return
    os.close(descriptor)

    directory_descriptor = _open_directory(directory)
    try:
        try:
            os.unlink(state_path)
        except FileNotFoundError:
            return
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | _OPEN_DIRECTORY | _OPEN_CLOEXEC | _OPEN_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise StateSecurityError("state root must not be a symbolic link") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise StateSecurityError("state root is not a directory")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_regular(path: Path) -> int | None:
    flags = os.O_RDONLY | _OPEN_CLOEXEC | _OPEN_NOFOLLOW | _OPEN_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StateSecurityError("state file must not be a symbolic link") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise StateSecurityError("state file is not a regular file")
        if stat.S_IMODE(metadata.st_mode) != _FILE_MODE:
            os.fchmod(descriptor, _FILE_MODE)
            os.fsync(descriptor)
        return descriptor
    except Exception:
        os.close(descriptor)
        raise
