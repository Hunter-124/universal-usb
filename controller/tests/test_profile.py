from __future__ import annotations

from collections import deque
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uusb.openocd import OpenOCDFlashError, OpenOCDUnavailable
from uusb.profile import (
    PROFILE_SPECS,
    ProfileFlashError,
    ProfileVerificationError,
    set_profile,
)
from uusb.protocol import Header, Profile, UsbFlag


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, interval: float) -> None:
        self.now += interval


class FakeTransport:
    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.flash_error: Exception | None = None

    def close(self) -> None:
        self.events.append("transport-close")

    def flash(self, path: Path) -> None:
        self.events.append(("flash", path))
        if self.flash_error is not None:
            raise self.flash_error

    def start(self) -> None:
        self.events.append("transport-start")


class FakeEngine:
    def __init__(
        self,
        transport: FakeTransport,
        headers: list[Header | Exception],
        events: list[object],
    ) -> None:
        self.transport = transport
        self._headers = deque(headers)
        self._last: Header | Exception | None = None
        self.events = events

    def stop_heartbeat(self) -> None:
        self.events.append("heartbeat-stop")
    def start_heartbeat(self) -> None:
        self.events.append("heartbeat-start")


    def read_header(self) -> Header:
        if self._headers:
            self._last = self._headers.popleft()
        if isinstance(self._last, Exception):
            raise self._last
        if self._last is None:
            raise AssertionError("fake header sequence is empty")
        return self._last


def header(
    profile: Profile,
    *,
    boot: int,
    uptime: int,
    mounted: bool,
) -> Header:
    return Header(
        magic=0x31425355,
        abi_version=1,
        layout_size=0x1000,
        fw_version=0x00010000,
        profile=int(profile),
        boot_counter=boot,
        uptime_ms=uptime,
        usb_flags=int(UsbFlag.MOUNTED) if mounted else 0,
        host_heartbeat=0,
        last_error=0,
        control_completed=0,
        media_epoch=0,
        block_completed=0,
    )


class ProfileSetTests(unittest.TestCase):
    def run_set(
        self,
        name: str,
        headers: list[Header | Exception],
        *,
        timeout: float = 0.050,
    ) -> tuple[object, list[object], FakeTransport]:
        events: list[object] = []
        transport = FakeTransport(events)
        engine = FakeEngine(transport, headers, events)
        clock = FakeClock()
        result = set_profile(
            engine,  # type: ignore[arg-type]
            name,
            "/firmware/build",
            timeout=timeout,
            poll_interval=0.005,
            liveness_interval=0.001,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        return result, events, transport

    def assert_verification_fails(
        self, name: str, headers: list[Header | Exception]
    ) -> None:
        with self.assertRaises(ProfileVerificationError):
            self.run_set(name, headers)

    def test_exact_name_id_and_elf_mapping(self) -> None:
        self.assertEqual(
            {
                name: (int(spec.profile), spec.elf_basename)
                for name, spec in PROFILE_SPECS.items()
            },
            {
                "hid-msc": (1, "uusb-hid-msc.elf"),
                "microphone": (2, "uusb-microphone.elf"),
                "webcam": (3, "uusb-webcam.elf"),
                "security-token": (4, "uusb-security-token.elf"),
            },
        )

    def test_disconnect_then_unmounted_to_mounted_passes_liveness_gate(self) -> None:
        result, events, _ = self.run_set(
            "webcam",
            [
                header(Profile.HID_MSC, boot=8, uptime=400, mounted=True),
                OpenOCDUnavailable("target reset"),
                header(Profile.WEBCAM, boot=9, uptime=2, mounted=False),
                header(Profile.WEBCAM, boot=9, uptime=10, mounted=True),
                header(Profile.WEBCAM, boot=9, uptime=12, mounted=True),
            ],
        )
        self.assertEqual(result.profile, Profile.WEBCAM)
        self.assertEqual(result.header.uptime_ms, 12)
        self.assertEqual(
            events,
            [
                "heartbeat-stop",
                "transport-close",
                ("flash", Path("/firmware/build/uusb-webcam.elf")),
                "transport-start",
                "heartbeat-start",
            ],
        )

    def test_stale_preexisting_profile_header_fails(self) -> None:
        self.assert_verification_fails(
            "microphone",
            [
                header(Profile.MICROPHONE, boot=3, uptime=100, mounted=True),
                header(Profile.MICROPHONE, boot=3, uptime=100, mounted=True),
            ],
        )

    def test_new_boot_with_stale_mounted_state_fails(self) -> None:
        self.assert_verification_fails(
            "microphone",
            [
                header(Profile.HID_MSC, boot=3, uptime=100, mounted=True),
                header(Profile.MICROPHONE, boot=4, uptime=1, mounted=True),
            ],
        )
    def test_same_boot_unmounted_to_mounted_is_stale(self) -> None:
        self.assert_verification_fails(
            "microphone",
            [
                header(Profile.MICROPHONE, boot=3, uptime=100, mounted=True),
                header(Profile.MICROPHONE, boot=3, uptime=101, mounted=False),
                header(Profile.MICROPHONE, boot=3, uptime=102, mounted=True),
            ],
        )

    def test_nonadvancing_uptime_fails(self) -> None:
        self.assert_verification_fails(
            "security-token",
            [
                header(Profile.HID_MSC, boot=3, uptime=100, mounted=True),
                header(Profile.SECURITY_TOKEN, boot=4, uptime=1, mounted=False),
                header(Profile.SECURITY_TOKEN, boot=4, uptime=5, mounted=True),
                header(Profile.SECURITY_TOKEN, boot=4, uptime=5, mounted=True),
            ],
        )

    def test_boot_counter_change_during_liveness_fails(self) -> None:
        self.assert_verification_fails(
            "webcam",
            [
                header(Profile.HID_MSC, boot=3, uptime=100, mounted=True),
                header(Profile.WEBCAM, boot=4, uptime=1, mounted=False),
                header(Profile.WEBCAM, boot=4, uptime=5, mounted=True),
                header(Profile.WEBCAM, boot=5, uptime=1, mounted=True),
            ],
        )


    def test_wrong_profile_fails_after_fresh_mount_and_liveness(self) -> None:
        self.assert_verification_fails(
            "security-token",
            [
                header(Profile.HID_MSC, boot=3, uptime=100, mounted=True),
                header(Profile.WEBCAM, boot=4, uptime=1, mounted=False),
                header(Profile.WEBCAM, boot=4, uptime=5, mounted=True),
                header(Profile.WEBCAM, boot=4, uptime=8, mounted=True),
            ],
        )

    def test_mailbox_timeout_is_verification_error(self) -> None:
        self.assert_verification_fails(
            "hid-msc",
            [
                header(Profile.WEBCAM, boot=3, uptime=100, mounted=True),
                OpenOCDUnavailable("target disconnected"),
            ],
        )

    def test_flash_failure_is_distinct_and_does_not_restart_session(self) -> None:
        events: list[object] = []
        transport = FakeTransport(events)
        transport.flash_error = OpenOCDFlashError("verify failed")
        engine = FakeEngine(
            transport,
            [header(Profile.HID_MSC, boot=3, uptime=100, mounted=True)],
            events,
        )
        clock = FakeClock()
        with self.assertRaises(ProfileFlashError):
            set_profile(
                engine,  # type: ignore[arg-type]
                "security-token",
                "/firmware/build",
                timeout=0.050,
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
        self.assertEqual(
            events,
            [
                "heartbeat-stop",
                "transport-close",
                ("flash", Path("/firmware/build/uusb-security-token.elf")),
            ],
        )


if __name__ == "__main__":
    unittest.main()
