"""Firmware profile selection with post-flash mailbox verification."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .command_engine import CommandEngine
from .openocd import OpenOCDError, OpenOCDTransport
from .protocol import Header, Profile, ProtocolError, UsbFlag


@dataclass(frozen=True, slots=True)
class ProfileSpec:
    profile: Profile
    elf_basename: str


PROFILE_SPECS: Mapping[str, ProfileSpec] = MappingProxyType(
    {
        "hid-msc": ProfileSpec(Profile.HID_MSC, "uusb-hid-msc.elf"),
        "microphone": ProfileSpec(Profile.MICROPHONE, "uusb-microphone.elf"),
        "webcam": ProfileSpec(Profile.WEBCAM, "uusb-webcam.elf"),
        "security-token": ProfileSpec(
            Profile.SECURITY_TOKEN, "uusb-security-token.elf"
        ),
    }
)


class ProfileSetError(RuntimeError):
    """Base failure while selecting a firmware profile."""


class ProfileFlashError(ProfileSetError):
    """The requested profile could not be flashed and verified by OpenOCD."""


class ProfileVerificationError(ProfileSetError):
    """Post-flash mailbox state did not prove the requested profile is ready."""


@dataclass(frozen=True, slots=True)
class ProfileSetResult:
    name: str
    profile: Profile
    elf_path: Path
    header: Header


def set_profile(
    engine: CommandEngine,
    name: str,
    firmware_directory: str | os.PathLike[str],
    *,
    timeout: float = 10.0,
    poll_interval: float = 0.050,
    liveness_interval: float = 0.010,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ProfileSetResult:
    """Flash one exact profile ELF and return only after proving USB readiness.

    The single deadline covers the pre-flash snapshot, lifecycle operations, and
    every post-flash poll. A mounted header is accepted only after an unmounted
    post-flash header, so a mailbox image left behind by the previous firmware
    cannot satisfy the gate.
    """
    if timeout <= 0 or poll_interval <= 0 or liveness_interval <= 0:
        raise ValueError("profile-set timeout and intervals must be positive")
    try:
        spec = PROFILE_SPECS[name]
    except KeyError as error:
        choices = ", ".join(PROFILE_SPECS)
        raise ValueError(f"unknown profile {name!r}; expected one of: {choices}") from error

    transport: OpenOCDTransport = engine.transport
    elf_path = (Path(firmware_directory).expanduser() / spec.elf_basename).resolve()
    deadline = monotonic() + timeout

    engine.stop_heartbeat()
    try:
        pre_flash = engine.read_header()
    except Exception as error:
        transport.close()
        raise ProfileVerificationError(
            "could not capture the pre-flash mailbox snapshot"
        ) from error
    transport.close()

    try:
        transport.flash(elf_path)
    except Exception as error:
        raise ProfileFlashError(f"failed to flash {spec.elf_basename}") from error

    try:
        transport.start()
        engine.start_heartbeat()
    except Exception as error:
        raise ProfileVerificationError(
            "persistent OpenOCD/heartbeat could not be restarted after flashing"
        ) from error

    saw_unmounted = False
    last_observation = "no valid post-flash mailbox header"
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ProfileVerificationError(
                f"profile {name!r} did not become ready before the deadline: "
                f"{last_observation}; pre-flash boot counter was "
                f"{pre_flash.boot_counter}"
            )

        try:
            first = engine.read_header()
        except (OpenOCDError, ProtocolError) as error:
            last_observation = f"mailbox unavailable ({error})"
            _sleep_with_deadline(sleep, poll_interval, deadline, monotonic)
            continue

        fresh_boot = first.boot_counter != pre_flash.boot_counter
        first_mounted = bool(first.usb_flags & UsbFlag.MOUNTED)
        if not first_mounted:
            if fresh_boot:
                saw_unmounted = True
                last_observation = (
                    f"profile {first.profile} boot {first.boot_counter} is unmounted"
                )
            else:
                last_observation = "post-flash mailbox still has the pre-flash boot counter"
            _sleep_with_deadline(sleep, poll_interval, deadline, monotonic)
            continue
        if first.profile != int(spec.profile):
            last_observation = (
                f"mounted profile is {first.profile}, expected {int(spec.profile)}"
            )
            _sleep_with_deadline(sleep, poll_interval, deadline, monotonic)
            continue
        if not fresh_boot:
            last_observation = "mounted mailbox still has the pre-flash boot counter"
            _sleep_with_deadline(sleep, poll_interval, deadline, monotonic)
            continue
        if not saw_unmounted:
            last_observation = "mounted state is stale; no post-flash unmounted state observed"
            _sleep_with_deadline(sleep, poll_interval, deadline, monotonic)
            continue

        _sleep_with_deadline(sleep, liveness_interval, deadline, monotonic)
        if deadline - monotonic() <= 0:
            last_observation = "deadline expired between liveness reads"
            continue
        try:
            second = engine.read_header()
        except (OpenOCDError, ProtocolError) as error:
            last_observation = f"mailbox unavailable during liveness check ({error})"
            continue

        if not (second.usb_flags & UsbFlag.MOUNTED):
            saw_unmounted = True
            last_observation = "USB became unmounted during the liveness check"
            continue
        if second.profile != int(spec.profile):
            last_observation = (
                f"profile changed to {second.profile} during the liveness check"
            )
            continue
        if second.boot_counter != first.boot_counter:
            last_observation = "firmware rebooted during the liveness check"
            continue
        uptime_delta = (second.uptime_ms - first.uptime_ms) & 0xFFFFFFFF
        if uptime_delta == 0 or uptime_delta >= 0x80000000:
            last_observation = "firmware uptime did not advance monotonically"
            continue

        return ProfileSetResult(name, spec.profile, elf_path, second)


def _sleep_with_deadline(
    sleep: Callable[[float], None],
    interval: float,
    deadline: float,
    monotonic: Callable[[], float],
) -> None:
    remaining = deadline - monotonic()
    if remaining > 0:
        sleep(min(interval, remaining))
