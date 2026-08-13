from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uusb.daemon import ControllerDaemon
from uusb.protocol import TOKEN_REQUEST_SIZE
from uusb.rpc import prepare_socket_directory


class _IdleTokenTransport:
    def __init__(self) -> None:
        self.writes: list[tuple[object, ...]] = []

    def read_bytes(self, _address: int, size: int) -> bytes:
        self.assert_size(size)
        return bytes(size)

    def write_words(self, *args: object) -> None:
        self.writes.append(args)

    @staticmethod
    def assert_size(size: int) -> None:
        if size != TOKEN_REQUEST_SIZE:
            raise AssertionError(f"unexpected token slot size: {size}")


class HardwareRegressionTests(unittest.TestCase):
    def test_idle_zero_token_slot_is_not_parsed_or_acknowledged(self) -> None:
        transport = _IdleTokenTransport()
        daemon = object.__new__(ControllerDaemon)
        daemon.engine = SimpleNamespace(transport=transport)
        daemon._pending_token = None

        daemon._service_token_slot()

        self.assertIsNone(daemon._pending_token)
        self.assertEqual(transport.writes, [])

    def test_missing_owned_runtime_root_is_created_privately(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary, "runtime")
            socket = runtime / "universal-usb" / "control.sock"

            directory = prepare_socket_directory(socket, uid=os.geteuid())

            self.assertEqual(directory, socket.parent)
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
