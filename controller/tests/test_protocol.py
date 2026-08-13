from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uusb.crc32c import CRC32C_CHECK, crc32c
from uusb.protocol import (
    FIELD_OFFSETS,
    MAILBOX_SIZE,
    Header,
    ProtocolError,
    TokenTransport,
    validate_reserved_regions,
)


GOLDEN_HEADER = bytes((
    0x55, 0x53, 0x42, 0x31, 0x01, 0x00, 0x00, 0x10,
    0x03, 0x02, 0x01, 0x00, 0x04, 0x00, 0x00, 0x00,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
))

GOLDEN_TOKEN_REQUEST_PREFIX = bytes((
    0x04, 0x03, 0x02, 0x01, 0x02, 0x00, 0x03, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x11, 0x22, 0x33, 0x44,
    0x61, 0x62, 0x63,
))

EXPECTED_OFFSETS = {
    "header.magic": 0x000, "header.abi_version": 0x004,
    "header.layout_size": 0x006, "header.fw_version": 0x008,
    "header.profile": 0x00C, "header.boot_counter": 0x010,
    "header.uptime_ms": 0x014, "header.usb_flags": 0x018,
    "header.host_heartbeat": 0x01C, "header.last_error": 0x020,
    "header.control_completed": 0x024, "header.media_epoch": 0x028,
    "header.block_completed": 0x02C, "header.reserved": 0x030,
    "control.host_seq": 0x040, "control.opcode": 0x044,
    "control.request_length": 0x046, "control.request_flags": 0x048,
    "control.request_crc": 0x04C, "control.request_payload": 0x050,
    "control.host_ack": 0x0C0, "control.response_status": 0x0C4,
    "control.response_length": 0x0C8, "control.reserved": 0x0CA,
    "control.response_crc": 0x0CC, "control.response_payload": 0x0D0,
    "block.request_seq": 0x140, "block.epoch": 0x144,
    "block.op": 0x148, "block.reserved": 0x149,
    "block.lba": 0x14C, "block.data_length": 0x150,
    "block.request_crc": 0x154, "block.data": 0x158,
    "block.response_seq": 0x358, "block.response_status": 0x35C,
    "block.response_crc": 0x360,
    "token_request.request_seq": 0x400, "token_request.transport": 0x404,
    "token_request.length": 0x406, "token_request.flags": 0x408,
    "token_request.request_crc": 0x40C, "token_request.data": 0x410,
    "token_request.request_ack": 0x610,
    "token_response.response_seq": 0x640,
    "token_response.request_seq": 0x644, "token_response.length": 0x648,
    "token_response.reserved": 0x64A, "token_response.response_crc": 0x64C,
    "token_response.data": 0x650, "token_response.response_ack": 0x850,
}


class ProtocolGoldenTests(unittest.TestCase):
    def test_crc32c_check_vector(self) -> None:
        self.assertEqual(crc32c(b"123456789"), CRC32C_CHECK)
        self.assertEqual(CRC32C_CHECK, 0xE3069283)

    def test_every_named_offset_is_frozen(self) -> None:
        self.assertEqual(FIELD_OFFSETS, EXPECTED_OFFSETS)

    def test_explicit_header_vector(self) -> None:
        header = Header.unpack(GOLDEN_HEADER)
        self.assertEqual(header.fw_version, 0x00010203)
        self.assertEqual(header.profile, 4)
        self.assertEqual(header.boot_counter, 1)
        self.assertEqual(header.usb_flags, 1)
        self.assertEqual(header.media_epoch, 1)

    def test_explicit_token_prefix_is_little_endian(self) -> None:
        prefix = GOLDEN_TOKEN_REQUEST_PREFIX
        self.assertEqual(int.from_bytes(prefix[0:4], "little"), 0x01020304)
        self.assertEqual(int.from_bytes(prefix[4:6], "little"), TokenTransport.CCID)
        self.assertEqual(int.from_bytes(prefix[6:8], "little"), 3)
        self.assertEqual(prefix[16:19], b"abc")

    def test_reserved_boundaries_are_explicit(self) -> None:
        image = bytearray(MAILBOX_SIZE)
        validate_reserved_regions(image)
        for offset in (0x030, 0x03F, 0x364, 0x3FF, 0x614, 0x63F,
                       0x854, 0xFFF):
            with self.subTest(offset=offset):
                image[offset] = 1
                with self.assertRaises(ProtocolError):
                    validate_reserved_regions(image)
                image[offset] = 0


if __name__ == "__main__":
    unittest.main()
