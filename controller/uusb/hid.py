"""Deterministic USB HID report construction for a US keyboard and mouse."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType


KEYBOARD_REPORT_SIZE = 8
KEYBOARD_MAX_KEYS = 6
TAP_DURATION_MIN_MS = 10
TAP_DURATION_MAX_MS = 5000
MODIFIER_LEFT_CONTROL = 0x01
MODIFIER_LEFT_SHIFT = 0x02
MODIFIER_LEFT_ALT = 0x04
MODIFIER_LEFT_META = 0x08
MODIFIER_RIGHT_CONTROL = 0x10
MODIFIER_RIGHT_SHIFT = 0x20
MODIFIER_RIGHT_ALT = 0x40
MODIFIER_RIGHT_META = 0x80
EMPTY_KEYBOARD_REPORT = bytes(KEYBOARD_REPORT_SIZE)


_key_values: dict[str, int] = {
    **{chr(ord("a") + offset): 0x04 + offset for offset in range(26)},
    **{str(number): 0x1D + number for number in range(1, 10)},
    "0": 0x27,
    "enter": 0x28,
    "escape": 0x29,
    "backspace": 0x2A,
    "tab": 0x2B,
    "space": 0x2C,
    "minus": 0x2D,
    "equal": 0x2E,
    "left-bracket": 0x2F,
    "right-bracket": 0x30,
    "backslash": 0x31,
    "semicolon": 0x33,
    "apostrophe": 0x34,
    "grave": 0x35,
    "comma": 0x36,
    "period": 0x37,
    "slash": 0x38,
    "caps-lock": 0x39,
    **{f"f{number}": 0x39 + number for number in range(1, 13)},
    "print-screen": 0x46,
    "scroll-lock": 0x47,
    "pause": 0x48,
    "insert": 0x49,
    "home": 0x4A,
    "page-up": 0x4B,
    "delete": 0x4C,
    "end": 0x4D,
    "page-down": 0x4E,
    "right": 0x4F,
    "left": 0x50,
    "down": 0x51,
    "up": 0x52,
    "num-lock": 0x53,
    "keypad-divide": 0x54,
    "keypad-multiply": 0x55,
    "keypad-minus": 0x56,
    "keypad-plus": 0x57,
    "keypad-enter": 0x58,
    **{f"keypad-{number}": 0x58 + number for number in range(1, 10)},
    "keypad-0": 0x62,
    "keypad-period": 0x63,
    "application": 0x65,
    "power": 0x66,
    "keypad-equal": 0x67,
    "left-control": 0xE0,
    "left-shift": 0xE1,
    "left-alt": 0xE2,
    "left-meta": 0xE3,
    "right-control": 0xE4,
    "right-shift": 0xE5,
    "right-alt": 0xE6,
    "right-meta": 0xE7,
}
KEYBOARD_KEYS: Mapping[str, int] = MappingProxyType(_key_values)
_MODIFIER_USAGES = frozenset(range(0xE0, 0xE8))
_KEYBOARD_USAGES = frozenset(_key_values.values())

CONSUMER_CONTROLS: Mapping[str, int] = MappingProxyType(
    {
        "volume-up": 0x00E9,
        "volume-down": 0x00EA,
        "mute": 0x00E2,
        "play-pause": 0x00CD,
        "next": 0x00B5,
        "previous": 0x00B6,
        "stop": 0x00B7,
    }
)
MOUSE_BUTTONS: Mapping[str, int] = MappingProxyType(
    {"left": 0x01, "right": 0x02, "middle": 0x04}
)


_text_values: dict[str, tuple[int, int]] = {}
for _letter in "abcdefghijklmnopqrstuvwxyz":
    _usage = KEYBOARD_KEYS[_letter]
    _text_values[_letter] = (0, _usage)
    _text_values[_letter.upper()] = (MODIFIER_LEFT_SHIFT, _usage)
for _digit in "0123456789":
    _text_values[_digit] = (0, KEYBOARD_KEYS[_digit])
for _character, _key_name in {
    " ": "space",
    "\n": "enter",
    "\t": "tab",
    "-": "minus",
    "=": "equal",
    "[": "left-bracket",
    "]": "right-bracket",
    "\\": "backslash",
    ";": "semicolon",
    "'": "apostrophe",
    "`": "grave",
    ",": "comma",
    ".": "period",
    "/": "slash",
}.items():
    _text_values[_character] = (0, KEYBOARD_KEYS[_key_name])
for _character, _key_name in {
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "minus",
    "+": "equal",
    "{": "left-bracket",
    "}": "right-bracket",
    "|": "backslash",
    ":": "semicolon",
    '"': "apostrophe",
    "~": "grave",
    "<": "comma",
    ">": "period",
    "?": "slash",
}.items():
    _text_values[_character] = (MODIFIER_LEFT_SHIFT, KEYBOARD_KEYS[_key_name])
US_TEXT_KEYS: Mapping[str, tuple[int, int]] = MappingProxyType(_text_values)
del _character, _digit, _key_name, _letter, _usage

Key = str | int


def validate_tap_duration(milliseconds: int) -> int:
    """Return a valid HID tap duration, rejecting values outside 10..5000."""
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, int):
        raise TypeError("tap duration must be an integer")
    if not TAP_DURATION_MIN_MS <= milliseconds <= TAP_DURATION_MAX_MS:
        raise ValueError("tap duration must be between 10 and 5000 milliseconds")
    return milliseconds


def keyboard_report(
    keys: Key | Iterable[Key] = (),
    modifiers: int | Key | Iterable[Key] = 0,
) -> bytes:
    """Build one boot-keyboard report from at most six non-modifier keys."""
    modifier_mask = _resolve_modifier_mask(modifiers)
    usages: list[int] = []
    for usage in _resolve_keys(keys):
        if usage in _MODIFIER_USAGES:
            modifier_mask |= 1 << (usage - 0xE0)
        elif usage not in usages:
            usages.append(usage)
    return _pack_keyboard_report(modifier_mask, usages)


def encode_text(text: str) -> tuple[tuple[int, int], ...]:
    """Encode US-layout text as ``(modifier, usage)`` keystrokes."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    encoded: list[tuple[int, int]] = []
    for character in text:
        try:
            encoded.append(US_TEXT_KEYS[character])
        except KeyError as error:
            raise ValueError(f"character {character!r} is not available on the US layout") from error
    return tuple(encoded)


def text_reports(text: str) -> tuple[bytes, ...]:
    """Return a press and an all-keys release report for every character."""
    reports: list[bytes] = []
    for modifier, usage in encode_text(text):
        reports.append(_pack_keyboard_report(modifier, [usage]))
        reports.append(EMPTY_KEYBOARD_REPORT)
    return tuple(reports)


def split_signed8(value: int) -> tuple[int, ...]:
    """Split an integer deterministically into signed-eight-bit chunks."""
    _require_integer(value, "movement")
    if value == 0:
        return (0,)
    chunks: list[int] = []
    remaining = value
    while remaining:
        chunk = min(127, max(-128, remaining))
        chunks.append(chunk)
        remaining -= chunk
    return tuple(chunks)


def mouse_report(buttons: int, dx: int = 0, dy: int = 0, wheel: int = 0) -> bytes:
    """Build one four-byte mouse report from already bounded movement."""
    button_mask = _validate_buttons(buttons)
    for value, label in ((dx, "dx"), (dy, "dy"), (wheel, "wheel")):
        _require_integer(value, label)
        if not -128 <= value <= 127:
            raise ValueError(f"{label} must fit in a signed 8-bit integer")
    return bytes((button_mask, dx & 0xFF, dy & 0xFF, wheel & 0xFF))


def split_mouse_reports(
    buttons: int,
    dx: int = 0,
    dy: int = 0,
    wheel: int = 0,
) -> tuple[bytes, ...]:
    """Split arbitrary integral movement into deterministic mouse reports."""
    button_mask = _validate_buttons(buttons)
    for value, label in ((dx, "dx"), (dy, "dy"), (wheel, "wheel")):
        _require_integer(value, label)
    reports: list[bytes] = []
    remaining_x, remaining_y, remaining_wheel = dx, dy, wheel
    while not reports or remaining_x or remaining_y or remaining_wheel:
        step_x = min(127, max(-128, remaining_x))
        step_y = min(127, max(-128, remaining_y))
        step_wheel = min(127, max(-128, remaining_wheel))
        reports.append(mouse_report(button_mask, step_x, step_y, step_wheel))
        remaining_x -= step_x
        remaining_y -= step_y
        remaining_wheel -= step_wheel
    return tuple(reports)


def consumer_report(control: str | int) -> bytes:
    """Build a two-byte consumer-control report for one supported usage."""
    if isinstance(control, str):
        try:
            usage = CONSUMER_CONTROLS[control]
        except KeyError as error:
            raise ValueError(f"unknown consumer control {control!r}") from error
    else:
        _require_integer(control, "consumer usage")
        usage = control
        if usage not in CONSUMER_CONTROLS.values():
            raise ValueError("unsupported consumer usage")
    return usage.to_bytes(2, "little")


class KeyboardState:
    """Track held keyboard keys and construct atomic press/release reports."""

    def __init__(self) -> None:
        self._modifiers = 0
        self._keys: list[int] = []

    @property
    def report(self) -> bytes:
        return _pack_keyboard_report(self._modifiers, self._keys)

    def key_down(self, keys: Key | Iterable[Key]) -> bytes:
        modifier_mask = self._modifiers
        usages = list(self._keys)
        for usage in _resolve_keys(keys):
            if usage in _MODIFIER_USAGES:
                modifier_mask |= 1 << (usage - 0xE0)
            elif usage not in usages:
                usages.append(usage)
        report = _pack_keyboard_report(modifier_mask, usages)
        self._modifiers = modifier_mask
        self._keys = usages
        return report

    def key_up(self, keys: Key | Iterable[Key]) -> bytes:
        modifier_mask = self._modifiers
        usages = list(self._keys)
        for usage in _resolve_keys(keys):
            if usage in _MODIFIER_USAGES:
                modifier_mask &= ~(1 << (usage - 0xE0))
            elif usage in usages:
                usages.remove(usage)
        self._modifiers = modifier_mask
        self._keys = usages
        return self.report

    def tap_reports(self, keys: Key | Iterable[Key]) -> tuple[bytes, bytes]:
        modifier_mask = self._modifiers
        usages = list(self._keys)
        for usage in _resolve_keys(keys):
            if usage in _MODIFIER_USAGES:
                modifier_mask |= 1 << (usage - 0xE0)
            elif usage not in usages:
                usages.append(usage)
        pressed = _pack_keyboard_report(modifier_mask, usages)
        return pressed, self.report

    def release_all(self) -> bytes:
        self._modifiers = 0
        self._keys.clear()
        return EMPTY_KEYBOARD_REPORT


class MouseState:
    """Track held mouse buttons while constructing movement reports."""

    def __init__(self) -> None:
        self._buttons = 0

    @property
    def report(self) -> bytes:
        return mouse_report(self._buttons)

    def button_down(self, buttons: str | int | Iterable[str]) -> bytes:
        self._buttons |= _resolve_buttons(buttons)
        return self.report

    def button_up(self, buttons: str | int | Iterable[str]) -> bytes:
        self._buttons &= ~_resolve_buttons(buttons)
        return self.report

    def move_reports(
        self, dx: int = 0, dy: int = 0, wheel: int = 0
    ) -> tuple[bytes, ...]:
        return split_mouse_reports(self._buttons, dx, dy, wheel)

    def release_all(self) -> bytes:
        self._buttons = 0
        return self.report


def _resolve_keys(keys: Key | Iterable[Key]) -> tuple[int, ...]:
    values: Iterable[Key]
    if isinstance(keys, (str, int)):
        values = (keys,)
    else:
        values = keys
    resolved: list[int] = []
    for key in values:
        if isinstance(key, str):
            try:
                usage = KEYBOARD_KEYS[key]
            except KeyError as error:
                raise ValueError(f"unknown keyboard key {key!r}") from error
        else:
            _require_integer(key, "keyboard usage")
            usage = key
            if usage not in _KEYBOARD_USAGES:
                raise ValueError("unsupported keyboard usage")
        resolved.append(usage)
    return tuple(resolved)


def _resolve_modifier_mask(modifiers: int | Key | Iterable[Key]) -> int:
    if isinstance(modifiers, int):
        _require_integer(modifiers, "modifier mask")
        if not 0 <= modifiers <= 0xFF:
            raise ValueError("modifier mask must fit in one byte")
        return modifiers
    mask = 0
    for usage in _resolve_keys(modifiers):
        if usage not in _MODIFIER_USAGES:
            raise ValueError("modifier list contains a non-modifier key")
        mask |= 1 << (usage - 0xE0)
    return mask


def _pack_keyboard_report(modifier_mask: int, usages: list[int]) -> bytes:
    if len(usages) > KEYBOARD_MAX_KEYS:
        raise ValueError("a keyboard report cannot hold a seventh non-modifier key")
    return bytes((modifier_mask, 0, *usages, *(0 for _ in range(6 - len(usages)))))


def _resolve_buttons(buttons: str | int | Iterable[str]) -> int:
    if isinstance(buttons, int):
        return _validate_buttons(buttons)
    names = (buttons,) if isinstance(buttons, str) else buttons
    mask = 0
    for name in names:
        try:
            mask |= MOUSE_BUTTONS[name]
        except (KeyError, TypeError) as error:
            raise ValueError(f"unknown mouse button {name!r}") from error
    return mask


def _validate_buttons(buttons: int) -> int:
    _require_integer(buttons, "mouse buttons")
    if buttons & ~0x07 or buttons < 0:
        raise ValueError("mouse button mask contains unsupported bits")
    return buttons


def _require_integer(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
