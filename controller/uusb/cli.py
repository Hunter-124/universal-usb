"""Argparse command surface for the Universal USB controller daemon."""

from __future__ import annotations

import argparse
import base64
import binascii
import getpass
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final, TextIO

from .doctor import display_doctor_safety
from .rpc import RpcClient, RpcError, RpcProtocolError, RpcRemoteError, RpcUnavailable
from .safety import SafetyAcknowledgementState
from .scenario import ScenarioError, load_scenario

EXIT_SUCCESS: Final = 0
EXIT_USAGE: Final = 2
EXIT_UNAVAILABLE: Final = 3
EXIT_PROFILE: Final = 4
EXIT_MAILBOX: Final = 5
EXIT_FLASH: Final = 6
EXIT_REJECTED: Final = 7
EXIT_VERIFICATION: Final = 8
PROFILES: Final = ("hid-msc", "microphone", "webcam", "security-token")


class CliInputError(ValueError):
    """A secret or cross-option constraint failed after argparse."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uusb")
    commands = parser.add_subparsers(dest="command", required=True)

    _leaf(commands, "doctor", "doctor")
    status = _leaf(commands, "status", "status")
    status.add_argument("--json", action="store_true", dest="json_output")
    events = _leaf(commands, "events", "events")
    events.add_argument("--json", action="store_true", dest="json_output")

    profile = commands.add_parser("profile")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    _leaf(profile_commands, "list", "profile.list")
    _leaf(profile_commands, "show", "profile.show")
    profile_set = _leaf(profile_commands, "set", "profile.set")
    profile_set.add_argument("profile", choices=PROFILES)
    profile_set.add_argument("--yes", action="store_true", required=True)

    hid = commands.add_parser("hid")
    hid_commands = hid.add_subparsers(dest="hid_command", required=True)
    key = hid_commands.add_parser("key")
    key_commands = key.add_subparsers(dest="key_command", required=True)
    for operation in ("tap", "down", "up"):
        key_operation = _leaf(key_commands, operation, "hid.key")
        key_operation.set_defaults(operation=operation)
        key_operation.add_argument("keys", metavar="KEY", nargs="+")
        key_operation.add_argument(
            "--hold-ms", type=_bounded_int("hold milliseconds", 10, 5_000)
        )
    text = _leaf(hid_commands, "text", "hid.text")
    text.add_argument("text")
    text.add_argument(
        "--interval-ms", type=_bounded_int("interval milliseconds", 0, 5_000)
    )

    mouse = hid_commands.add_parser("mouse")
    mouse_commands = mouse.add_subparsers(dest="mouse_command", required=True)
    move = _leaf(mouse_commands, "move", "hid.mouse")
    move.set_defaults(operation="move")
    move.add_argument("--dx", type=_bounded_int("dx", -1_000_000, 1_000_000), required=True)
    move.add_argument("--dy", type=_bounded_int("dy", -1_000_000, 1_000_000), required=True)
    button = mouse_commands.add_parser("button")
    button_commands = button.add_subparsers(dest="button_command", required=True)
    for operation in ("down", "up", "click"):
        button_operation = _leaf(button_commands, operation, "hid.mouse")
        button_operation.set_defaults(operation=f"button.{operation}")
        button_operation.add_argument("button", choices=("left", "right", "middle"))
    scroll = _leaf(mouse_commands, "scroll", "hid.mouse")
    scroll.set_defaults(operation="scroll")
    scroll.add_argument("amount", type=_bounded_int("scroll", -1_000_000, 1_000_000))

    consumer = hid_commands.add_parser("consumer")
    consumer_commands = consumer.add_subparsers(dest="consumer_command", required=True)
    for operation in ("tap", "down", "up"):
        consumer_operation = _leaf(consumer_commands, operation, "hid.consumer")
        consumer_operation.set_defaults(operation=operation)
        consumer_operation.add_argument(
            "control",
            choices=(
                "volume-up", "volume-down", "mute", "play-pause",
                "next", "previous", "stop",
            ),
        )

    msc = commands.add_parser("msc")
    msc_commands = msc.add_subparsers(dest="msc_command", required=True)
    attach = _leaf(msc_commands, "attach", "msc.attach")
    attach.add_argument("image", type=Path)
    attach.add_argument("--read-write", action="store_true")
    _leaf(msc_commands, "detach", "msc.detach")
    _leaf(msc_commands, "status", "msc.status")

    mic = commands.add_parser("mic")
    mic_commands = mic.add_subparsers(dest="mic_command", required=True)
    tone = _leaf(mic_commands, "tone", "mic.tone")
    tone.add_argument(
        "--frequency-hz", type=_bounded_int("frequency", 20, 20_000), required=True
    )
    tone.add_argument("--level-dbfs", type=_bounded_float("level", -96.0, 0.0))
    _leaf(mic_commands, "silence", "mic.silence")
    _leaf(mic_commands, "status", "mic.status")

    cam = commands.add_parser("cam")
    cam_commands = cam.add_subparsers(dest="cam_command", required=True)
    pattern = _leaf(cam_commands, "pattern", "cam.pattern")
    pattern.add_argument("pattern", choices=("bars", "checker", "gradient"))
    _leaf(cam_commands, "status", "cam.status")

    scenario = commands.add_parser("scenario")
    scenario_commands = scenario.add_subparsers(dest="scenario_command", required=True)
    validate = _leaf(scenario_commands, "validate", "scenario.validate")
    validate.add_argument("file", type=Path)
    run = _leaf(scenario_commands, "run", "scenario.run")
    run.add_argument("file", type=Path)
    run.add_argument("--switch-profile", action="store_true")
    run.add_argument("--json", action="store_true", dest="json_output")

    token = commands.add_parser("token")
    token_commands = token.add_subparsers(dest="token_command", required=True)
    _leaf(token_commands, "status", "token.status")
    _leaf(token_commands, "touch", "token.touch")
    reset = _leaf(token_commands, "reset", "token.reset")
    reset.add_argument("--yes", action="store_true", required=True)
    pin = token_commands.add_parser("pin")
    pin_commands = pin.add_subparsers(dest="pin_command", required=True)
    _leaf(pin_commands, "set", "token.pin.set")
    credential = token_commands.add_parser("credential")
    credential_commands = credential.add_subparsers(
        dest="credential_command", required=True
    )
    _leaf(credential_commands, "list", "token.credential.list")
    credential_delete = _leaf(
        credential_commands, "delete", "token.credential.delete"
    )
    credential_delete.add_argument("identifier")

    otp = token_commands.add_parser("otp")
    otp_commands = otp.add_subparsers(dest="otp_command", required=True)
    provision = _leaf(otp_commands, "provision", "token.otp.provision")
    provision.add_argument("name")
    provision.add_argument("--type", dest="otp_type", choices=("totp", "hotp"), default="totp")
    provision.add_argument("--digits", type=int, choices=(6, 7, 8), default=6)
    provision.add_argument("--period", type=_bounded_int("period", 1, 3600), default=30)
    provision.add_argument(
        "--counter", type=_bounded_int("counter", 0, (1 << 63) - 1), default=0
    )
    otp_remove = _leaf(otp_commands, "remove", "token.otp.remove")
    otp_remove.add_argument("name")
    otp_select = _leaf(otp_commands, "select", "token.otp.select")
    otp_select.add_argument("name")
    return parser


def command_to_rpc(
    arguments: argparse.Namespace,
    *,
    secret_reader: Callable[[str], str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Map parsed CLI state to exactly one shared contract method."""

    method = arguments.rpc_method
    params: dict[str, Any] = {}
    if method == "profile.set":
        params = {"profile": arguments.profile, "yes": bool(arguments.yes)}
    elif method == "hid.key":
        params = {"operation": arguments.operation, "keys": list(arguments.keys)}
        if arguments.hold_ms is not None:
            params["hold_ms"] = arguments.hold_ms
    elif method == "hid.text":
        params = {"text": arguments.text}
        if arguments.interval_ms is not None:
            params["interval_ms"] = arguments.interval_ms
    elif method == "hid.mouse":
        params = {"operation": arguments.operation}
        if arguments.operation == "move":
            params.update(dx=arguments.dx, dy=arguments.dy)
        elif arguments.operation == "scroll":
            params["amount"] = arguments.amount
        else:
            params["button"] = arguments.button
    elif method == "hid.consumer":
        params = {"operation": arguments.operation, "control": arguments.control}
    elif method == "msc.attach":
        params = {
            "image": str(arguments.image.expanduser().absolute()),
            "read_write": bool(arguments.read_write),
        }
    elif method == "mic.tone":
        params = {"frequency_hz": arguments.frequency_hz}
        if arguments.level_dbfs is not None:
            params["level_dbfs"] = arguments.level_dbfs
    elif method == "cam.pattern":
        params = {"pattern": arguments.pattern}
    elif method in {"scenario.validate", "scenario.run"}:
        load_scenario(arguments.file)
        params = {"file": str(arguments.file.expanduser().absolute())}
        if method == "scenario.run":
            params["switch_profile"] = bool(arguments.switch_profile)
    elif method == "token.reset":
        params = {"yes": bool(arguments.yes)}
    elif method == "token.pin.set":
        reader = _read_secret if secret_reader is None else secret_reader
        pin = reader("New test-token PIN: ")
        if not 4 <= len(pin) <= 16 or not pin.isascii():
            raise CliInputError("PIN must contain 4..16 ASCII characters")
        params = {"pin": pin}
    elif method == "token.credential.delete":
        params = {"credential_id": arguments.identifier}
    elif method == "token.otp.provision":
        reader = _read_secret if secret_reader is None else secret_reader
        secret = _normalize_base32_secret(reader("OTP secret (Base32): "))
        if arguments.otp_type == "totp" and arguments.counter != 0:
            raise CliInputError("--counter is valid only with --type hotp")
        if arguments.otp_type == "hotp" and arguments.period != 30:
            raise CliInputError("--period is valid only with --type totp")
        params = {
            "name": arguments.name,
            "kind": arguments.otp_type,
            "secret": secret,
            "digits": arguments.digits,
        }
        if arguments.otp_type == "totp":
            params["period"] = arguments.period
        else:
            params["counter"] = arguments.counter
    elif method in {"token.otp.remove", "token.otp.select"}:
        params = {"name": arguments.name}
    return method, params


def main(
    argv: Sequence[str] | None = None,
    *,
    client: RpcClient | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
        if arguments.rpc_method == "doctor":
            display_doctor_safety(SafetyAcknowledgementState(), errors)
        method, params = command_to_rpc(arguments)
        if method == "scenario.validate":
            _print_result(
                {"valid": True, "file": params["file"]},
                output,
                json_output=False,
            )
            return EXIT_SUCCESS
        rpc_client = RpcClient() if client is None else client
        result = rpc_client.call(method, params)
    except (CliInputError, ScenarioError) as error:
        print(f"uusb: {error}", file=errors)
        return EXIT_USAGE
    except RpcRemoteError as error:
        print(f"uusb: {error.message}", file=errors)
        return _remote_exit(error.code)
    except (RpcUnavailable, RpcProtocolError, RpcError) as error:
        print(f"uusb: {error}", file=errors)
        return EXIT_UNAVAILABLE

    _print_result(result, output, json_output=bool(getattr(arguments, "json_output", False)))
    return EXIT_SUCCESS


def _leaf(subparsers: Any, name: str, method: str) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name)
    parser.set_defaults(rpc_method=method)
    return parser


def _bounded_int(label: str, minimum: int, maximum: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            number = int(value, 10)
        except ValueError as error:
            raise argparse.ArgumentTypeError(f"{label} must be an integer") from error
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"{label} must be between {minimum} and {maximum}"
            )
        return number

    return parse


def _bounded_float(
    label: str, minimum: float, maximum: float
) -> Callable[[str], float]:
    def parse(value: str) -> float:
        try:
            number = float(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError(f"{label} must be a number") from error
        if number != number or not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"{label} must be between {minimum:g} and {maximum:g}"
            )
        return number

    return parse


def _read_secret(prompt: str) -> str:
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    value = sys.stdin.readline()
    if not value:
        raise CliInputError("secret input ended before a value was read")
    return value.rstrip("\r\n")


def _normalize_base32_secret(secret: str) -> str:
    normalized = "".join(secret.split()).upper().rstrip("=")
    if not 1 <= len(normalized) <= 1024:
        raise CliInputError("OTP secret must be non-empty bounded Base32")
    padded = normalized + "=" * ((-len(normalized)) % 8)
    try:
        decoded = base64.b32decode(padded, casefold=False)
    except (binascii.Error, ValueError) as error:
        raise CliInputError("OTP secret is not valid Base32") from error
    if not decoded:
        raise CliInputError("OTP secret must decode to at least one byte")
    return normalized


def _remote_exit(code: int | str) -> int:
    if isinstance(code, int) and not isinstance(code, bool) and EXIT_USAGE <= code <= EXIT_VERIFICATION:
        return code
    normalized = str(code).strip().lower().replace("_", ".").replace("-", ".")
    if normalized in {"daemon.unavailable", "stlink.unavailable", "unavailable"}:
        return EXIT_UNAVAILABLE
    if normalized.startswith("profile.mismatch") or normalized.startswith("capability."):
        return EXIT_PROFILE
    if normalized.startswith(("mailbox.", "crc.", "liveness.")):
        return EXIT_MAILBOX
    if normalized.startswith(("flash.", "profile.verification")):
        return EXIT_FLASH
    if normalized.startswith(("firmware.", "media.", "rejected")):
        return EXIT_REJECTED
    if normalized.startswith(("verification.", "post.action.")):
        return EXIT_VERIFICATION
    return EXIT_REJECTED


def _print_result(result: Any, stream: TextIO, *, json_output: bool) -> None:
    if json_output:
        print(
            json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
            file=stream,
        )
    elif result is None:
        return
    elif isinstance(result, str):
        print(result, file=stream)
    elif isinstance(result, list) and all(isinstance(item, str) for item in result):
        for item in result:
            print(item, file=stream)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), file=stream)


if __name__ == "__main__":
    raise SystemExit(main())
