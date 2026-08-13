"""Authoritative little-endian mirror of the 4096-byte SWD mailbox ABI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag
from struct import Struct, pack

from .crc32c import crc32c_parts

MAILBOX_ADDRESS = 0x20000000
MAILBOX_SIZE = 0x1000
MAILBOX_MAGIC = 0x31425355
ABI_VERSION = 1
CONTROL_PAYLOAD_SIZE = 112
BLOCK_DATA_SIZE = 512
TOKEN_DATA_SIZE = 512

HEADER_OFFSET = 0x000
HEADER_SIZE = 0x040
CONTROL_OFFSET = 0x040
CONTROL_SIZE = 0x100
BLOCK_OFFSET = 0x140
BLOCK_SIZE = 0x224
RESERVED_0364_OFFSET = 0x364
RESERVED_0364_SIZE = 0x09C
TOKEN_REQUEST_OFFSET = 0x400
TOKEN_REQUEST_SIZE = 0x214
RESERVED_0614_OFFSET = 0x614
RESERVED_0614_SIZE = 0x02C
TOKEN_RESPONSE_OFFSET = 0x640
TOKEN_RESPONSE_SIZE = 0x214
RESERVED_0854_OFFSET = 0x854
RESERVED_0854_SIZE = 0x7AC

FIELD_OFFSETS = {
    "header.magic": 0x000,
    "header.abi_version": 0x004,
    "header.layout_size": 0x006,
    "header.fw_version": 0x008,
    "header.profile": 0x00C,
    "header.boot_counter": 0x010,
    "header.uptime_ms": 0x014,
    "header.usb_flags": 0x018,
    "header.host_heartbeat": 0x01C,
    "header.last_error": 0x020,
    "header.control_completed": 0x024,
    "header.media_epoch": 0x028,
    "header.block_completed": 0x02C,
    "header.reserved": 0x030,
    "control.host_seq": 0x040,
    "control.opcode": 0x044,
    "control.request_length": 0x046,
    "control.request_flags": 0x048,
    "control.request_crc": 0x04C,
    "control.request_payload": 0x050,
    "control.host_ack": 0x0C0,
    "control.response_status": 0x0C4,
    "control.response_length": 0x0C8,
    "control.reserved": 0x0CA,
    "control.response_crc": 0x0CC,
    "control.response_payload": 0x0D0,
    "block.request_seq": 0x140,
    "block.epoch": 0x144,
    "block.op": 0x148,
    "block.reserved": 0x149,
    "block.lba": 0x14C,
    "block.data_length": 0x150,
    "block.request_crc": 0x154,
    "block.data": 0x158,
    "block.response_seq": 0x358,
    "block.response_status": 0x35C,
    "block.response_crc": 0x360,
    "token_request.request_seq": 0x400,
    "token_request.transport": 0x404,
    "token_request.length": 0x406,
    "token_request.flags": 0x408,
    "token_request.request_crc": 0x40C,
    "token_request.data": 0x410,
    "token_request.request_ack": 0x610,
    "token_response.response_seq": 0x640,
    "token_response.request_seq": 0x644,
    "token_response.length": 0x648,
    "token_response.reserved": 0x64A,
    "token_response.response_crc": 0x64C,
    "token_response.data": 0x650,
    "token_response.response_ack": 0x850,
}

RESERVED_RANGES = (
    (0x030, 0x040),
    (0x364, 0x400),
    (0x614, 0x640),
    (0x854, 0x1000),
)


class Profile(IntEnum):
    HID_MSC = 1
    MICROPHONE = 2
    WEBCAM = 3
    SECURITY_TOKEN = 4


class UsbFlag(IntFlag):
    CLOCK_VALID = 1 << 0
    MOUNTED = 1 << 1
    SUSPENDED = 1 << 2
    MEDIA_PRESENT = 1 << 3
    MEDIA_WRITABLE = 1 << 4
    HID_RELEASE_PENDING = 1 << 5
    BLOCK_PENDING = 1 << 6


class Opcode(IntEnum):
    GET_INFO = 0x0001
    RELEASE_ALL = 0x0002
    HID_KEYBOARD = 0x0100
    HID_MOUSE = 0x0101
    HID_CONSUMER = 0x0102
    MEDIA_ATTACH = 0x0200
    MEDIA_DETACH = 0x0201
    MIC_CONFIG = 0x0300
    UVC_PATTERN = 0x0400
    RESET = 0x7F00


class Status(IntEnum):
    OK = 0
    BAD_ABI = 1
    BAD_LENGTH = 2
    BAD_CRC = 3
    BAD_OPCODE = 4
    WRONG_PROFILE = 5
    BAD_STATE = 6
    OUT_OF_RANGE = 7
    BUSY = 8
    TIMEOUT = 9
    IO_ERROR = 10
    RESET_DURING_COMMAND = 11


class BlockOperation(IntEnum):
    READ = 1
    WRITE = 2
    FLUSH = 3


class TokenTransport(IntEnum):
    FIDO_HID = 1
    CCID = 2


class ProtocolError(ValueError):
    """A mailbox image violates the frozen ABI."""


_HEADER = Struct("<IHH10I4I")
_CONTROL_REQUEST_METADATA = Struct("<HHI")
_CONTROL_RESPONSE_METADATA = Struct("<IHH")
_BLOCK_REQUEST_METADATA = Struct("<IB3xII")
_U32 = Struct("<I")
_TOKEN_REQUEST_METADATA = Struct("<HHI")
_TOKEN_RESPONSE_METADATA = Struct("<IHH")

if _HEADER.size != HEADER_SIZE:
    raise RuntimeError("header struct does not occupy 0x40 bytes")
if _BLOCK_REQUEST_METADATA.size != 16:
    raise RuntimeError("block request metadata does not occupy 16 bytes")


@dataclass(frozen=True, slots=True)
class Header:
    magic: int
    abi_version: int
    layout_size: int
    fw_version: int
    profile: int
    boot_counter: int
    uptime_ms: int
    usb_flags: int
    host_heartbeat: int
    last_error: int
    control_completed: int
    media_epoch: int
    block_completed: int

    @classmethod
    def unpack(cls, data: bytes | bytearray | memoryview, *, validate: bool = True) -> "Header":
        raw = bytes(data)
        if len(raw) != HEADER_SIZE:
            raise ProtocolError("header must be exactly 64 bytes")
        values = _HEADER.unpack(raw)
        header = cls(*values[:13])
        if validate:
            if header.magic != MAILBOX_MAGIC:
                raise ProtocolError("mailbox magic mismatch")
            if header.abi_version != ABI_VERSION:
                raise ProtocolError("mailbox ABI version mismatch")
            if header.layout_size != MAILBOX_SIZE:
                raise ProtocolError("mailbox layout size mismatch")
            if any(values[13:]):
                raise ProtocolError("header reserved words are nonzero")
            try:
                Profile(header.profile)
            except ValueError as error:
                raise ProtocolError("unknown firmware profile") from error
        return header


@dataclass(frozen=True, slots=True)
class ControlResponse:
    sequence: int
    status: Status
    payload: bytes


@dataclass(frozen=True, slots=True)
class BlockRequest:
    sequence: int
    epoch: int
    operation: BlockOperation
    lba: int
    data_length: int
    data: bytes

@dataclass(frozen=True, slots=True)
class BlockResponse:
    sequence: int
    status: Status
    data: bytes


@dataclass(frozen=True, slots=True)
class TokenRequest:
    sequence: int
    transport: TokenTransport
    data: bytes
    acknowledged_sequence: int


@dataclass(frozen=True, slots=True)
class TokenResponse:
    sequence: int
    request_sequence: int
    data: bytes
    acknowledged_sequence: int


def firmware_version(major: int, minor: int, patch: int) -> int:
    if not 0 <= major <= 0xFFFF or not 0 <= minor <= 0xFF or not 0 <= patch <= 0xFF:
        raise ProtocolError("firmware version component out of range")
    return major << 16 | minor << 8 | patch


def control_request_crc(opcode: int | Opcode, payload: bytes, flags: int = 0) -> int:
    data = bytes(payload)
    if len(data) > CONTROL_PAYLOAD_SIZE:
        raise ProtocolError("control request exceeds 112 bytes")
    if not 0 <= flags <= 0xFFFFFFFF:
        raise ProtocolError("control request flags out of range")
    metadata = _CONTROL_REQUEST_METADATA.pack(int(opcode), len(data), flags)
    return crc32c_parts((metadata, data))


def control_response_crc(status: int | Status, payload: bytes, reserved: int = 0) -> int:
    data = bytes(payload)
    if len(data) > CONTROL_PAYLOAD_SIZE:
        raise ProtocolError("control response exceeds 112 bytes")
    metadata = _CONTROL_RESPONSE_METADATA.pack(int(status), len(data), reserved)
    return crc32c_parts((metadata, data))


def pack_control_request(sequence: int, opcode: int | Opcode, payload: bytes) -> bytes:
    data = bytes(payload)
    if not 0 <= sequence <= 0xFFFFFFFF:
        raise ProtocolError("control sequence out of range")
    validate_control_payload(Opcode(opcode), data)
    body = bytearray(CONTROL_SIZE // 2)
    _U32.pack_into(body, 0, sequence)
    _CONTROL_REQUEST_METADATA.pack_into(body, 4, int(opcode), len(data), 0)
    _U32.pack_into(body, 12, control_request_crc(opcode, data))
    body[16 : 16 + len(data)] = data
    return bytes(body)


def pack_control_request_body(opcode: int | Opcode, payload: bytes) -> bytes:
    """Return bytes 0x044..0x0bf; publish host_seq separately and last."""
    return pack_control_request(0, opcode, payload)[4:]


def parse_control_response(data: bytes | bytearray | memoryview, sequence: int) -> ControlResponse:
    raw = bytes(data)
    if len(raw) != CONTROL_SIZE - 0x80:
        raise ProtocolError("control response half must be exactly 128 bytes")
    ack, status_value, length, reserved, expected_crc = Struct("<IIHHI").unpack_from(raw)
    if ack != sequence:
        raise ProtocolError("control ACK sequence mismatch")
    if length > CONTROL_PAYLOAD_SIZE:
        raise ProtocolError("control response length exceeds 112 bytes")
    if reserved != 0:
        raise ProtocolError("control response reserved field is nonzero")
    try:
        status = Status(status_value)
    except ValueError as error:
        raise ProtocolError("unknown control response status") from error
    payload = raw[16 : 16 + length]
    if control_response_crc(status, payload, reserved) != expected_crc:
        raise ProtocolError("control response CRC mismatch")
    return ControlResponse(ack, status, payload)


def block_request_crc(epoch: int, operation: int | BlockOperation, lba: int,
                      data_length: int, data: bytes = b"") -> int:
    op = BlockOperation(operation)
    payload = bytes(data)
    _validate_u32(epoch, "block epoch")
    _validate_u32(lba, "block LBA")
    if op in (BlockOperation.READ, BlockOperation.WRITE):
        if data_length != BLOCK_DATA_SIZE:
            raise ProtocolError("READ/WRITE block length must be 512")
    elif data_length != 0:
        raise ProtocolError("FLUSH block length must be zero")
    if op is BlockOperation.WRITE:
        if len(payload) != BLOCK_DATA_SIZE:
            raise ProtocolError("WRITE block payload must be 512 bytes")
    elif payload:
        raise ProtocolError("only WRITE request CRC includes block data")
    metadata = _BLOCK_REQUEST_METADATA.pack(epoch, op, lba, data_length)
    return crc32c_parts((metadata, payload))


def block_response_crc(status: int | Status, operation: int | BlockOperation,
                       data_length: int, data: bytes = b"") -> int:
    result = Status(status)
    op = BlockOperation(operation)
    payload = bytes(data)
    parts: list[bytes] = [_U32.pack(result)]
    if result is Status.OK and op is BlockOperation.READ:
        if data_length != BLOCK_DATA_SIZE or len(payload) != BLOCK_DATA_SIZE:
            raise ProtocolError("successful READ response must contain 512 bytes")
        parts.extend((_U32.pack(data_length), payload))
    elif payload:
        raise ProtocolError("only successful READ response CRC includes data")
    return crc32c_parts(parts)

def parse_block_request(data: bytes | bytearray | memoryview) -> BlockRequest:
    raw = bytes(data)
    if len(raw) != BLOCK_SIZE:
        raise ProtocolError("block slot size mismatch")
    sequence, epoch, operation_value, lba, data_length, expected_crc = \
        Struct("<IIB3xIII").unpack_from(raw)
    if raw[9:12] != b"\0\0\0":
        raise ProtocolError("block request reserved bytes are nonzero")
    try:
        operation = BlockOperation(operation_value)
    except ValueError as error:
        raise ProtocolError("unknown block operation") from error
    payload = raw[24:24 + data_length] if operation is BlockOperation.WRITE else b""
    actual_crc = block_request_crc(epoch, operation, lba, data_length, payload)
    if actual_crc != expected_crc:
        raise ProtocolError("block request CRC mismatch")
    return BlockRequest(sequence, epoch, operation, lba, data_length, payload)


def parse_block_response(
    data: bytes | bytearray | memoryview,
    request: BlockRequest,
) -> BlockResponse:
    raw = bytes(data)
    if len(raw) != BLOCK_SIZE:
        raise ProtocolError("block slot size mismatch")
    sequence, status_value, expected_crc = Struct("<III").unpack_from(raw, 0x218)
    if sequence != request.sequence:
        raise ProtocolError("block response sequence mismatch")
    try:
        status = Status(status_value)
    except ValueError as error:
        raise ProtocolError("unknown block response status") from error
    payload = (
        raw[24:24 + request.data_length]
        if status is Status.OK and request.operation is BlockOperation.READ
        else b""
    )
    if block_response_crc(
        status, request.operation, request.data_length, payload
    ) != expected_crc:
        raise ProtocolError("block response CRC mismatch")
    return BlockResponse(sequence, status, payload)


def token_request_crc(transport: int | TokenTransport, data: bytes, flags: int = 0) -> int:
    token_transport = TokenTransport(transport)
    payload = bytes(data)
    if len(payload) > TOKEN_DATA_SIZE:
        raise ProtocolError("token request exceeds 512 bytes")
    if flags != 0:
        raise ProtocolError("token request flags must be zero in ABI v1")
    return crc32c_parts((_TOKEN_REQUEST_METADATA.pack(token_transport, len(payload), flags), payload))


def token_response_crc(request_sequence: int, data: bytes, reserved: int = 0) -> int:
    _validate_u32(request_sequence, "token request sequence")
    payload = bytes(data)
    if len(payload) > TOKEN_DATA_SIZE:
        raise ProtocolError("token response exceeds 512 bytes")
    if reserved != 0:
        raise ProtocolError("token response reserved field must be zero")
    return crc32c_parts((_TOKEN_RESPONSE_METADATA.pack(request_sequence, len(payload), reserved), payload))


def parse_token_request(data: bytes | bytearray | memoryview) -> TokenRequest:
    raw = bytes(data)
    if len(raw) != TOKEN_REQUEST_SIZE:
        raise ProtocolError("token request slot size mismatch")
    sequence, transport_value, length, flags, expected_crc = Struct("<IHHII").unpack_from(raw)
    if length > TOKEN_DATA_SIZE or flags != 0:
        raise ProtocolError("invalid token request metadata")
    try:
        transport = TokenTransport(transport_value)
    except ValueError as error:
        raise ProtocolError("unknown token transport") from error
    payload = raw[16 : 16 + length]
    if token_request_crc(transport, payload, flags) != expected_crc:
        raise ProtocolError("token request CRC mismatch")
    acknowledged = _U32.unpack_from(raw, 0x210)[0]
    return TokenRequest(sequence, transport, payload, acknowledged)


def parse_token_response(data: bytes | bytearray | memoryview) -> TokenResponse:
    raw = bytes(data)
    if len(raw) != TOKEN_RESPONSE_SIZE:
        raise ProtocolError("token response slot size mismatch")
    sequence, request_sequence, length, reserved, expected_crc = Struct("<IIHHI").unpack_from(raw)
    if length > TOKEN_DATA_SIZE or reserved != 0:
        raise ProtocolError("invalid token response metadata")
    payload = raw[16 : 16 + length]
    if token_response_crc(request_sequence, payload, reserved) != expected_crc:
        raise ProtocolError("token response CRC mismatch")
    acknowledged = _U32.unpack_from(raw, 0x210)[0]
    return TokenResponse(sequence, request_sequence, payload, acknowledged)


def pack_token_response_body(request_sequence: int, data: bytes) -> bytes:
    """Return bytes 0x644..0x84f; publish response_seq separately and last."""
    payload = bytes(data)
    if len(payload) > TOKEN_DATA_SIZE:
        raise ProtocolError("token response exceeds 512 bytes")
    body = bytearray(TOKEN_RESPONSE_SIZE - 8)
    _TOKEN_RESPONSE_METADATA.pack_into(
        body, 0, request_sequence, len(payload), 0
    )
    _U32.pack_into(body, 8, token_response_crc(request_sequence, payload))
    body[12:12 + len(payload)] = payload
    return bytes(body)

def validate_control_payload(opcode: Opcode, payload: bytes,
                             profile: Profile | None = None) -> None:
    data = bytes(payload)
    if profile is not None and not opcode_allowed_for_profile(opcode, profile):
        raise ProtocolError("control opcode is unavailable for active profile")
    expected = {
        Opcode.GET_INFO: 0,
        Opcode.RELEASE_ALL: 0,
        Opcode.HID_KEYBOARD: 8,
        Opcode.HID_MOUSE: 4,
        Opcode.HID_CONSUMER: 2,
        Opcode.MEDIA_ATTACH: 8,
        Opcode.MEDIA_DETACH: 0,
        Opcode.MIC_CONFIG: 6,
        Opcode.UVC_PATTERN: 1,
        Opcode.RESET: 0,
    }[opcode]
    if len(data) != expected:
        raise ProtocolError(f"{opcode.name} payload must be {expected} bytes")
    if opcode is Opcode.HID_KEYBOARD and data[1] != 0:
        raise ProtocolError("keyboard reserved byte must be zero")
    if opcode is Opcode.HID_MOUSE and data[0] & ~0x07:
        raise ProtocolError("mouse button mask contains unsupported bits")
    if opcode is Opcode.HID_CONSUMER and int.from_bytes(data, "little") not in {
        0x0000, 0x00E9, 0x00EA, 0x00E2, 0x00CD, 0x00B5, 0x00B6, 0x00B7
    }:
        raise ProtocolError("unsupported consumer usage")
    if opcode is Opcode.MEDIA_ATTACH:
        block_count = int.from_bytes(data[:4], "little")
        if block_count == 0 or data[4] > 1:
            raise ProtocolError("invalid media geometry or writable flag")
        if any(data[5:8]):
            raise ProtocolError("media reserved bytes must be zero")
    if opcode is Opcode.MIC_CONFIG:
        frequency = int.from_bytes(data[2:4], "little")
        amplitude = int.from_bytes(data[4:6], "little")
        if data[1] != 0:
            raise ProtocolError("microphone reserved byte must be zero")
        if data[0] > 1 or not 20 <= frequency <= 20_000 or amplitude > 32_767:
            raise ProtocolError("microphone setting out of range")
    if opcode is Opcode.UVC_PATTERN and data[0] > 2:
        raise ProtocolError("camera pattern out of range")


def opcode_allowed_for_profile(opcode: Opcode, profile: Profile) -> bool:
    if opcode in (Opcode.GET_INFO, Opcode.RELEASE_ALL, Opcode.RESET):
        return True
    if opcode is Opcode.HID_KEYBOARD:
        return profile in (Profile.HID_MSC, Profile.SECURITY_TOKEN)
    if opcode in (Opcode.HID_MOUSE, Opcode.HID_CONSUMER,
                  Opcode.MEDIA_ATTACH, Opcode.MEDIA_DETACH):
        return profile is Profile.HID_MSC
    if opcode is Opcode.MIC_CONFIG:
        return profile is Profile.MICROPHONE
    if opcode is Opcode.UVC_PATTERN:
        return profile is Profile.WEBCAM
    return False


def validate_reserved_regions(image: bytes | bytearray | memoryview) -> None:
    raw = bytes(image)
    if len(raw) != MAILBOX_SIZE:
        raise ProtocolError("mailbox image must be exactly 4096 bytes")
    for start, end in RESERVED_RANGES:
        if any(raw[start:end]):
            raise ProtocolError(f"reserved mailbox range 0x{start:03x}..0x{end - 1:03x} is nonzero")
    if raw[0x0CA:0x0CC] != b"\0\0":
        raise ProtocolError("control response reserved field is nonzero")
    if raw[0x149:0x14C] != b"\0\0\0":
        raise ProtocolError("block reserved bytes are nonzero")
    if raw[0x64A:0x64C] != b"\0\0":
        raise ProtocolError("token response reserved field is nonzero")


def _validate_u32(value: int, name: str) -> None:
    if not 0 <= value <= 0xFFFFFFFF:
        raise ProtocolError(f"{name} is outside uint32 range")
