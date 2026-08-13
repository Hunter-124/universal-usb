from __future__ import annotations

import sys
from pathlib import Path
import subprocess
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uusb.openocd import (
    OpenOCDMalformedResponse,
    OpenOCDTransport,
    parse_read_memory_response,
)


class FakeStream:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeChild:
    def __init__(self, running: bool = True) -> None:
        self.stdin = FakeStream()
        self.stdout = FakeStream()
        self.stderr = None
        self.running = running
        self.terminated = 0
        self.killed = 0
        self.waited = 0

    def poll(self) -> int | None:
        return None if self.running else 0

    def terminate(self) -> None:
        self.terminated += 1
        self.running = False

    def kill(self) -> None:
        self.killed += 1
        self.running = False

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.waited += 1
        return 0


class ReadParserTests(unittest.TestCase):
    def test_accepts_exact_decimal_or_hex_uint32_list(self) -> None:
        self.assertEqual(
            parse_read_memory_response(b"0x00000001 4294967295\n", 2),
            (1, 0xFFFFFFFF),
        )

    def test_rejects_banner_partial_extra_noninteger_and_range(self) -> None:
        rejected = (
            (b"Open On-Chip Debugger 0x1", 1),
            (b"0x00000001", 2),
            (b"0x1 0x2", 1),
            (b"0x1 nope", 2),
            (b"0x", 1),
            (b"4294967296", 1),
            (b"+1", 1),
            (b"1_000", 1),
            (b"\xff", 1),
            (b"", 1),
        )
        for response, count in rejected:
            with self.subTest(response=response, count=count):
                with self.assertRaises(OpenOCDMalformedResponse):
                    parse_read_memory_response(response, count)


class LifecycleTests(unittest.TestCase):
    def test_persistent_argv_is_exact_and_non_halting(self) -> None:
        transport = OpenOCDTransport("relative.log")
        self.assertEqual(
            transport.persistent_argv,
            (
                "openocd", "-l", str(Path("relative.log").absolute()),
                "-f", "interface/stlink.cfg", "-f", "target/stm32f1x.cfg",
                "-c", "stm32f1x.cpu configure -work-area-phys 0x20001000 -work-area-size 0x4000",
                "-c", "gdb_port disabled", "-c", "telnet_port disabled",
                "-c", "tcl_port pipe", "-c", "init",
            ),
        )
        self.assertNotIn("halt", " ".join(transport.persistent_argv))

    def test_reap_targets_only_supplied_child(self) -> None:
        child = FakeChild()
        OpenOCDTransport._reap_child(child)  # type: ignore[arg-type]
        self.assertEqual(child.terminated, 1)
        self.assertEqual(child.killed, 0)
        self.assertEqual(child.waited, 1)
        self.assertTrue(child.stdin.closed)
        self.assertTrue(child.stdout.closed)

    def test_flash_uses_absolute_elf_and_defers_restart(self) -> None:
        calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

        def fake_run(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0)

        with tempfile.TemporaryDirectory() as directory:
            elf = Path(directory, "uusb-hid-msc.elf")
            elf.write_bytes(b"ELF")
            transport = OpenOCDTransport(Path(directory, "openocd.log"), run_factory=fake_run)
            old_child = FakeChild()
            transport._child = old_child  # type: ignore[assignment]
            transport.flash(elf)
            self.assertIsNone(transport._child)
            self.assertEqual(old_child.terminated, 1)
            argv = calls[0][0]
            self.assertEqual(
                argv[-4:],
                ("-c", f"program {elf.resolve()} verify reset", "-c", "shutdown"),
            )
            self.assertIn(
                "stm32f1x.cpu configure -work-area-phys 0x20001000 -work-area-size 0x4000",
                argv,
            )
            self.assertEqual(calls[0][1]["timeout"], 30.0)


if __name__ == "__main__":
    unittest.main()
