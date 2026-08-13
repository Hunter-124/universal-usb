"""Closed version-1 scenario validation, preflight, and serial execution."""

from __future__ import annotations

import copy
import json
import os
import stat
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from .hid import CONSUMER_CONTROLS as HID_CONSUMER_CONTROLS
from .hid import KEYBOARD_KEYS
from .media import BLOCK_SIZE, UINT32_MAX

MAX_STEPS: Final = 1000
MAX_WAIT_MS: Final = 600_000
MAX_HOLD_MS: Final = 5_000
MAX_INTERVAL_MS: Final = 5_000
MAX_MOUSE_DELTA: Final = 1_000_000
MAX_SCROLL: Final = 1_000_000
PROFILES: Final = frozenset(
    {"hid-msc", "microphone", "webcam", "security-token"}
)
CONSUMER_CONTROLS: Final = frozenset(HID_CONSUMER_CONTROLS)
MOUSE_BUTTONS: Final = frozenset({"left", "right", "middle"})
CAMERA_PATTERNS: Final = frozenset({"bars", "checker", "gradient"})


class ScenarioError(RuntimeError):
    """Base scenario failure."""


class ScenarioValidationError(ScenarioError):
    """The JSON document is outside the closed version-1 grammar."""


class ScenarioPreflightError(ScenarioError):
    """A valid document cannot safely run in the current environment."""


class ScenarioExecutionError(ScenarioError):
    """A preflighted action failed at runtime."""


class RpcCaller(Protocol):
    def call(self, method: str, params: Mapping[str, Any] | None = None) -> Any: ...


@dataclass(frozen=True, slots=True)
class PreparedStep:
    action: str
    method: str | None
    params: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedScenario:
    document: Mapping[str, Any]
    steps: tuple[PreparedStep, ...]


def load_scenario(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read one bounded UTF-8 JSON file and validate its closed grammar."""

    source = Path(path)
    try:
        info = source.lstat()
    except OSError as error:
        raise ScenarioPreflightError(f"cannot inspect scenario file: {error}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ScenarioPreflightError("scenario must be a regular, non-symlink file")
    if info.st_size > 1024 * 1024:
        raise ScenarioPreflightError("scenario file exceeds 1 MiB")
    try:
        with source.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ScenarioValidationError(f"cannot read scenario JSON: {error}") from error
    validate_scenario(value)
    return value

def load_and_preflight(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load a file and finish every local preflight check for daemon execution."""

    source = Path(path).expanduser().absolute()
    document = load_scenario(source)
    prepared = preflight_scenario(document, base_directory=source.parent)
    normalized = copy.deepcopy(document)
    for raw, step in zip(normalized["steps"], prepared.steps, strict=True):
        if step.action == "msc.attach":
            raw["image"] = step.params["image"]
    return normalized


def validate_scenario(value: Any) -> None:
    """Validate exactly schema/scenario-v1.schema.json without jsonschema."""

    document = _object(value, "scenario")
    _exact_keys(
        document,
        required={"version", "profile", "steps"},
        optional={"name", "description"},
        where="scenario",
    )
    if _integer(document["version"], "scenario.version") != 1:
        raise ScenarioValidationError("scenario.version must be exactly 1")
    _enum(document["profile"], PROFILES, "scenario.profile")
    if "name" in document:
        _string(document["name"], "scenario.name", minimum=1, maximum=128)
    if "description" in document:
        _string(
            document["description"], "scenario.description", minimum=1, maximum=4096
        )
    steps = document["steps"]
    if not isinstance(steps, list):
        raise ScenarioValidationError("scenario.steps must be an array")
    if not steps or len(steps) > MAX_STEPS:
        raise ScenarioValidationError(
            f"scenario.steps must contain between 1 and {MAX_STEPS} steps"
        )
    for index, step in enumerate(steps):
        _validate_step(step, index)


def preflight_scenario(
    value: Any,
    *,
    base_directory: str | os.PathLike[str] | None = None,
    client: RpcCaller | None = None,
    switch_profile: bool = False,
) -> PreparedScenario:
    """Validate every key, image, bound, and profile before returning any action."""

    validate_scenario(value)
    if not isinstance(switch_profile, bool):
        raise ScenarioPreflightError("switch_profile must be a boolean")
    document: Mapping[str, Any] = value
    base = Path.cwd() if base_directory is None else Path(base_directory)
    prepared: list[PreparedStep] = []

    # Complete all local and filesystem checks before the first RPC or action.
    for index, raw in enumerate(document["steps"]):
        prepared.append(_prepare_step(raw, index, base))
    _validate_profile_capabilities(document["profile"], prepared)

    if client is not None:
        try:
            status = client.call("status", {})
        except Exception as error:
            raise ScenarioPreflightError("cannot obtain active profile") from error
        active = _active_profile(status)
        expected = document["profile"]
        if active != expected:
            if not switch_profile:
                raise ScenarioPreflightError(
                    f"scenario requires profile {expected!r}, active profile is {active!r}"
                )
            try:
                client.call("profile.set", {"profile": expected, "yes": True})
            except Exception as error:
                raise ScenarioPreflightError(
                    f"could not switch to required profile {expected!r}"
                ) from error
    return PreparedScenario(document, tuple(prepared))

def run_scenario(
    value: Any,
    client: RpcCaller | Callable[[str, Mapping[str, Any] | None], Any],
    *,
    base_directory: str | os.PathLike[str] | None = None,
    switch_profile: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Any]:
    """Preflight everything, then execute serially and release HID on failure."""

    prepared = preflight_scenario(
        value,
        base_directory=base_directory,
        client=client if hasattr(client, "call") else None,
        switch_profile=switch_profile,
    )
    results: list[Any] = []
    try:
        for step in prepared.steps:
            if step.action == "wait":
                sleep(step.params["milliseconds"] / 1000.0)
                results.append(None)
            elif step.action == "mark":
                results.append({"mark": step.params["label"]})
            else:
                assert step.method is not None
                results.append(_call(client, step.method, step.params))
    except Exception as error:
        try:
            _call(client, "hid.release_all", {})
        except Exception:
            pass
        raise ScenarioExecutionError(
            f"scenario stopped after {len(results)} completed steps"
        ) from error
    return results


def action_to_rpc(
    step: Mapping[str, Any],
    *,
    base_directory: str | os.PathLike[str] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Return the shared RPC method and params for one already-valid action."""

    _validate_step(step, 0)
    prepared = _prepare_step(
        step, 0, Path.cwd() if base_directory is None else Path(base_directory)
    )
    return prepared.method, dict(prepared.params)


def _validate_step(value: Any, index: int) -> None:
    step = _object(value, f"scenario.steps[{index}]")
    action = step.get("action")
    if not isinstance(action, str):
        raise ScenarioValidationError(f"scenario.steps[{index}].action must be a string")
    where = f"scenario.steps[{index}]"

    if action == "wait":
        _exact_keys(step, {"action", "milliseconds"}, set(), where)
        _bounded_integer(step["milliseconds"], f"{where}.milliseconds", 0, MAX_WAIT_MS)
    elif action == "mark":
        _exact_keys(step, {"action", "label"}, set(), where)
        _string(step["label"], f"{where}.label", minimum=1, maximum=128)
    elif action in {"hid.key.tap", "hid.key.down", "hid.key.up"}:
        optional = {"hold_ms"}
        _exact_keys(step, {"action", "keys"}, optional, where)
        _keys(step["keys"], f"{where}.keys")
        if "hold_ms" in step:
            _bounded_integer(step["hold_ms"], f"{where}.hold_ms", 10, MAX_HOLD_MS)
    elif action == "hid.text":
        _exact_keys(step, {"action", "text"}, {"interval_ms"}, where)
        _string(step["text"], f"{where}.text", minimum=1, maximum=4096)
        if "interval_ms" in step:
            _bounded_integer(
                step["interval_ms"], f"{where}.interval_ms", 0, MAX_INTERVAL_MS
            )
    elif action == "hid.mouse.move":
        _exact_keys(step, {"action", "dx", "dy"}, set(), where)
        _bounded_integer(step["dx"], f"{where}.dx", -MAX_MOUSE_DELTA, MAX_MOUSE_DELTA)
        _bounded_integer(step["dy"], f"{where}.dy", -MAX_MOUSE_DELTA, MAX_MOUSE_DELTA)
    elif action in {
        "hid.mouse.button.down",
        "hid.mouse.button.up",
        "hid.mouse.button.click",
    }:
        _exact_keys(step, {"action", "button"}, set(), where)
        _enum(step["button"], MOUSE_BUTTONS, f"{where}.button")
    elif action == "hid.mouse.scroll":
        _exact_keys(step, {"action", "amount"}, set(), where)
        _bounded_integer(step["amount"], f"{where}.amount", -MAX_SCROLL, MAX_SCROLL)
    elif action in {"hid.consumer.tap", "hid.consumer.down", "hid.consumer.up"}:
        _exact_keys(step, {"action", "control"}, set(), where)
        _enum(step["control"], _consumer_names(), f"{where}.control")
    elif action == "msc.attach":
        _exact_keys(step, {"action", "image"}, {"read_write"}, where)
        _string(step["image"], f"{where}.image", minimum=1, maximum=4096)
        if "read_write" in step and not isinstance(step["read_write"], bool):
            raise ScenarioValidationError(f"{where}.read_write must be a boolean")
    elif action in {"msc.detach", "mic.silence", "token.touch"}:
        _exact_keys(step, {"action"}, set(), where)
    elif action == "mic.tone":
        _exact_keys(step, {"action", "frequency_hz"}, {"level_dbfs"}, where)
        _bounded_integer(step["frequency_hz"], f"{where}.frequency_hz", 20, 20_000)
        if "level_dbfs" in step:
            _number(step["level_dbfs"], f"{where}.level_dbfs", -96.0, 0.0)
    elif action == "cam.pattern":
        _exact_keys(step, {"action", "pattern"}, set(), where)
        _enum(step["pattern"], CAMERA_PATTERNS, f"{where}.pattern")
    elif action == "token.otp.select":
        _exact_keys(step, {"action", "name"}, set(), where)
        _string(step["name"], f"{where}.name", minimum=1, maximum=128)
    else:
        raise ScenarioValidationError(f"{where}.action {action!r} is not supported")


def _prepare_step(step: Mapping[str, Any], index: int, base: Path) -> PreparedStep:
    action = step["action"]
    if action == "wait":
        return PreparedStep(action, None, {"milliseconds": step["milliseconds"]})
    if action == "mark":
        return PreparedStep(action, None, {"label": step["label"]})
    if action.startswith("hid.key."):
        params: dict[str, Any] = {
            "operation": action.rsplit(".", 1)[1],
            "keys": list(step["keys"]),
        }
        if "hold_ms" in step:
            params["hold_ms"] = step["hold_ms"]
        return PreparedStep(action, "hid.key", params)
    if action == "hid.text":
        params = {"text": step["text"]}
        if "interval_ms" in step:
            params["interval_ms"] = step["interval_ms"]
        return PreparedStep(action, "hid.text", params)
    if action == "hid.mouse.move":
        return PreparedStep(
            action, "hid.mouse", {"operation": "move", "dx": step["dx"], "dy": step["dy"]}
        )
    if action.startswith("hid.mouse.button."):
        return PreparedStep(
            action,
            "hid.mouse",
            {"operation": f"button.{action.rsplit('.', 1)[1]}", "button": step["button"]},
        )
    if action == "hid.mouse.scroll":
        return PreparedStep(
            action, "hid.mouse", {"operation": "scroll", "amount": step["amount"]}
        )
    if action.startswith("hid.consumer."):
        return PreparedStep(
            action,
            "hid.consumer",
            {"operation": action.rsplit(".", 1)[1], "control": step["control"]},
        )
    if action == "msc.attach":
        image = _preflight_image(step["image"], bool(step.get("read_write", False)), base, index)
        return PreparedStep(
            action,
            "msc.attach",
            {"image": os.fspath(image), "read_write": bool(step.get("read_write", False))},
        )
    if action in {"msc.detach", "mic.silence", "token.touch"}:
        return PreparedStep(action, action, {})
    if action == "mic.tone":
        params = {"frequency_hz": step["frequency_hz"]}
        if "level_dbfs" in step:
            params["level_dbfs"] = step["level_dbfs"]
        return PreparedStep(action, action, params)
    if action == "cam.pattern":
        return PreparedStep(action, action, {"pattern": step["pattern"]})
    if action == "token.otp.select":
        return PreparedStep(action, action, {"name": step["name"]})
    raise AssertionError(f"validated action was not mapped: {action}")


def _preflight_image(image: str, read_write: bool, base: Path, index: int) -> Path:
    candidate = Path(image).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    candidate = candidate.absolute()
    try:
        info = candidate.lstat()
    except OSError as error:
        raise ScenarioPreflightError(
            f"scenario.steps[{index}].image cannot be inspected: {error}"
        ) from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ScenarioPreflightError(
            f"scenario.steps[{index}].image must be a regular, non-symlink file"
        )
    if info.st_size <= 0 or info.st_size % BLOCK_SIZE:
        raise ScenarioPreflightError(
            f"scenario.steps[{index}].image must be non-empty and divisible by {BLOCK_SIZE}"
        )
    if info.st_size // BLOCK_SIZE > UINT32_MAX:
        raise ScenarioPreflightError(f"scenario.steps[{index}].image is too large")
    if read_write and not os.access(candidate, os.W_OK):
        raise ScenarioPreflightError(
            f"scenario.steps[{index}].image is not writable for read-write attachment"
        )
    if not os.access(candidate, os.R_OK):
        raise ScenarioPreflightError(f"scenario.steps[{index}].image is not readable")
    return candidate


def _validate_profile_capabilities(profile: str, steps: Sequence[PreparedStep]) -> None:
    for index, step in enumerate(steps):
        action = step.action
        expected: str | None = None
        if action.startswith("hid.") or action.startswith("msc."):
            expected = "hid-msc"
        elif action.startswith("mic."):
            expected = "microphone"
        elif action.startswith("cam."):
            expected = "webcam"
        elif action.startswith("token."):
            expected = "security-token"
        if expected is not None and profile != expected:
            raise ScenarioPreflightError(
                f"scenario.steps[{index}].action {action!r} requires profile {expected!r}"
            )


def _call(
    client: RpcCaller | Callable[[str, Mapping[str, Any] | None], Any],
    method: str,
    params: Mapping[str, Any],
) -> Any:
    if hasattr(client, "call"):
        return client.call(method, params)  # type: ignore[union-attr]
    return client(method, params)


def _active_profile(status: Any) -> str:
    if not isinstance(status, dict):
        raise ScenarioPreflightError("daemon status result must be an object")
    active = status.get("profile", status.get("active_profile"))
    if active not in PROFILES:
        raise ScenarioPreflightError("daemon status did not contain a known active profile")
    return active


def _keyboard_names() -> frozenset[str]:
    return frozenset(KEYBOARD_KEYS)


def _consumer_names() -> frozenset[str]:
    return CONSUMER_CONTROLS


def _keys(value: Any, where: str) -> None:
    if not isinstance(value, list) or not value or len(value) > 6:
        raise ScenarioValidationError(f"{where} must contain between 1 and 6 keys")
    supported = _keyboard_names()
    seen: set[str] = set()
    for index, key in enumerate(value):
        name = _string(key, f"{where}[{index}]", minimum=1, maximum=64)
        if name not in supported:
            raise ScenarioValidationError(f"{where}[{index}] is not a supported key")
        if name in seen:
            raise ScenarioValidationError(f"{where} must not contain duplicate keys")
        seen.add(name)


def _object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScenarioValidationError(f"{where} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise ScenarioValidationError(f"{where} property names must be strings")
    return value


def _exact_keys(
    value: Mapping[str, Any], required: set[str], optional: set[str], where: str
) -> None:
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise ScenarioValidationError(
            f"{where} is missing required properties: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise ScenarioValidationError(
            f"{where} has unknown properties: {', '.join(sorted(unknown))}"
        )


def _string(
    value: Any, where: str, *, minimum: int, maximum: int
) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ScenarioValidationError(
            f"{where} must be a string of {minimum}..{maximum} characters"
        )
    return value


def _integer(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioValidationError(f"{where} must be an integer")
    return value


def _bounded_integer(value: Any, where: str, minimum: int, maximum: int) -> int:
    integer = _integer(value, where)
    if not minimum <= integer <= maximum:
        raise ScenarioValidationError(f"{where} must be between {minimum} and {maximum}")
    return integer


def _number(value: Any, where: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioValidationError(f"{where} must be a number")
    number = float(value)
    if number != number or not minimum <= number <= maximum:
        raise ScenarioValidationError(f"{where} must be between {minimum} and {maximum}")
    return number


def _enum(value: Any, choices: frozenset[str], where: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ScenarioValidationError(
            f"{where} must be one of: {', '.join(sorted(choices))}"
        )
    return value
