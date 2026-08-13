from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uusb.cli import (
    EXIT_PROFILE,
    build_parser,
    command_to_rpc,
    main,
)
from uusb.hid import CONSUMER_CONTROLS, KEYBOARD_KEYS
from uusb.rpc import (
    accept_same_uid,
    RpcPermissionError,
    RpcRemoteError,
    open_server_socket,
    receive_request,
    send_error,
    send_result,
    validate_client_socket,
)
from uusb.safety import WARNING_REVISION
from uusb.scenario import (
    ScenarioExecutionError,
    ScenarioPreflightError,
    ScenarioValidationError,
    preflight_scenario,
    run_scenario,
    validate_scenario,
)


class FakeClient:
    def __init__(self, profile: str = "hid-msc", fail_method: str | None = None) -> None:
        self.profile = profile
        self.fail_method = fail_method
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, method: str, params: object = None) -> object:
        values = dict(params or {})
        self.calls.append((method, values))
        if method == self.fail_method:
            raise RuntimeError("injected action failure")
        if method == "status":
            return {"profile": self.profile}
        return {"accepted": True}


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = build_parser()

    def mapped(self, words: list[str]) -> tuple[str, dict[str, object]]:
        return command_to_rpc(self.parser.parse_args(words), secret_reader=lambda _: "1234")

    def test_every_non_file_command_parses_and_maps(self) -> None:
        cases = [
            (["doctor"], "doctor"),
            (["status", "--json"], "status"),
            (["events", "--json"], "events"),
            (["profile", "list"], "profile.list"),
            (["profile", "show"], "profile.show"),
            (["profile", "set", "security-token", "--yes"], "profile.set"),
            (["hid", "key", "tap", "a", "--hold-ms", "10"], "hid.key"),
            (["hid", "key", "down", "left-control", "a"], "hid.key"),
            (["hid", "key", "up", "a"], "hid.key"),
            (["hid", "text", "hello", "--interval-ms", "1"], "hid.text"),
            (["hid", "mouse", "move", "--dx", "300", "--dy", "-2"], "hid.mouse"),
            (["hid", "mouse", "button", "down", "left"], "hid.mouse"),
            (["hid", "mouse", "button", "up", "right"], "hid.mouse"),
            (["hid", "mouse", "button", "click", "middle"], "hid.mouse"),
            (["hid", "mouse", "scroll", "-4"], "hid.mouse"),
            (["hid", "consumer", "tap", "play-pause"], "hid.consumer"),
            (["hid", "consumer", "down", "volume-up"], "hid.consumer"),
            (["hid", "consumer", "up", "next"], "hid.consumer"),
            (["msc", "attach", "disk.img", "--read-write"], "msc.attach"),
            (["msc", "detach"], "msc.detach"),
            (["msc", "status"], "msc.status"),
            (["mic", "tone", "--frequency-hz", "1000", "--level-dbfs", "-12"], "mic.tone"),
            (["mic", "silence"], "mic.silence"),
            (["mic", "status"], "mic.status"),
            (["cam", "pattern", "checker"], "cam.pattern"),
            (["cam", "status"], "cam.status"),
            (["token", "status"], "token.status"),
            (["token", "touch"], "token.touch"),
            (["token", "reset", "--yes"], "token.reset"),
            (["token", "pin", "set"], "token.pin.set"),
            (["token", "credential", "list"], "token.credential.list"),
            (["token", "credential", "delete", "credential-1"], "token.credential.delete"),
            (["token", "otp", "remove", "login"], "token.otp.remove"),
            (["token", "otp", "select", "login"], "token.otp.select"),
        ]
        for words, expected in cases:
            with self.subTest(words=words):
                method, _ = self.mapped(words)
                self.assertEqual(method, expected)

        args = self.parser.parse_args(
            ["token", "otp", "provision", "login", "--type", "hotp", "--counter", "9"]
        )
        method, params = command_to_rpc(
            args, secret_reader=lambda _: "JBSWY3DPEHPK3PXP"
        )
        self.assertEqual(method, "token.otp.provision")
        self.assertEqual(params["kind"], "hotp")
        self.assertEqual(params["counter"], 9)

    def test_scenario_commands_load_closed_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            path.write_text(
                json.dumps({"version": 1, "profile": "hid-msc", "steps": [{"action": "wait", "milliseconds": 0}]}),
                encoding="utf-8",
            )
            for words, expected in (
                (["scenario", "validate", str(path)], "scenario.validate"),
                (["scenario", "run", str(path), "--switch-profile", "--json"], "scenario.run"),
            ):
                with self.subTest(words=words):
                    method, params = self.mapped(words)
                    self.assertEqual(method, expected)
                    self.assertEqual(params["file"], str(path.absolute()))

    def test_doctor_prints_safety_before_rpc(self) -> None:
        class DoctorClient(FakeClient):
            def __init__(self, errors: object) -> None:
                super().__init__()
                self.errors = errors

            def call(self, method: str, params: object = None) -> object:
                self.assert_warning_present()
                return super().call(method, params)

            def assert_warning_present(self) -> None:
                if WARNING_REVISION not in self.errors.getvalue():
                    raise AssertionError("doctor RPC preceded the safety warning")

        import io

        errors = io.StringIO()
        client = DoctorClient(errors)
        self.assertEqual(main(["doctor"], client=client, stderr=errors), 0)
        self.assertEqual(client.calls[0][0], "doctor")

    def test_remote_profile_error_has_stable_exit(self) -> None:
        class RejectingClient(FakeClient):
            def call(self, method: str, params: object = None) -> object:
                raise RpcRemoteError("profile.mismatch", "wrong active profile")

        self.assertEqual(main(["status"], client=RejectingClient()), EXIT_PROFILE)


class RpcTests(unittest.TestCase):
    def test_exact_request_and_response_envelopes(self) -> None:
        server, peer = socket.socketpair()
        self.addCleanup(server.close)
        self.addCleanup(peer.close)
        peer.sendall(b'{"id":7,"method":"status","params":{}}\n')
        self.assertEqual(receive_request(server), (7, "status", {}))
        send_result(server, 7, {"ok": True})
        self.assertEqual(
            json.loads(peer.recv(1024)), {"id": 7, "result": {"ok": True}}
        )
        send_error(server, 8, "media.rejected", "not accepted")
        self.assertEqual(
            json.loads(peer.recv(1024)),
            {"id": 8, "error": {"code": "media.rejected", "message": "not accepted"}},
        )

    def test_server_directory_socket_and_peer_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "universal-usb" / "control.sock"
            server = open_server_socket(path)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection = None
            try:
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                validate_client_socket(path)
                client.connect(str(path))
                connection, peer_uid = accept_same_uid(server)
                self.assertEqual(peer_uid, os.geteuid())
            finally:
                if connection is not None:
                    connection.close()
                client.close()
                server.close()
                path.unlink()

    def test_server_rejects_permissive_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            private = Path(directory) / "universal-usb"
            private.mkdir(mode=0o755)
            private.chmod(0o755)
            with self.assertRaises(RpcPermissionError):
                open_server_socket(private / "control.sock")


class ScenarioTests(unittest.TestCase):
    def valid(self, steps: list[dict[str, object]], profile: str = "hid-msc") -> dict[str, object]:
        return {"version": 1, "profile": profile, "steps": steps}

    def test_unknown_fields_actions_and_dangerous_token_actions_are_rejected(self) -> None:
        invalid = [
            self.valid([{"action": "wait", "milliseconds": 1, "extra": True}]),
            self.valid([{"action": "shell", "command": "true"}]),
            self.valid([{"action": "token.reset"}], "security-token"),
            self.valid([{"action": "token.pin.set", "pin": "1234"}], "security-token"),
            self.valid([{"action": "token.otp.provision", "secret": "AAAA"}], "security-token"),
        ]
        for document in invalid:
            with self.subTest(document=document), self.assertRaises(ScenarioValidationError):
                validate_scenario(document)

    def test_all_keys_and_images_preflight_before_rpc(self) -> None:
        client = FakeClient()
        document = self.valid(
            [
                {"action": "hid.key.tap", "keys": ["not-a-key"]},
                {"action": "msc.attach", "image": "missing.img"},
            ]
        )
        with self.assertRaises(ScenarioValidationError):
            preflight_scenario(document, client=client)
        self.assertEqual(client.calls, [])

        client = FakeClient()
        document = self.valid([{"action": "msc.attach", "image": "missing.img"}])
        with self.assertRaises(ScenarioPreflightError):
            preflight_scenario(document, client=client)
        self.assertEqual(client.calls, [])

    def test_profile_is_never_switched_implicitly(self) -> None:
        client = FakeClient(profile="webcam")
        with self.assertRaises(ScenarioPreflightError):
            preflight_scenario(
                self.valid([{"action": "wait", "milliseconds": 0}]), client=client
            )
        self.assertEqual([method for method, _ in client.calls], ["status"])

    def test_runtime_failure_releases_all_hid(self) -> None:
        client = FakeClient(fail_method="hid.key")
        document = self.valid(
            [
                {"action": "hid.text", "text": "a"},
                {"action": "hid.key.down", "keys": ["a"]},
            ]
        )
        with self.assertRaises(ScenarioExecutionError):
            run_scenario(document, client)
        self.assertEqual(client.calls[-1], ("hid.release_all", {}))

    def test_schema_action_and_hid_enums_match_validator_surface(self) -> None:
        schema_path = Path(__file__).resolve().parents[2] / "schema" / "scenario-v1.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        variants = schema["$defs"]["step"]["oneOf"]
        schema_actions = {variant["properties"]["action"]["const"] for variant in variants}
        expected_actions = {
            "wait", "mark", "hid.key.tap", "hid.key.down", "hid.key.up", "hid.text",
            "hid.mouse.move", "hid.mouse.button.down", "hid.mouse.button.up",
            "hid.mouse.button.click", "hid.mouse.scroll", "hid.consumer.tap",
            "hid.consumer.down", "hid.consumer.up", "msc.attach", "msc.detach",
            "mic.tone", "mic.silence", "cam.pattern", "token.touch", "token.otp.select",
        }
        self.assertEqual(schema_actions, expected_actions)
        self.assertEqual(set(schema["$defs"]["key"]["enum"]), set(KEYBOARD_KEYS))
        controls = variants[11]["properties"]["control"]["enum"]
        self.assertEqual(set(controls), set(CONSUMER_CONTROLS))


if __name__ == "__main__":
    unittest.main()
