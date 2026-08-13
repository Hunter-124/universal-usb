"""Table-free CRC-32C (Castagnoli) used by the SWD mailbox ABI."""

from __future__ import annotations

from collections.abc import Iterable

CRC32C_POLYNOMIAL = 0x1EDC6F41
CRC32C_REFLECTED_POLYNOMIAL = 0x82F63B78
CRC32C_INITIAL = 0xFFFFFFFF
CRC32C_XOROUT = 0xFFFFFFFF
CRC32C_CHECK = 0xE3069283
_UINT32_MASK = 0xFFFFFFFF


def _update(state: int, data: bytes | bytearray | memoryview) -> int:
    if not 0 <= state <= _UINT32_MASK:
        raise ValueError("CRC-32C state must be an unsigned 32-bit integer")
    view = memoryview(data).cast("B")
    crc = state
    for value in view:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (CRC32C_REFLECTED_POLYNOMIAL & -(crc & 1))
    return crc & _UINT32_MASK


def crc32c(data: bytes | bytearray | memoryview) -> int:
    """Return reflected CRC-32C with the ABI's initial value and xor-out."""

    return _update(CRC32C_INITIAL, data) ^ CRC32C_XOROUT


def crc32c_parts(parts: Iterable[bytes | bytearray | memoryview]) -> int:
    """CRC discontiguous ABI fields without concatenating them."""

    state = CRC32C_INITIAL
    for part in parts:
        state = _update(state, part)
    return state ^ CRC32C_XOROUT
