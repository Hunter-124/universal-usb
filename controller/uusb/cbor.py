"""Small, strict canonical CBOR codec for CTAP2 messages.

Only the data model used by CTAP2 is supported: integers, byte and UTF-8 text
strings, arrays, maps, booleans, and null.  Indefinite lengths, tags, floating
point values, non-minimal encodings, duplicate keys, and non-canonical map
ordering are rejected.  Both encoding and decoding have explicit resource
limits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MAX_CBOR_DEPTH = 16
MAX_CBOR_ITEMS = 1_024
MAX_CBOR_BYTES = 65_536
MAX_CBOR_STRING_BYTES = 16_384
_UINT64_MAX = (1 << 64) - 1


class CBORError(ValueError):
    """Base class for unsupported or malformed CBOR."""


class CBOREncodeError(CBORError):
    """A Python value cannot be represented by this canonical subset."""


class CBORDecodeError(CBORError):
    """Input is malformed, unsupported, or not canonically encoded."""


class CBORLimitError(CBORError):
    """Input or output exceeds a configured resource limit."""


@dataclass(slots=True)
class _Limits:
    max_depth: int
    max_items: int
    max_bytes: int
    max_string_bytes: int

    @classmethod
    def checked(
        cls,
        max_depth: int,
        max_items: int,
        max_bytes: int,
        max_string_bytes: int,
    ) -> "_Limits":
        values = (max_depth, max_items, max_bytes, max_string_bytes)
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("CBOR limits must be non-negative integers")
        return cls(max_depth, max_items, max_bytes, max_string_bytes)


def _argument(major: int, value: int) -> bytes:
    if value < 24:
        return bytes(((major << 5) | value,))
    if value <= 0xFF:
        return bytes(((major << 5) | 24, value))
    if value <= 0xFFFF:
        return bytes(((major << 5) | 25,)) + value.to_bytes(2, "big")
    if value <= 0xFFFFFFFF:
        return bytes(((major << 5) | 26,)) + value.to_bytes(4, "big")
    if value <= _UINT64_MAX:
        return bytes(((major << 5) | 27,)) + value.to_bytes(8, "big")
    raise CBOREncodeError("integer is outside the CBOR uint64 domain")


class _Encoder:
    def __init__(self, limits: _Limits) -> None:
        self.limits = limits
        self.items = 0

    def encode(self, value: Any, depth: int = 0) -> bytes:
        if depth > self.limits.max_depth:
            raise CBORLimitError("CBOR nesting depth exceeded")
        self.items += 1
        if self.items > self.limits.max_items:
            raise CBORLimitError("CBOR item count exceeded")

        value_type = type(value)
        if value is None:
            result = b"\xf6"
        elif value_type is bool:
            result = b"\xf5" if value else b"\xf4"
        elif value_type is int:
            if value >= 0:
                result = _argument(0, value)
            else:
                encoded = -1 - value
                if encoded > _UINT64_MAX:
                    raise CBOREncodeError("integer is outside the CBOR int64 domain")
                result = _argument(1, encoded)
        elif value_type is bytes:
            if len(value) > self.limits.max_string_bytes:
                raise CBORLimitError("CBOR byte string is too large")
            result = _argument(2, len(value)) + value
        elif value_type is str:
            try:
                encoded_text = value.encode("utf-8", "strict")
            except UnicodeEncodeError as error:
                raise CBOREncodeError("text contains an invalid Unicode scalar") from error
            if len(encoded_text) > self.limits.max_string_bytes:
                raise CBORLimitError("CBOR text string is too large")
            result = _argument(3, len(encoded_text)) + encoded_text
        elif value_type in (list, tuple):
            header = _argument(4, len(value))
            parts = [header]
            total = len(header)
            for item in value:
                encoded_item = self.encode(item, depth + 1)
                total += len(encoded_item)
                if total > self.limits.max_bytes:
                    raise CBORLimitError("CBOR output is too large")
                parts.append(encoded_item)
            result = b"".join(parts)
        elif value_type is dict:
            header = _argument(5, len(value))
            total = len(header)
            entries: list[tuple[bytes, bytes]] = []
            for key, item in value.items():
                if type(key) not in (int, bytes, str, bool, type(None)):
                    raise CBOREncodeError("unsupported non-scalar CBOR map key")
                encoded_key = self.encode(key, depth + 1)
                encoded_value = self.encode(item, depth + 1)
                total += len(encoded_key) + len(encoded_value)
                if total > self.limits.max_bytes:
                    raise CBORLimitError("CBOR output is too large")
                entries.append((encoded_key, encoded_value))
            entries.sort(key=lambda entry: (len(entry[0]), entry[0]))
            for index in range(1, len(entries)):
                if entries[index - 1][0] == entries[index][0]:
                    raise CBOREncodeError("map contains duplicate canonical keys")
            parts = [header]
            for encoded_key, encoded_value in entries:
                parts.extend((encoded_key, encoded_value))
            result = b"".join(parts)
        else:
            raise CBOREncodeError(f"unsupported CBOR type: {value_type.__name__}")

        if len(result) > self.limits.max_bytes:
            raise CBORLimitError("CBOR output is too large")
        return result


def dumps(
    value: Any,
    *,
    max_depth: int = MAX_CBOR_DEPTH,
    max_items: int = MAX_CBOR_ITEMS,
    max_bytes: int = MAX_CBOR_BYTES,
    max_string_bytes: int = MAX_CBOR_STRING_BYTES,
) -> bytes:
    """Encode *value* using deterministic RFC 8949 map-key ordering."""

    limits = _Limits.checked(max_depth, max_items, max_bytes, max_string_bytes)
    return _Encoder(limits).encode(value)


class _Decoder:
    def __init__(self, data: bytes, limits: _Limits) -> None:
        self.data = data
        self.limits = limits
        self.offset = 0
        self.items = 0

    def _take(self, length: int) -> bytes:
        end = self.offset + length
        if end > len(self.data):
            raise CBORDecodeError("truncated CBOR input")
        result = self.data[self.offset:end]
        self.offset = end
        return result

    def _read_argument(self, additional: int) -> int:
        if additional < 24:
            return additional
        widths = {24: 1, 25: 2, 26: 4, 27: 8}
        try:
            width = widths[additional]
        except KeyError as error:
            if additional == 31:
                raise CBORDecodeError("indefinite-length CBOR is not supported") from error
            raise CBORDecodeError("reserved CBOR additional information") from error
        value = int.from_bytes(self._take(width), "big")
        minimum = {1: 24, 2: 0x100, 4: 0x1_0000, 8: 0x1_0000_0000}[width]
        if value < minimum:
            raise CBORDecodeError("non-minimal CBOR integer or length")
        return value

    def decode(self, depth: int = 0) -> Any:
        if depth > self.limits.max_depth:
            raise CBORLimitError("CBOR nesting depth exceeded")
        self.items += 1
        if self.items > self.limits.max_items:
            raise CBORLimitError("CBOR item count exceeded")
        if self.offset >= len(self.data):
            raise CBORDecodeError("truncated CBOR input")

        initial = self.data[self.offset]
        self.offset += 1
        major = initial >> 5
        additional = initial & 0x1F

        if major == 7:
            if additional == 20:
                return False
            if additional == 21:
                return True
            if additional == 22:
                return None
            raise CBORDecodeError("unsupported CBOR simple or floating-point value")

        argument = self._read_argument(additional)
        if major == 0:
            return argument
        if major == 1:
            return -1 - argument
        if major in (2, 3):
            if argument > self.limits.max_string_bytes:
                raise CBORLimitError("CBOR string is too large")
            raw = self._take(argument)
            if major == 2:
                return raw
            try:
                return raw.decode("utf-8", "strict")
            except UnicodeDecodeError as error:
                raise CBORDecodeError("CBOR text is not valid UTF-8") from error
        if major == 4:
            if argument > self.limits.max_items:
                raise CBORLimitError("CBOR array is too large")
            return [self.decode(depth + 1) for _ in range(argument)]
        if major == 5:
            if argument > self.limits.max_items // 2:
                raise CBORLimitError("CBOR map is too large")
            result: dict[Any, Any] = {}
            previous_order: tuple[int, bytes] | None = None
            for _ in range(argument):
                key_start = self.offset
                key = self.decode(depth + 1)
                encoded_key = self.data[key_start:self.offset]
                order = (len(encoded_key), encoded_key)
                if previous_order is not None and order <= previous_order:
                    if order == previous_order:
                        raise CBORDecodeError("duplicate CBOR map key")
                    raise CBORDecodeError("CBOR map keys are not canonically ordered")
                previous_order = order
                try:
                    if key in result:
                        raise CBORDecodeError("duplicate CBOR map key")
                    result[key] = self.decode(depth + 1)
                except TypeError as error:
                    raise CBORDecodeError("unsupported non-scalar CBOR map key") from error
            return result
        raise CBORDecodeError("unsupported CBOR major type")


def loads(
    data: bytes | bytearray | memoryview,
    *,
    max_depth: int = MAX_CBOR_DEPTH,
    max_items: int = MAX_CBOR_ITEMS,
    max_bytes: int = MAX_CBOR_BYTES,
    max_string_bytes: int = MAX_CBOR_STRING_BYTES,
) -> Any:
    """Decode one complete canonical CBOR item and reject trailing bytes."""

    limits = _Limits.checked(max_depth, max_items, max_bytes, max_string_bytes)
    try:
        view = memoryview(data)
    except TypeError as error:
        raise CBORDecodeError("CBOR input must be bytes-like") from error
    if view.nbytes > limits.max_bytes:
        raise CBORLimitError("CBOR input is too large")
    raw = view.tobytes()
    decoder = _Decoder(raw, limits)
    result = decoder.decode()
    if decoder.offset != len(raw):
        raise CBORDecodeError("trailing bytes after CBOR item")
    return result


encode = dumps
decode = loads


__all__ = [
    "CBORDecodeError",
    "CBOREncodeError",
    "CBORError",
    "CBORLimitError",
    "MAX_CBOR_BYTES",
    "MAX_CBOR_DEPTH",
    "MAX_CBOR_ITEMS",
    "MAX_CBOR_STRING_BYTES",
    "decode",
    "dumps",
    "encode",
    "loads",
]
