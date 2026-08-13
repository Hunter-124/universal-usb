from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uusb.hid import (
    CONSUMER_CONTROLS,
    EMPTY_KEYBOARD_REPORT,
    KEYBOARD_KEYS,
    KeyboardState,
    MouseState,
    consumer_report,
    encode_text,
    keyboard_report,
    split_mouse_reports,
    split_signed8,
    text_reports,
    validate_tap_duration,
)
from uusb.otp import (
    OtpConflictError,
    OtpStore,
    OtpValidationError,
    UINT64_MAX,
    keyboard_reports,
)
from uusb.state import (
    StateSecurityError,
    atomic_write_json,
    read_json,
    remove_state_file,
    secure_state_dir,
)


RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


class TemporaryStateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)
        self.root = self.directory / "state"


class SecureJsonStateTests(TemporaryStateTestCase):
    def test_secure_modes_roundtrip_and_atomic_inode_replacement(self) -> None:
        root = secure_state_dir(self.root)
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
        path = root / "value.json"

        atomic_write_json(path, {"generation": 1})
        first_inode = path.stat().st_ino
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(read_json(path, None), {"generation": 1})

        os.chmod(root, 0o755)
        os.chmod(path, 0o644)
        atomic_write_json(path, {"generation": 2})
        self.assertNotEqual(path.stat().st_ino, first_inode)
        self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(read_json(path, None), {"generation": 2})

    def test_file_fsync_precedes_replace_and_directory_fsync_follows(self) -> None:
        path = secure_state_dir(self.root) / "ordered.json"
        events: list[str] = []
        real_fsync = os.fsync
        real_replace = os.replace

        def tracking_fsync(descriptor: int) -> None:
            kind = "directory-fsync" if stat.S_ISDIR(os.fstat(descriptor).st_mode) else "file-fsync"
            events.append(kind)
            real_fsync(descriptor)

        def tracking_replace(source: str, destination: str | os.PathLike[str]) -> None:
            events.append("replace")
            real_replace(source, destination)

        with (
            mock.patch("uusb.state.os.fsync", side_effect=tracking_fsync),
            mock.patch("uusb.state.os.replace", side_effect=tracking_replace),
        ):
            atomic_write_json(path, {"durable": True})

        self.assertEqual(events, ["file-fsync", "replace", "directory-fsync"])

    def test_failed_replace_preserves_old_file_and_cleans_temporary(self) -> None:
        path = secure_state_dir(self.root) / "atomic.json"
        atomic_write_json(path, {"old": True})
        original = path.read_bytes()

        with mock.patch("uusb.state.os.replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                atomic_write_json(path, {"old": False})

        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(tuple(self.root.iterdir()), (path,))

    def test_missing_read_and_remove_are_idempotent(self) -> None:
        path = secure_state_dir(self.root) / "missing.json"
        marker = object()
        self.assertIs(read_json(path, marker), marker)
        remove_state_file(path)
        self.assertFalse(path.exists())

    def test_symlinks_and_nonregular_files_are_refused(self) -> None:
        root = secure_state_dir(self.root)
        regular = root / "regular.json"
        regular.write_text("{}", encoding="utf-8")
        symbolic = root / "symbolic.json"
        symbolic.symlink_to(regular)
        fifo = root / "fifo.json"
        os.mkfifo(fifo)
        directory = root / "directory.json"
        directory.mkdir()

        for unsafe in (symbolic, fifo, directory):
            with self.subTest(path=unsafe, operation="read"):
                with self.assertRaises(StateSecurityError):
                    read_json(unsafe, None)
            with self.subTest(path=unsafe, operation="write"):
                with self.assertRaises(StateSecurityError):
                    atomic_write_json(unsafe, {})
            with self.subTest(path=unsafe, operation="remove"):
                with self.assertRaises(StateSecurityError):
                    remove_state_file(unsafe)

        linked_root = self.directory / "linked-state"
        linked_root.symlink_to(root, target_is_directory=True)
        with self.assertRaises(StateSecurityError):
            secure_state_dir(linked_root)


class OtpStoreTests(TemporaryStateTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.path = self.root / "otp.json"
        self.store = OtpStore(self.path)

    def test_empty_store_has_no_default_credentials_or_state_file(self) -> None:
        self.assertEqual(
            self.store.status(), {"selected": None, "credentials": []}
        )
        self.assertFalse(self.path.exists())

    def test_rfc4226_sha1_vectors_and_explicit_durable_commits(self) -> None:
        expected = (
            "755224",
            "287082",
            "359152",
            "969429",
            "338314",
            "254676",
            "287922",
            "162583",
            "399871",
            "520489",
        )
        self.store.provision(
            "rfc-hotp", "hotp", RFC_SECRET, digits=6, counter=0
        )
        self.store.select("rfc-hotp")

        for counter, code in enumerate(expected):
            with self.subTest(counter=counter):
                before_generation = self.path.read_bytes()
                self.assertEqual(self.store.generate_selected(), code)
                self.assertEqual(self.path.read_bytes(), before_generation)
                self.assertEqual(
                    self.store.commit_hotp("rfc-hotp", counter), counter + 1
                )

        self.assertEqual(
            self.store.status()["credentials"][0]["counter"], len(expected)
        )

    def test_rfc6238_sha1_vectors_and_generation_never_mutates(self) -> None:
        vectors = {
            59: "94287082",
            1_111_111_109: "07081804",
            1_111_111_111: "14050471",
            1_234_567_890: "89005924",
            2_000_000_000: "69279037",
            20_000_000_000: "65353130",
        }
        self.store.provision(
            "rfc-totp", "totp", RFC_SECRET, digits=8, period=30
        )
        self.store.select("rfc-totp")
        persisted = self.path.read_bytes()

        for instant, code in vectors.items():
            with self.subTest(instant=instant):
                self.assertEqual(self.store.generate_selected(now=instant), code)
                self.assertEqual(self.path.read_bytes(), persisted)

    def test_commit_requires_current_expected_counter_and_successful_save(self) -> None:
        self.store.provision(
            "counter", "hotp", RFC_SECRET, digits=6, counter=7
        )
        original = self.path.read_bytes()

        with self.assertRaises(OtpConflictError):
            self.store.commit_hotp("counter", 6)
        self.assertEqual(self.path.read_bytes(), original)

        with mock.patch("uusb.otp.atomic_write_json", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                self.store.commit_hotp("counter", 7)
        self.assertEqual(self.path.read_bytes(), original)
        reopened = OtpStore(self.path)
        self.assertEqual(reopened.status()["credentials"][0]["counter"], 7)

    def test_status_never_exposes_secret_and_selection_is_explicit(self) -> None:
        self.store.provision(
            "account", "totp", RFC_SECRET, digits=6, period=30
        )
        status = self.store.status()
        self.assertIsNone(status["selected"])
        self.assertNotIn(RFC_SECRET, repr(status))
        self.assertNotIn("secret", repr(status).lower())

        self.store.select("account")
        self.assertEqual(self.store.status()["selected"], "account")
        self.store.remove("account")
        self.assertEqual(
            self.store.status(), {"selected": None, "credentials": []}
        )

    def test_strict_base32_digits_period_and_counter_bounds(self) -> None:
        invalid_secrets = (
            "",
            RFC_SECRET.lower(),
            f"{RFC_SECRET}=",
            f"{RFC_SECRET} ",
            "A",
            "NOT-BASE32",
        )
        for index, secret in enumerate(invalid_secrets):
            with self.subTest(secret=secret):
                with self.assertRaises(OtpValidationError):
                    self.store.provision(
                        f"invalid-{index}",
                        "totp",
                        secret,
                        digits=6,
                        period=30,
                    )

        for digits in (5, 9, True):
            with self.subTest(digits=digits):
                with self.assertRaises(OtpValidationError):
                    self.store.provision(
                        f"digits-{digits}",
                        "totp",
                        RFC_SECRET,
                        digits=digits,
                        period=30,
                    )
        for period in (0, 86401, True):
            with self.subTest(period=period):
                with self.assertRaises(OtpValidationError):
                    self.store.provision(
                        f"period-{period}",
                        "totp",
                        RFC_SECRET,
                        digits=6,
                        period=period,
                    )
        for counter in (-1, UINT64_MAX + 1, True):
            with self.subTest(counter=counter):
                with self.assertRaises(OtpValidationError):
                    self.store.provision(
                        f"counter-{counter}",
                        "hotp",
                        RFC_SECRET,
                        digits=6,
                        counter=counter,
                    )

    def test_kind_specific_values_are_required_and_not_cross_applied(self) -> None:
        with self.assertRaises(OtpValidationError):
            self.store.provision(
                "totp", "totp", RFC_SECRET, digits=6
            )
        with self.assertRaises(OtpValidationError):
            self.store.provision(
                "hotp", "hotp", RFC_SECRET, digits=6
            )
        with self.assertRaises(OtpValidationError):
            self.store.provision(
                "totp-counter", "totp", RFC_SECRET, digits=6, period=30, counter=0
            )
        with self.assertRaises(OtpValidationError):
            self.store.provision(
                "hotp-period", "hotp", RFC_SECRET, digits=6, counter=0, period=30
            )


class HidPrimitiveTests(unittest.TestCase):
    def test_us_key_names_and_exact_keyboard_reports(self) -> None:
        self.assertEqual(KEYBOARD_KEYS["a"], 0x04)
        self.assertEqual(KEYBOARD_KEYS["1"], 0x1E)
        self.assertEqual(KEYBOARD_KEYS["left-shift"], 0xE1)
        self.assertEqual(
            keyboard_report(["left-shift", "a", "1"]),
            bytes((0x02, 0, 0x04, 0x1E, 0, 0, 0, 0)),
        )
        self.assertEqual(encode_text("aA1!"), (
            (0, 0x04),
            (0x02, 0x04),
            (0, 0x1E),
            (0x02, 0x1E),
        ))

    def test_text_reports_release_every_key_and_reject_unmappable_text(self) -> None:
        reports = text_reports("aA")
        self.assertEqual(
            reports,
            (
                bytes((0, 0, 0x04, 0, 0, 0, 0, 0)),
                EMPTY_KEYBOARD_REPORT,
                bytes((0x02, 0, 0x04, 0, 0, 0, 0, 0)),
                EMPTY_KEYBOARD_REPORT,
            ),
        )
        with self.assertRaises(ValueError):
            text_reports("é")

    def test_held_keyboard_rejects_seventh_key_without_mutating(self) -> None:
        state = KeyboardState()
        six = state.key_down(["a", "b", "c", "d", "e", "f"])
        self.assertEqual(len(six), 8)
        with self.assertRaises(ValueError):
            state.key_down("g")
        self.assertEqual(state.report, six)

        shifted = state.key_down("left-shift")
        self.assertEqual(shifted[0], 0x02)
        self.assertEqual(state.key_up(["left-shift", "c"])[2:8], bytes((4, 5, 7, 8, 9, 0)))

    def test_tap_reports_preserve_existing_held_state(self) -> None:
        state = KeyboardState()
        held = state.key_down(["left-control", "a"])
        pressed, released = state.tap_reports("b")
        self.assertEqual(pressed, bytes((1, 0, 4, 5, 0, 0, 0, 0)))
        self.assertEqual(released, held)
        self.assertEqual(state.report, held)

    def test_tap_duration_bounds_are_inclusive(self) -> None:
        self.assertEqual(validate_tap_duration(10), 10)
        self.assertEqual(validate_tap_duration(5000), 5000)
        for invalid in (9, 5001, True, 1.5):
            with self.subTest(value=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    validate_tap_duration(invalid)

    def test_signed8_and_mouse_report_splitting_are_deterministic(self) -> None:
        self.assertEqual(split_signed8(300), (127, 127, 46))
        self.assertEqual(split_signed8(-300), (-128, -128, -44))
        self.assertEqual(
            split_mouse_reports(3, 300, -129, 1),
            (
                bytes((3, 127, 128, 1)),
                bytes((3, 127, 255, 0)),
                bytes((3, 46, 0, 0)),
            ),
        )
        self.assertEqual(split_mouse_reports(0), (bytes(4),))

    def test_mouse_button_state_is_applied_to_every_movement_chunk(self) -> None:
        state = MouseState()
        self.assertEqual(state.button_down(["left", "middle"]), bytes((5, 0, 0, 0)))
        reports = state.move_reports(128, 0, 0)
        self.assertEqual(reports, (bytes((5, 127, 0, 0)), bytes((5, 1, 0, 0))))
        self.assertEqual(state.button_up("left"), bytes((4, 0, 0, 0)))
        self.assertEqual(state.release_all(), bytes(4))

    def test_consumer_usages_are_named_and_little_endian(self) -> None:
        self.assertEqual(CONSUMER_CONTROLS["volume-up"], 0xE9)
        self.assertEqual(consumer_report("volume-up"), b"\xe9\x00")
        with self.assertRaises(ValueError):
            consumer_report("eject")

    def test_otp_keyboard_reports_are_digits_with_full_releases(self) -> None:
        reports = keyboard_reports("10")
        self.assertEqual(
            reports,
            (
                bytes((0, 0, 0x1E, 0, 0, 0, 0, 0)),
                EMPTY_KEYBOARD_REPORT,
                bytes((0, 0, 0x27, 0, 0, 0, 0, 0)),
                EMPTY_KEYBOARD_REPORT,
            ),
        )
        with self.assertRaises(OtpValidationError):
            keyboard_reports("12\n")


if __name__ == "__main__":
    unittest.main()
