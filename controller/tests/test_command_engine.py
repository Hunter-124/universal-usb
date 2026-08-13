from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uusb.command_engine import CommandEngine, MailboxResetDuringCommand
from uusb.protocol import (
    MAILBOX_ADDRESS,
    MAILBOX_MAGIC,
    MAILBOX_SIZE,
    Opcode,
    Status,
    control_response_crc,
)


class MemoryTransport:
    def __init__(self, *, reset_on_trigger: bool = False) -> None:
        self.memory = bytearray(MAILBOX_SIZE)
        self.memory[0:4] = MAILBOX_MAGIC.to_bytes(4, "little")
        self.memory[4:6] = (1).to_bytes(2, "little")
        self.memory[6:8] = MAILBOX_SIZE.to_bytes(2, "little")
        self.memory[0x0C:0x10] = (4).to_bytes(4, "little")
        self.memory[0x10:0x14] = (7).to_bytes(4, "little")
        self.memory[0x18:0x1C] = (1).to_bytes(4, "little")
        self.reset_on_trigger = reset_on_trigger
        self.writes: list[tuple[str, int, bytes | tuple[int, ...]]] = []
        self.closed = False

    def _offset(self, address: int) -> int:
        return address - MAILBOX_ADDRESS

    def read_bytes(self, address: int, length: int, **kwargs: object) -> bytes:
        del kwargs
        offset = self._offset(address)
        return bytes(self.memory[offset:offset + length])

    def write_bytes(self, address: int, data: bytes, **kwargs: object) -> None:
        del kwargs
        raw = bytes(data)
        offset = self._offset(address)
        self.memory[offset:offset + len(raw)] = raw
        self.writes.append(("bytes", offset, raw))

    def read_words(self, address: int, count: int, **kwargs: object) -> tuple[int, ...]:
        del kwargs
        offset = self._offset(address)
        return tuple(int.from_bytes(self.memory[index:index + 4], "little")
                     for index in range(offset, offset + count * 4, 4))

    def write_words(self, address: int, words: tuple[int, ...], **kwargs: object) -> None:
        del kwargs
        values = tuple(words)
        offset = self._offset(address)
        for index, value in enumerate(values):
            self.memory[offset + index * 4:offset + index * 4 + 4] = value.to_bytes(4, "little")
        self.writes.append(("words", offset, values))
        if offset == 0x040:
            sequence = values[0]
            if self.reset_on_trigger:
                boot = int.from_bytes(self.memory[0x10:0x14], "little") + 1
                self.memory[0x10:0x14] = boot.to_bytes(4, "little")
                return
            self.memory[0x0C0:0x0C4] = sequence.to_bytes(4, "little")
            self.memory[0x0C4:0x0C8] = int(Status.OK).to_bytes(4, "little")
            self.memory[0x0C8:0x0CC] = b"\0\0\0\0"
            crc = control_response_crc(Status.OK, b"")
            self.memory[0x0CC:0x0D0] = crc.to_bytes(4, "little")

    def close(self) -> None:
        self.closed = True


class CommandEngineTests(unittest.TestCase):
    def test_request_fields_precede_sequence_trigger(self) -> None:
        transport = MemoryTransport()
        engine = CommandEngine(transport)  # type: ignore[arg-type]
        response = engine.submit(
            Opcode.HID_KEYBOARD,
            b"\x02\x00\x04\x00\x00\x00\x00\x00",
        )
        self.assertEqual(response.status, Status.OK)
        publications = [write for write in transport.writes if write[1] in (0x044, 0x040)]
        self.assertEqual([write[1] for write in publications], [0x044, 0x040])
        self.assertEqual(len(publications[0][2]), 124)
        self.assertEqual(publications[1][2], (1,))

    def test_reset_after_trigger_is_indeterminate_and_not_retried(self) -> None:
        transport = MemoryTransport(reset_on_trigger=True)
        engine = CommandEngine(transport)  # type: ignore[arg-type]
        with self.assertRaises(MailboxResetDuringCommand):
            engine.submit(Opcode.GET_INFO)
        trigger_writes = [write for write in transport.writes if write[1] == 0x040]
        self.assertEqual(len(trigger_writes), 1)

    def test_two_read_liveness_requires_advancing_uptime(self) -> None:
        transport = MemoryTransport()

        def advance(_: float) -> None:
            uptime = int.from_bytes(transport.memory[0x14:0x18], "little") + 10
            transport.memory[0x14:0x18] = uptime.to_bytes(4, "little")

        engine = CommandEngine(transport, sleep=advance)  # type: ignore[arg-type]
        result = engine.check_liveness()
        self.assertEqual(result.second.uptime_ms - result.first.uptime_ms, 10)

    def test_heartbeat_writes_only_host_owned_word(self) -> None:
        transport = MemoryTransport()
        engine = CommandEngine(transport)  # type: ignore[arg-type]
        self.assertEqual(engine.heartbeat_once(), 1)
        self.assertEqual(transport.writes[-1], ("words", 0x01C, (1,)))


if __name__ == "__main__":
    unittest.main()
