"""Single-owner Universal USB controller daemon and mailbox service."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import socket
import struct
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .ccid import CcidBackend
from .command_engine import (
    CommandEngine,
    CommandEngineError,
    MailboxCommandTimeout,
    MailboxNotLive,
    MailboxResetDuringCommand,
)
from .fido import FidoBackend, TouchRequired
from .hid import (
    CONSUMER_CONTROLS,
    KEYBOARD_KEYS,
    keyboard_report,
    split_mouse_reports,
    text_reports,
)
from .media import MediaError, MediaState, RawImage
from .openocd import OpenOCDError, OpenOCDTransport
from .otp import OtpStore, keyboard_reports as otp_keyboard_reports
from .profile import (
    PROFILE_SPECS,
    ProfileFlashError,
    ProfileVerificationError,
    set_profile,
)
from .protocol import (
    BLOCK_OFFSET,
    BLOCK_SIZE,
    FIELD_OFFSETS,
    MAILBOX_ADDRESS,
    TOKEN_REQUEST_OFFSET,
    TOKEN_REQUEST_SIZE,
    BlockOperation,
    Opcode,
    Profile,
    ProtocolError,
    Status,
    TokenRequest,
    TokenTransport,
    UsbFlag,
    block_response_crc,
    pack_token_response_body,
    parse_block_request,
    parse_token_request,
)
from .safety import ACKNOWLEDGEMENT_PHRASE, SafetyAcknowledgementState
from .state import secure_state_dir

EXIT_SUCCESS = 0
EXIT_PREFLIGHT = 2
EXIT_UNAVAILABLE = 3
EXIT_PROFILE = 4
EXIT_MAILBOX = 5
EXIT_FLASH = 6
EXIT_REJECTED = 7
EXIT_VERIFICATION = 8

PROFILE_NAMES = {int(spec.profile): name for name, spec in PROFILE_SPECS.items()}
PATTERNS = {"bars": 0, "checker": 1, "gradient": 2}
BUTTONS = {"left": 1, "right": 2, "middle": 4}
MAX_RPC_LINE = 1 << 20


class DaemonFault(RuntimeError):
    """A stable private-RPC failure."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Event:
    sequence: int
    monotonic_ns: int
    kind: str
    details: Mapping[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "monotonic_ns": self.monotonic_ns,
            "kind": self.kind,
            "details": dict(self.details),
        }


class EventRing:
    """Bounded, structured and secret-free daemon event history."""

    def __init__(self, capacity: int = 256) -> None:
        if capacity <= 0:
            raise ValueError("event capacity must be positive")
        self._events: deque[Event] = deque(maxlen=capacity)
        self._sequence = 0
        self._lock = threading.Lock()

    def append(self, kind: str, **details: Any) -> Event:
        if not kind or any(not isinstance(key, str) for key in details):
            raise ValueError("invalid event")
        with self._lock:
            event = Event(self._sequence + 1, time.monotonic_ns(), kind, dict(details))
            self._sequence = event.sequence
            self._events.append(event)
            return event

    def list(self, after: int = 0) -> list[dict[str, Any]]:
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise DaemonFault(EXIT_PREFLIGHT, "event cursor must be a nonnegative integer")
        with self._lock:
            return [event.to_json() for event in self._events if event.sequence > after]


class ControllerDaemon:
    """Own OpenOCD, media, credentials, token state, and all mailbox traffic."""

    def __init__(
        self,
        engine: CommandEngine,
        *,
        media: RawImage | None = None,
        fido: FidoBackend | None = None,
        ccid: CcidBackend | None = None,
        otp: OtpStore | None = None,
        state_directory: str | os.PathLike[str] | None = None,
        firmware_directory: str | os.PathLike[str] = "build/firmware",
        service_interval: float = 0.010,
        heartbeat_interval: float = 0.250,
    ) -> None:
        if service_interval <= 0 or heartbeat_interval <= 0:
            raise ValueError("service intervals must be positive")
        root = secure_state_dir(state_directory)
        self.engine = engine
        self.media = media or RawImage()
        self.fido = fido or FidoBackend(root)
        self.ccid = ccid or CcidBackend(root)
        self.otp = otp or OtpStore(root / "otp.json")
        self.firmware_directory = Path(firmware_directory)
        self.events = EventRing()
        self._service_interval = service_interval
        self._heartbeat_interval = heartbeat_interval
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._service_thread: threading.Thread | None = None
        self._lifecycle_lock = threading.RLock()
        self._request_lock = threading.RLock()
        self._service_lock = threading.RLock()
        self._held_keys: set[str] = set()
        self._mouse_buttons = 0
        self._consumer_usage = 0
        self._media_epoch: int | None = None
        self._pending_token: TokenRequest | None = None
        self._token_response_sequence: int | None = None
        self._service_error: str | None = None

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._service_thread is not None and self._service_thread.is_alive():
                return
            self.engine.transport.start()
            self.engine.start_heartbeat()
            self._stop.clear()
            self._wake.clear()
            thread = threading.Thread(
                target=self._service_loop,
                name="uusbd-mailbox-service",
                daemon=True,
            )
            self._service_thread = thread
            thread.start()
            self.events.append("daemon.started")

    def close(self) -> None:
        with self._lifecycle_lock:
            thread, self._service_thread = self._service_thread, None
            self._stop.set()
            self._wake.set()
        if thread is not None:
            thread.join()
        self._release_all(best_effort=True)
        try:
            self.media.close()
        except MediaError as error:
            self.events.append("media.close_failed", error=type(error).__name__)
        self.engine.close()
        self.events.append("daemon.stopped")

    def client_broken(self) -> None:
        """Release held reports when a client vanishes before receiving its result."""
        self._release_all(best_effort=True)
        self.events.append("rpc.client_broken")

    def handle(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        if not isinstance(method, str) or not method:
            raise DaemonFault(EXIT_PREFLIGHT, "RPC method must be a nonempty string")
        values = {} if params is None else dict(params)
        if not isinstance(params, (Mapping, type(None))):
            raise DaemonFault(EXIT_PREFLIGHT, "RPC params must be an object")
        with self._request_lock:
            try:
                return self._dispatch(method, values)
            except DaemonFault:
                raise
            except ProfileFlashError as error:
                raise DaemonFault(EXIT_FLASH, str(error)) from error
            except ProfileVerificationError as error:
                raise DaemonFault(EXIT_VERIFICATION, str(error)) from error
            except (MailboxCommandTimeout, MailboxNotLive, MailboxResetDuringCommand,
                    ProtocolError) as error:
                raise DaemonFault(EXIT_MAILBOX, str(error)) from error
            except OpenOCDError as error:
                raise DaemonFault(EXIT_UNAVAILABLE, str(error)) from error
            except MediaError as error:
                raise DaemonFault(EXIT_REJECTED, str(error)) from error
            except CommandEngineError as error:
                raise DaemonFault(EXIT_MAILBOX, str(error)) from error
            except (KeyError, TypeError, ValueError) as error:
                raise DaemonFault(EXIT_PREFLIGHT, str(error)) from error

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "status":
            self._require_only(params)
            return self._status()
        if method == "events":
            self._require_only(params, "after")
            return {"events": self.events.list(params.get("after", 0))}
        if method == "doctor":
            self._require_only(params)
            live = self.engine.check_liveness()
            state = self.engine.transport.target_state()
            if state != "running":
                raise DaemonFault(EXIT_VERIFICATION, f"target core is {state}, not running")
            result = self._header_json(live.second)
            result["target_state"] = state
            result["uptime_advanced"] = True
            return result
        if method == "profile.list":
            self._require_only(params)
            return {"profiles": list(PROFILE_SPECS)}
        if method == "profile.show":
            self._require_only(params)
            header = self.engine.read_header()
            return self._header_json(header)
        if method == "profile.set":
            self._require_only(params, "profile", "yes")
            if params.get("yes") is not True:
                raise DaemonFault(EXIT_PREFLIGHT, "profile set requires --yes")
            name = self._string(params, "profile")
            with self._service_lock:
                result = set_profile(self.engine, name, self.firmware_directory)
            self._media_epoch = None
            self._pending_token = None
            self.events.append("profile.set", profile=name)
            return self._header_json(result.header)
        if method == "hid.release_all":
            self._require_only(params)
            self._release_all(best_effort=False)
            self.events.append("hid.released_all")
            return {"released": True}
        if method == "hid.key":
            self._require_profile(Profile.HID_MSC)
            return self._hid_key(params)
        if method == "hid.text":
            self._require_profile(Profile.HID_MSC)
            return self._hid_text(params)
        if method == "hid.mouse":
            self._require_profile(Profile.HID_MSC)
            return self._hid_mouse(params)
        if method == "hid.consumer":
            self._require_profile(Profile.HID_MSC)
            return self._hid_consumer(params)
        if method == "msc.attach":
            self._require_profile(Profile.HID_MSC)
            return self._msc_attach(params)
        if method == "msc.detach":
            self._require_profile(Profile.HID_MSC)
            return self._msc_detach(params)
        if method == "msc.status":
            self._require_only(params)
            return self._media_status()
        if method == "mic.tone":
            self._require_profile(Profile.MICROPHONE)
            return self._mic_tone(params)
        if method == "mic.silence":
            self._require_profile(Profile.MICROPHONE)
            self._require_only(params)
            self._submit_ok(Opcode.MIC_CONFIG, struct.pack("<BBHH", 0, 0, 1000, 0))
            self.events.append("mic.silence")
            return {"mode": "silence"}
        if method == "mic.status":
            self._require_profile(Profile.MICROPHONE)
            self._require_only(params)
            return self._status()
        if method == "cam.pattern":
            self._require_profile(Profile.WEBCAM)
            self._require_only(params, "pattern")
            pattern = self._string(params, "pattern")
            try:
                value = PATTERNS[pattern]
            except KeyError as error:
                raise ValueError("unknown camera pattern") from error
            self._submit_ok(Opcode.UVC_PATTERN, bytes((value,)))
            self.events.append("cam.pattern", pattern=pattern)
            return {"pattern": pattern}
        if method == "cam.status":
            self._require_profile(Profile.WEBCAM)
            self._require_only(params)
            return self._status()
        if method == "token.status":
            self._require_profile(Profile.SECURITY_TOKEN)
            self._require_only(params)
            return self._token_status()
        if method == "token.touch":
            self._require_profile(Profile.SECURITY_TOKEN)
            self._require_only(params)
            return self._token_touch()
        if method == "token.reset":
            self._require_profile(Profile.SECURITY_TOKEN)
            self._require_only(params, "yes")
            if params.get("yes") is not True:
                raise ValueError("token reset requires --yes")
            self.fido.reset()
            self.ccid.reset()
            self.events.append("token.reset")
            return {"reset": True}
        if method == "token.pin.set":
            self._require_profile(Profile.SECURITY_TOKEN)
            self._require_only(params, "pin")
            pin = self._secret_string(params, "pin")
            self.ccid.set_pin(pin)
            self.events.append("token.pin_set")
            return {"set": True}
        if method == "token.credential.list":
            self._require_profile(Profile.SECURITY_TOKEN)
            self._require_only(params)
            return {"credentials": self.fido.list_credentials()}
        if method == "token.credential.delete":
            self._require_profile(Profile.SECURITY_TOKEN)
            self._require_only(params, "credential_id")
            identifier = self._string(params, "credential_id")
            deleted = self.fido.delete_credential(identifier)
            self.events.append("token.credential_deleted", credential_id=identifier)
            return {"deleted": deleted}
        if method == "token.otp.provision":
            return self._otp_provision(params)
        if method == "token.otp.remove":
            self._require_profile(Profile.SECURITY_TOKEN)
            self._require_only(params, "name")
            name = self._string(params, "name")
            self.otp.remove(name)
            self.events.append("token.otp_removed", name=name)
            return {"removed": name}
        if method == "token.otp.select":
            self._require_profile(Profile.SECURITY_TOKEN)
            self._require_only(params, "name")
            name = self._string(params, "name")
            self.otp.select(name)
            self.events.append("token.otp_selected", name=name)
            return {"selected": name}
        if method in {"scenario.validate", "scenario.run"}:
            return self._scenario(method, params)
        raise DaemonFault(EXIT_PREFLIGHT, f"unknown RPC method {method!r}")

    def _status(self) -> dict[str, Any]:
        header = self.engine.read_header()
        result = self._header_json(header)
        result["media"] = self._media_status()
        result["service_error"] = self._service_error
        if header.profile == int(Profile.SECURITY_TOKEN):
            result["token"] = self._token_status()
        return result

    @staticmethod
    def _header_json(header: Any) -> dict[str, Any]:
        version = header.fw_version
        return {
            "profile": PROFILE_NAMES.get(header.profile, "unknown"),
            "profile_id": header.profile,
            "firmware_version": f"{version >> 16}.{version >> 8 & 0xff}.{version & 0xff}",
            "boot_counter": header.boot_counter,
            "uptime_ms": header.uptime_ms,
            "usb": {
                "clock_valid": bool(header.usb_flags & UsbFlag.CLOCK_VALID),
                "mounted": bool(header.usb_flags & UsbFlag.MOUNTED),
                "suspended": bool(header.usb_flags & UsbFlag.SUSPENDED),
            },
            "last_error": header.last_error,
        }

    def _require_profile(self, profile: Profile) -> None:
        active = self.engine.read_header().profile
        if active != int(profile):
            raise DaemonFault(
                EXIT_PROFILE,
                f"active profile is {PROFILE_NAMES.get(active, active)!r}; "
                f"operation requires {PROFILE_NAMES[int(profile)]!r}",
            )

    def _submit_ok(self, opcode: Opcode, payload: bytes = b"") -> bytes:
        response = self.engine.submit(opcode, payload)
        if response.status is not Status.OK:
            raise DaemonFault(EXIT_REJECTED, f"firmware rejected {opcode.name}: {response.status.name}")
        return response.payload

    def _send_keyboard(self, report: bytes) -> None:
        self._submit_ok(Opcode.HID_KEYBOARD, report)

    def _hid_key(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_only(params, "operation", "keys", "hold_ms")
        operation = self._string(params, "operation")
        keys = self._string_list(params, "keys")
        if not keys:
            raise ValueError("at least one key is required")
        unknown = [key for key in keys if key not in KEYBOARD_KEYS]
        if unknown:
            raise ValueError(f"unknown key {unknown[0]!r}")
        hold_ms = self._integer(params.get("hold_ms", 50), "hold_ms", 10, 5000)
        before = set(self._held_keys)
        if operation in {"down", "tap"}:
            candidate = self._held_keys | set(keys)
            down = keyboard_report(sorted(candidate))
            self._held_keys = candidate
            self._send_keyboard(down)
        if operation == "tap":
            time.sleep(hold_ms / 1000.0)
            self._held_keys = before
            self._send_keyboard(keyboard_report(sorted(self._held_keys)))
        elif operation == "up":
            self._held_keys.difference_update(keys)
            self._send_keyboard(keyboard_report(sorted(self._held_keys)))
        elif operation != "down":
            raise ValueError("key operation must be tap, down, or up")
        self.events.append("hid.key", operation=operation, keys=keys)
        return {"held": sorted(self._held_keys)}

    def _hid_text(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_only(params, "text", "interval_ms")
        text = self._string(params, "text", allow_empty=True)
        interval = self._integer(params.get("interval_ms", 20), "interval_ms", 0, 5000)
        reports = tuple(text_reports(text))
        for index, report in enumerate(reports):
            self._send_keyboard(report)
            if interval and index + 1 < len(reports):
                time.sleep(interval / 1000.0)
        self.events.append("hid.text", characters=len(text))
        return {"characters": len(text), "reports": len(reports)}

    def _hid_mouse(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_only(params, "operation", "dx", "dy", "button", "amount")
        operation = self._string(params, "operation")
        reports: tuple[bytes, ...]
        if operation == "move":
            dx = self._integer(params.get("dx"), "dx", -(1 << 31), (1 << 31) - 1)
            dy = self._integer(params.get("dy"), "dy", -(1 << 31), (1 << 31) - 1)
            reports = split_mouse_reports(self._mouse_buttons, dx, dy, 0)
        elif operation in {"button.down", "button.up", "button.click"}:
            button = self._string(params, "button")
            try:
                bit = BUTTONS[button]
            except KeyError as error:
                raise ValueError("mouse button must be left, right, or middle") from error
            button_reports: list[bytes] = []
            if operation in {"button.down", "button.click"}:
                self._mouse_buttons |= bit
                button_reports.append(bytes((self._mouse_buttons, 0, 0, 0)))
            if operation in {"button.up", "button.click"}:
                self._mouse_buttons &= ~bit
                button_reports.append(bytes((self._mouse_buttons, 0, 0, 0)))
            reports = tuple(button_reports)
        elif operation == "scroll":
            amount = self._integer(params.get("amount"), "amount", -(1 << 31), (1 << 31) - 1)
            reports = split_mouse_reports(self._mouse_buttons, 0, 0, amount)
        else:
            raise ValueError("unknown mouse operation")
        for report in reports:
            self._submit_ok(Opcode.HID_MOUSE, report)
        self.events.append("hid.mouse", operation=operation, reports=len(reports))
        return {"reports": len(reports), "buttons": self._mouse_buttons}

    def _hid_consumer(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_only(params, "operation", "control")
        operation = self._string(params, "operation")
        control = self._string(params, "control")
        try:
            usage = CONSUMER_CONTROLS[control]
        except KeyError as error:
            raise ValueError("unknown consumer control") from error
        if operation in {"down", "tap"}:
            self._consumer_usage = usage
            self._submit_ok(Opcode.HID_CONSUMER, struct.pack("<H", usage))
        if operation in {"up", "tap"}:
            self._consumer_usage = 0
            self._submit_ok(Opcode.HID_CONSUMER, b"\0\0")
        if operation not in {"down", "up", "tap"}:
            raise ValueError("consumer operation must be tap, down, or up")
        self.events.append("hid.consumer", operation=operation, control=control)
        return {"held": self._consumer_usage != 0}

    def _msc_attach(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_only(params, "image", "read_write")
        image = self._string(params, "image")
        read_write = params.get("read_write", False)
        if not isinstance(read_write, bool):
            raise TypeError("read_write must be a boolean")
        self.media.attach(image, read_write)
        payload = struct.pack("<IB3x", self.media.block_count, int(read_write))
        try:
            self._submit_ok(Opcode.MEDIA_ATTACH, payload)
            self._media_epoch = self.engine.read_header().media_epoch
        except Exception:
            self.media.close()
            raise
        self.events.append("media.attached", read_write=read_write, blocks=self.media.block_count)
        return self._media_status()

    def _msc_detach(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_only(params)
        self._submit_ok(Opcode.MEDIA_DETACH)
        self.media.detach(5.0)
        self._media_epoch = None
        self.events.append("media.detached")
        return self._media_status()

    def _media_status(self) -> dict[str, Any]:
        return {
            "state": self.media.state.name.lower(),
            "image": self.media.path,
            "block_count": self.media.block_count,
            "read_write": self.media.read_write,
            "removal_prevented": self.media.removal_prevented,
        }

    def _mic_tone(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_only(params, "frequency_hz", "level_dbfs")
        frequency = self._integer(params.get("frequency_hz"), "frequency_hz", 20, 20_000)
        level = params.get("level_dbfs", -12.0)
        if isinstance(level, bool) or not isinstance(level, (int, float)) or not math.isfinite(level):
            raise ValueError("level_dbfs must be a finite number")
        if not -96.0 <= float(level) <= 0.0:
            raise ValueError("level_dbfs must be between -96 and 0")
        amplitude = min(32767, round(32767 * (10.0 ** (float(level) / 20.0))))
        self._submit_ok(Opcode.MIC_CONFIG, struct.pack("<BBHH", 1, 0, frequency, amplitude))
        self.events.append("mic.tone", frequency_hz=frequency, level_dbfs=float(level))
        return {"mode": "tone", "frequency_hz": frequency, "level_dbfs": float(level)}

    def _token_status(self) -> dict[str, Any]:
        return {
            "fido": self.fido.status(),
            "ccid": self.ccid.status(),
            "otp": self.otp.status(),
            "pending_exchange": self._pending_token is not None,
        }

    def _token_touch(self) -> dict[str, Any]:
        self.fido.touch()
        self._wake.set()
        otp_status = self.otp.status()
        selected = otp_status["selected"]
        typed = False
        if selected is not None:
            metadata = next(
                item for item in otp_status["credentials"] if item["name"] == selected
            )
            code = self.otp.generate_selected()
            try:
                for report in otp_keyboard_reports(code):
                    self._send_keyboard(report)
            except Exception:
                try:
                    self._send_keyboard(b"\0" * 8)
                finally:
                    raise
            if metadata["kind"] == "hotp":
                self.otp.commit_hotp(selected, metadata["counter"])
            typed = True
            self.events.append(
                "token.otp_typed", name=selected, token_kind=metadata["kind"]
            )
        self.events.append("token.touch", otp_typed=typed)
        return {"touched": True, "otp_typed": typed}

    def _otp_provision(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_profile(Profile.SECURITY_TOKEN)
        self._require_only(params, "name", "kind", "secret", "digits", "period", "counter")
        name = self._string(params, "name")
        kind = self._string(params, "kind")
        secret = self._secret_string(params, "secret")
        digits = self._integer(params.get("digits", 6), "digits", 6, 8)
        if kind == "totp":
            period = self._integer(params.get("period", 30), "period", 1, 86_400)
            if "counter" in params:
                raise ValueError("TOTP provisioning does not accept a counter")
            self.otp.provision(
                name, kind, secret, digits=digits, period=period, counter=None
            )
        elif kind == "hotp":
            counter = self._integer(params.get("counter", 0), "counter", 0, (1 << 64) - 1)
            if "period" in params:
                raise ValueError("HOTP provisioning does not accept a period")
            self.otp.provision(
                name, kind, secret, digits=digits, period=None, counter=counter
            )
        else:
            raise ValueError("OTP kind must be 'hotp' or 'totp'")
        self.events.append("token.otp_provisioned", name=name, token_kind=kind, digits=digits)
        return {"provisioned": name, "kind": kind, "digits": digits}

    def _scenario(self, method: str, params: dict[str, Any]) -> Any:
        from .scenario import load_and_preflight, run_scenario

        self._require_only(params, "file", "switch_profile")
        path = self._string(params, "file")
        scenario = load_and_preflight(path)
        if method == "scenario.validate":
            return {"valid": True, "profile": scenario["profile"], "steps": len(scenario["steps"])}
        switch = params.get("switch_profile", False)
        if not isinstance(switch, bool):
            raise TypeError("switch_profile must be a boolean")
        active = PROFILE_NAMES.get(self.engine.read_header().profile)
        if active != scenario["profile"]:
            if not switch:
                raise DaemonFault(EXIT_PROFILE, "scenario profile does not match the active profile")
            with self._service_lock:
                set_profile(self.engine, scenario["profile"], self.firmware_directory)
        try:
            results = run_scenario(scenario, self.handle)
        except Exception:
            self._release_all(best_effort=True)
            raise
        self.events.append("scenario.completed", steps=len(results))
        return {"steps": results}

    def _release_all(self, *, best_effort: bool) -> None:
        self._held_keys.clear()
        self._mouse_buttons = 0
        self._consumer_usage = 0
        try:
            self._submit_ok(Opcode.RELEASE_ALL)
        except Exception:
            if not best_effort:
                raise

    def _service_loop(self) -> None:
        while not self._stop.is_set():
            try:
                with self._service_lock:
                    heartbeat_error = self.engine.heartbeat_error
                    if heartbeat_error is not None:
                        raise heartbeat_error
                    header = self.engine.read_header()
                    if header.profile == int(Profile.HID_MSC):
                        self._service_block_slot(header.media_epoch)
                    elif header.profile == int(Profile.SECURITY_TOKEN):
                        self._service_token_slot()
                    self._service_error = None
            except Exception as error:
                self._service_error = type(error).__name__
                self.events.append("mailbox.service_error", error=type(error).__name__)
            self._wake.wait(self._service_interval)
            self._wake.clear()

    def _service_block_slot(self, firmware_epoch: int) -> None:
        raw = self.engine.transport.read_bytes(MAILBOX_ADDRESS + BLOCK_OFFSET, BLOCK_SIZE)
        request_seq = int.from_bytes(raw[0:4], "little")
        response_seq = int.from_bytes(raw[0x218:0x21c], "little")
        if request_seq == response_seq:
            return
        try:
            request = parse_block_request(raw)
            if self._media_epoch is None or request.epoch != self._media_epoch or firmware_epoch != self._media_epoch:
                raise MediaError("block request media epoch is stale")
            if request.operation is BlockOperation.READ:
                data = self.media.read_block(request.lba)
            elif request.operation is BlockOperation.WRITE:
                self.media.write_block(request.lba, request.data)
                data = b""
            else:
                self.media.synchronize()
                data = b""
            status = Status.OK
        except (MediaError, ProtocolError):
            request = None
            data = b""
            status = Status.IO_ERROR
        operation = request.operation if request is not None else BlockOperation.FLUSH
        length = request.data_length if request is not None else 0
        if status is Status.OK and operation is BlockOperation.READ:
            self.engine.transport.write_bytes(
                MAILBOX_ADDRESS + FIELD_OFFSETS["block.data"], data
            )
        crc = block_response_crc(status, operation, length, data)
        self.engine.transport.write_words(
            MAILBOX_ADDRESS + FIELD_OFFSETS["block.response_status"],
            (int(status), crc),
        )
        self.engine.transport.write_words(
            MAILBOX_ADDRESS + FIELD_OFFSETS["block.response_seq"],
            (request_seq,),
        )

    def _service_token_slot(self) -> None:
        if self._pending_token is None:
            raw = self.engine.transport.read_bytes(
                MAILBOX_ADDRESS + TOKEN_REQUEST_OFFSET, TOKEN_REQUEST_SIZE
            )
            request_sequence = int.from_bytes(raw[0:4], "little")
            acknowledged_sequence = int.from_bytes(raw[0x210:0x214], "little")
            if request_sequence == acknowledged_sequence:
                return
            request = parse_token_request(raw)
            self.engine.transport.write_words(
                MAILBOX_ADDRESS + FIELD_OFFSETS["token_request.request_ack"],
                (request.sequence,),
            )
            self._pending_token = request
        request = self._pending_token
        assert request is not None
        try:
            if request.transport is TokenTransport.FIDO_HID:
                response = self.fido.handle_bridge(request.data)
            elif request.transport is TokenTransport.CCID:
                response = self.ccid.handle_bridge(request.data)
            else:
                raise ProtocolError("unsupported token transport")
        except TouchRequired:
            return
        if not isinstance(response, bytes) or not 1 <= len(response) <= 512:
            raise ProtocolError("token backend returned an invalid response")
        current_response = self.engine.transport.read_words(
            MAILBOX_ADDRESS + FIELD_OFFSETS["token_response.response_seq"], 1
        )[0]
        response_ack = self.engine.transport.read_words(
            MAILBOX_ADDRESS + FIELD_OFFSETS["token_response.response_ack"], 1
        )[0]
        if current_response != response_ack:
            return
        sequence = (current_response + 1) & 0xffffffff
        body = pack_token_response_body(request.sequence, response)
        self.engine.transport.write_bytes(
            MAILBOX_ADDRESS + FIELD_OFFSETS["token_response.request_seq"], body
        )
        self.engine.transport.write_words(
            MAILBOX_ADDRESS + FIELD_OFFSETS["token_response.response_seq"], (sequence,)
        )
        self._pending_token = None

    @staticmethod
    def _require_only(params: Mapping[str, Any], *allowed: str) -> None:
        unknown = set(params) - set(allowed)
        if unknown:
            raise ValueError(f"unknown parameter {sorted(unknown)[0]!r}")

    @staticmethod
    def _string(params: Mapping[str, Any], name: str, *, allow_empty: bool = False) -> str:
        value = params.get(name)
        if not isinstance(value, str) or (not allow_empty and not value):
            raise TypeError(f"{name} must be a {'possibly empty ' if allow_empty else ''}string")
        return value

    @staticmethod
    def _secret_string(params: Mapping[str, Any], name: str) -> str:
        value = params.get(name)
        if not isinstance(value, str) or not value:
            raise TypeError(f"{name} must be a nonempty string")
        return value

    @staticmethod
    def _string_list(params: Mapping[str, Any], name: str) -> list[str]:
        value = params.get(name)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise TypeError(f"{name} must be an array of strings")
        return value

    @staticmethod
    def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer from {minimum} through {maximum}")
        return value


def runtime_socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise DaemonFault(EXIT_UNAVAILABLE, "XDG_RUNTIME_DIR is not set")
    runtime_root = Path(runtime)
    runtime_root.mkdir(mode=0o700, exist_ok=True)
    runtime_info = runtime_root.lstat()
    if runtime_root.is_symlink() or not runtime_root.is_dir() or runtime_info.st_uid != os.geteuid():
        raise DaemonFault(EXIT_UNAVAILABLE, "XDG_RUNTIME_DIR is not a private owned directory")
    root = runtime_root / "universal-usb"
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise DaemonFault(EXIT_UNAVAILABLE, "runtime directory is not a real directory")
    os.chmod(root, 0o700)
    return root / "control.sock"


def _receive_line(connection: socket.socket) -> bytes:
    buffer = bytearray()
    while True:
        chunk = connection.recv(4096)
        if not chunk:
            raise EOFError("client disconnected before request newline")
        buffer.extend(chunk)
        if len(buffer) > MAX_RPC_LINE:
            raise ValueError("RPC request exceeds size limit")
        marker = buffer.find(b"\n")
        if marker >= 0:
            if marker != len(buffer) - 1:
                raise ValueError("RPC connection contains data after request newline")
            return bytes(buffer[:marker])


def serve(daemon: ControllerDaemon, path: Path) -> None:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat_is_socket(info.st_mode) or info.st_uid != os.geteuid():
                raise DaemonFault(EXIT_UNAVAILABLE, "refusing unsafe existing control socket")
            path.unlink()
        listener.bind(os.fspath(path))
        os.chmod(path, 0o600)
        listener.listen(16)
        listener.settimeout(0.25)
        while not daemon._stop.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            request: Any = None
            with connection:
                try:
                    peer_pid, peer_uid, _ = struct.unpack("3i", connection.getsockopt(
                        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
                    ))
                    del peer_pid
                    if peer_uid != os.geteuid():
                        raise DaemonFault(EXIT_UNAVAILABLE, "RPC peer UID mismatch")
                    request = json.loads(_receive_line(connection))
                    if not isinstance(request, dict) or set(request) != {"id", "method", "params"}:
                        raise DaemonFault(EXIT_PREFLIGHT, "invalid RPC request object")
                    request_id = request["id"]
                    if isinstance(request_id, (dict, list, bool)) or request_id is None:
                        raise DaemonFault(EXIT_PREFLIGHT, "invalid RPC request id")
                    result = daemon.handle(request["method"], request["params"])
                    response = {"id": request_id, "result": result}
                except DaemonFault as error:
                    request_id = request.get("id") if isinstance(request, dict) else None
                    response = {"id": request_id, "error": {"code": error.code, "message": str(error)}}
                except (EOFError, BrokenPipeError, ConnectionResetError):
                    daemon.client_broken()
                    continue
                except Exception as error:
                    request_id = request.get("id") if isinstance(request, dict) else None
                    response = {"id": request_id, "error": {"code": EXIT_PREFLIGHT, "message": str(error)}}
                encoded = json.dumps(response, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"
                try:
                    connection.sendall(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    daemon.client_broken()
    finally:
        listener.close()
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def stat_is_socket(mode: int) -> bool:
    import stat
    return stat.S_ISSOCK(mode)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="uusbd")
    parser.add_argument("--acknowledge-safety", default=os.environ.get("UUSB_SAFETY_ACK"))
    parser.add_argument("--openocd", default="openocd")
    parser.add_argument("--firmware-dir", default="build/firmware")
    parser.add_argument("--state-dir")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    safety = SafetyAcknowledgementState()
    safety.display(sys.stderr)
    try:
        safety.acknowledge(args.acknowledge_safety or "")
        safety.require_before_hardware_operation()
        state_root = secure_state_dir(args.state_dir)
        log_path = state_root / "openocd.log"
        descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0), 0o600)
        os.close(descriptor)
        os.chmod(log_path, 0o600)
        transport = OpenOCDTransport(log_path, executable=args.openocd)
        engine = CommandEngine(transport)
        controller = ControllerDaemon(
            engine,
            state_directory=state_root,
            firmware_directory=args.firmware_dir,
        )
        controller.start()
        path = runtime_socket_path()
    except Exception as error:
        print(f"uusbd: {error}", file=sys.stderr)
        return getattr(error, "code", EXIT_UNAVAILABLE)

    def stop(_signum: int, _frame: Any) -> None:
        controller._stop.set()
        controller._wake.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        serve(controller, path)
    finally:
        controller.close()
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
