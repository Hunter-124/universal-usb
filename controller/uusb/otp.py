"""Durable HOTP/TOTP credential storage and report generation."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import math
import os
from pathlib import Path
import threading
import time
from typing import Any

from .hid import text_reports
from .state import StateFormatError, atomic_write_json, read_json, secure_state_dir


OTP_DIGITS_MIN = 6
OTP_DIGITS_MAX = 8
TOTP_PERIOD_MIN = 1
TOTP_PERIOD_MAX = 86400
UINT64_MAX = (1 << 64) - 1
_STATE_VERSION = 1


class OtpError(RuntimeError):
    """Base failure for OTP credential operations."""


class OtpValidationError(OtpError, ValueError):
    """An OTP credential or operation parameter is invalid."""


class OtpNotFoundError(OtpError, KeyError):
    """A requested OTP credential does not exist."""


class OtpConflictError(OtpError):
    """An OTP credential already exists or its HOTP counter changed."""


class OtpStateError(OtpError):
    """Persisted OTP state is malformed or unsupported."""


class OtpStore:
    """A named, explicitly selected collection of HOTP/TOTP credentials."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        secure_state_dir(self.path.parent)
        self._lock = threading.RLock()

    def provision(
        self,
        name: str,
        kind: str,
        secret: str,
        *,
        digits: int,
        period: int | None = None,
        counter: int | None = None,
    ) -> None:
        """Persist one new credential without selecting it implicitly."""
        credential_name = _validate_name(name)
        credential_kind = _validate_kind(kind)
        canonical_secret, _ = _decode_secret(secret)
        _validate_digits(digits)

        if credential_kind == "totp":
            if period is None:
                raise OtpValidationError("TOTP period is required")
            _validate_period(period)
            if counter is not None:
                raise OtpValidationError("TOTP credentials do not have a counter")
            credential: dict[str, Any] = {
                "kind": "totp",
                "secret": canonical_secret,
                "digits": digits,
                "period": period,
            }
        else:
            if counter is None:
                raise OtpValidationError("HOTP counter is required")
            _validate_counter(counter)
            if period is not None:
                raise OtpValidationError("HOTP credentials do not have a period")
            credential = {
                "kind": "hotp",
                "secret": canonical_secret,
                "digits": digits,
                "counter": counter,
            }

        with self._lock:
            state = self._load()
            credentials = state["credentials"]
            if credential_name in credentials:
                raise OtpConflictError("an OTP credential with that name already exists")
            credentials[credential_name] = credential
            atomic_write_json(self.path, state)

    def remove(self, name: str) -> None:
        """Remove one credential and clear selection when it was selected."""
        credential_name = _validate_name(name)
        with self._lock:
            state = self._load()
            credentials = state["credentials"]
            if credential_name not in credentials:
                raise OtpNotFoundError(credential_name)
            del credentials[credential_name]
            if state["selected"] == credential_name:
                state["selected"] = None
            atomic_write_json(self.path, state)

    def select(self, name: str) -> None:
        """Select an existing credential for subsequent generation."""
        credential_name = _validate_name(name)
        with self._lock:
            state = self._load()
            if credential_name not in state["credentials"]:
                raise OtpNotFoundError(credential_name)
            state["selected"] = credential_name
            atomic_write_json(self.path, state)

    def status(self) -> dict[str, Any]:
        """Return public metadata only; secret material is never returned."""
        with self._lock:
            state = self._load()
        public_credentials: list[dict[str, Any]] = []
        for name in sorted(state["credentials"]):
            credential = state["credentials"][name]
            public: dict[str, Any] = {
                "name": name,
                "kind": credential["kind"],
                "digits": credential["digits"],
            }
            if credential["kind"] == "totp":
                public["period"] = credential["period"]
            else:
                public["counter"] = credential["counter"]
            public_credentials.append(public)
        return {"selected": state["selected"], "credentials": public_credentials}

    def generate_selected(self, now: float | int | None = None) -> str:
        """Generate the selected code without changing persistent state."""
        with self._lock:
            state = self._load()
            selected = state["selected"]
            if selected is None:
                raise OtpNotFoundError("no OTP credential is selected")
            credential = state["credentials"][selected]

        if credential["kind"] == "hotp":
            moving_factor = credential["counter"]
        else:
            instant = time.time() if now is None else now
            if (
                isinstance(instant, bool)
                or not isinstance(instant, (int, float))
                or not math.isfinite(instant)
                or instant < 0
            ):
                raise OtpValidationError("TOTP time must be a finite non-negative number")
            moving_factor = int(instant) // credential["period"]
            if moving_factor > UINT64_MAX:
                raise OtpValidationError("TOTP moving factor exceeds uint64")

        _, secret_bytes = _decode_secret(credential["secret"])
        return _hotp(secret_bytes, moving_factor, credential["digits"])

    def commit_hotp(self, name: str, expected_counter: int) -> int:
        """Durably advance HOTP once, but only from the expected counter."""
        credential_name = _validate_name(name)
        _validate_counter(expected_counter)
        with self._lock:
            state = self._load()
            try:
                credential = state["credentials"][credential_name]
            except KeyError as error:
                raise OtpNotFoundError(credential_name) from error
            if credential["kind"] != "hotp":
                raise OtpValidationError("only HOTP credentials have a counter")
            if credential["counter"] != expected_counter:
                raise OtpConflictError("HOTP counter no longer matches the expected value")
            if expected_counter == UINT64_MAX:
                raise OtpValidationError("HOTP counter cannot advance past uint64")
            new_counter = expected_counter + 1
            credential["counter"] = new_counter
            # No in-memory counter is published: success is reported only after
            # atomic_write_json has fsynced the replacement and its directory.
            atomic_write_json(self.path, state)
            return new_counter

    def _load(self) -> dict[str, Any]:
        try:
            value = read_json(self.path, _empty_state())
        except StateFormatError as error:
            raise OtpStateError("OTP state is not valid JSON") from error
        return _validate_state(value)


def keyboard_reports(text: str) -> tuple[bytes, ...]:
    """Encode an OTP digit string as alternating press and release reports."""
    if not isinstance(text, str):
        raise TypeError("OTP text must be a string")
    if any(character not in "0123456789" for character in text):
        raise OtpValidationError("OTP keyboard text must contain only digits")
    return text_reports(text)


def _hotp(secret: bytes, counter: int, digits: int) -> str:
    digest = hmac.new(secret, counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return f"{binary % (10 ** digits):0{digits}d}"


def _decode_secret(secret: str) -> tuple[str, bytes]:
    if not isinstance(secret, str):
        raise OtpValidationError("OTP secret must be a base32 string")
    if not secret or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for character in secret):
        raise OtpValidationError("OTP secret must use canonical unpadded base32")
    padding = "=" * ((-len(secret)) % 8)
    try:
        decoded = base64.b32decode(secret + padding, casefold=False)
    except (binascii.Error, ValueError) as error:
        raise OtpValidationError("OTP secret is not valid base32") from error
    if not decoded or base64.b32encode(decoded).decode("ascii").rstrip("=") != secret:
        raise OtpValidationError("OTP secret is not canonical base32")
    return secret, decoded


def _empty_state() -> dict[str, Any]:
    return {"version": _STATE_VERSION, "selected": None, "credentials": {}}


def _validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"version", "selected", "credentials"}:
        raise OtpStateError("OTP state has an invalid top-level structure")
    if value["version"] != _STATE_VERSION or isinstance(value["version"], bool):
        raise OtpStateError("OTP state version is unsupported")
    credentials = value["credentials"]
    if not isinstance(credentials, dict):
        raise OtpStateError("OTP credentials must be an object")

    for name, credential in credentials.items():
        try:
            _validate_name(name)
            if not isinstance(credential, dict):
                raise OtpValidationError("credential must be an object")
            kind = _validate_kind(credential.get("kind"))
            _decode_secret(credential.get("secret"))
            _validate_digits(credential.get("digits"))
            if kind == "totp":
                if set(credential) != {"kind", "secret", "digits", "period"}:
                    raise OtpValidationError("TOTP fields are invalid")
                _validate_period(credential["period"])
            else:
                if set(credential) != {"kind", "secret", "digits", "counter"}:
                    raise OtpValidationError("HOTP fields are invalid")
                _validate_counter(credential["counter"])
        except OtpValidationError as error:
            raise OtpStateError(f"OTP credential {name!r} is malformed") from error

    selected = value["selected"]
    if selected is not None and (not isinstance(selected, str) or selected not in credentials):
        raise OtpStateError("selected OTP credential does not exist")
    return value


def _validate_name(name: Any) -> str:
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 128
        or name != name.strip()
        or not name.isprintable()
    ):
        raise OtpValidationError("OTP credential name must be 1..128 printable characters")
    return name


def _validate_kind(kind: Any) -> str:
    if not isinstance(kind, str) or kind not in {"hotp", "totp"}:
        raise OtpValidationError("OTP kind must be 'hotp' or 'totp'")
    return kind


def _validate_digits(digits: Any) -> None:
    if (
        isinstance(digits, bool)
        or not isinstance(digits, int)
        or not OTP_DIGITS_MIN <= digits <= OTP_DIGITS_MAX
    ):
        raise OtpValidationError("OTP digits must be an integer from 6 through 8")


def _validate_period(period: Any) -> None:
    if (
        isinstance(period, bool)
        or not isinstance(period, int)
        or not TOTP_PERIOD_MIN <= period <= TOTP_PERIOD_MAX
    ):
        raise OtpValidationError(
            "TOTP period must be an integer from 1 through 86400"
        )


def _validate_counter(counter: Any) -> None:
    if (
        isinstance(counter, bool)
        or not isinstance(counter, int)
        or not 0 <= counter <= UINT64_MAX
    ):
        raise OtpValidationError("HOTP counter must be an unsigned 64-bit integer")
