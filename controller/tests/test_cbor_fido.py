from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uusb import cbor
from uusb.fido import (
    AUTH_DATA_FLAG_AT,
    AUTH_DATA_FLAG_UP,
    COSE_ALGORITHM_ES256,
    CTAPHID_CBOR,
    CTAPHID_INIT,
    CTAPHID_PING,
    CTAP2_GET_ASSERTION,
    CTAP2_GET_INFO,
    CTAP2_MAKE_CREDENTIAL,
    CTAP2_OK,
    CTAP2_RESET,
    CtaphidFramer,
    FidoBackend,
    TouchRequired,
    der_signature_to_raw,
    frame_hid_message,
    p256_public_key_der,
    raw_signature_to_der,
)


class CanonicalCborTests(unittest.TestCase):
    def test_deterministic_encoding_uses_length_then_bytewise_key_order(self) -> None:
        value = {-1: True, 10: "ten", 1: b"x"}
        encoded = b"\xa3\x01\x41x\x0a\x63ten\x20\xf5"

        self.assertEqual(cbor.dumps(value), encoded)
        self.assertEqual(cbor.loads(encoded), value)
        self.assertEqual(cbor.dumps(cbor.loads(encoded)), encoded)

    def test_integer_boundaries_are_minimal(self) -> None:
        self.assertEqual(cbor.dumps([23, 24, 255, 256, -1, -24, -25]),
                         bytes.fromhex("8717181818ff19010020373818"))
        for malformed in (
            bytes.fromhex("1817"),
            bytes.fromhex("1900ff"),
            bytes.fromhex("1a0000ffff"),
            bytes.fromhex("1b00000000ffffffff"),
        ):
            with self.subTest(malformed=malformed.hex()):
                with self.assertRaises(cbor.CBORDecodeError):
                    cbor.loads(malformed)

    def test_rejects_duplicates_noncanonical_order_and_unsupported_forms(self) -> None:
        malformed = (
            bytes.fromhex("a201000101"),  # duplicate key
            bytes.fromhex("a202000100"),  # keys in descending order
            bytes.fromhex("a20001f402"),  # Python-equal 0 and false keys
            bytes.fromhex("9f01ff"),      # indefinite array
            bytes.fromhex("c000"),        # tag
            bytes.fromhex("f90000"),      # float
            b"\x00\x00",                 # trailing item
            b"\x61\xff",                 # invalid UTF-8
        )
        for value in malformed:
            with self.subTest(value=value.hex()):
                with self.assertRaises(cbor.CBORDecodeError):
                    cbor.loads(value)

    def test_limits_apply_to_encode_and_decode(self) -> None:
        with self.assertRaises(cbor.CBORLimitError):
            cbor.dumps([[[0]]], max_depth=2)
        with self.assertRaises(cbor.CBORLimitError):
            cbor.loads(bytes.fromhex("81818100"), max_depth=2)
        with self.assertRaises(cbor.CBORLimitError):
            cbor.dumps(b"12345", max_string_bytes=4)
        with self.assertRaises(cbor.CBORLimitError):
            cbor.loads(b"\x45" + b"12345", max_string_bytes=4)
        with self.assertRaises(cbor.CBORLimitError):
            cbor.loads(b"\x00" * 5, max_bytes=4)


class CtaphidFramingTests(unittest.TestCase):
    def test_fragmented_message_round_trip(self) -> None:
        payload = bytes(index % 251 for index in range(200))
        reports = frame_hid_message(0x01020304, CTAPHID_PING, payload)
        self.assertGreater(len(reports), 1)
        self.assertTrue(all(len(report) == 64 for report in reports))

        framer = CtaphidFramer()
        message = None
        for report in reports:
            message = framer.feed(report)
        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.cid, 0x01020304)
        self.assertEqual(message.command, CTAPHID_PING)
        self.assertEqual(message.payload, payload)

    def test_init_and_ping_bridge_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = FidoBackend(directory)
            nonce = bytes(range(8))
            initialized = backend.handle_bridge(bytes((CTAPHID_INIT,)) + nonce)
            self.assertEqual(initialized[0], CTAPHID_INIT)
            self.assertEqual(initialized[1:9], nonce)
            self.assertEqual(len(initialized), 18)
            ping = bytes((CTAPHID_PING,)) + os.urandom(120)
            self.assertEqual(backend.handle_bridge(ping), ping)


@unittest.skipUnless(shutil.which("openssl"), "system OpenSSL is required")
class FidoBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)
        self.backend = FidoBackend(self.directory)

    def _ctap(self, command: int, value: object | None = None) -> bytes:
        request = bytes((CTAPHID_CBOR, command))
        if value is not None:
            request += cbor.dumps(value, max_bytes=510, max_string_bytes=510)
        return self.backend.handle_bridge(request)

    def _successful_value(self, response: bytes) -> object:
        self.assertEqual(response[0], CTAPHID_CBOR)
        self.assertEqual(response[1], CTAP2_OK)
        return cbor.loads(response[2:])

    def _make_request(self) -> dict[int, object]:
        return {
            1: bytes(range(32)),
            2: {"id": "example.com", "name": "Example"},
            3: {
                "id": b"user-123",
                "name": "alice",
                "displayName": "Alice Example",
            },
            4: [
                {"alg": -257, "type": "public-key"},
                {"alg": COSE_ALGORITHM_ES256, "type": "public-key"},
            ],
            7: {"rk": True},
        }

    def _create_credential(self) -> tuple[bytes, dict[int, object]]:
        request = self._make_request()
        with self.assertRaises(TouchRequired):
            self._ctap(CTAP2_MAKE_CREDENTIAL, request)
        self.assertEqual(self.backend.status()["credential_count"], 0)
        self.backend.touch()
        attestation = self._successful_value(
            self._ctap(CTAP2_MAKE_CREDENTIAL, request)
        )
        self.assertIsInstance(attestation, dict)
        assert isinstance(attestation, dict)
        self.assertEqual(attestation[1], "none")
        self.assertEqual(attestation[3], {})

        auth_data = attestation[2]
        self.assertIsInstance(auth_data, bytes)
        assert isinstance(auth_data, bytes)
        self.assertEqual(auth_data[32], AUTH_DATA_FLAG_UP | AUTH_DATA_FLAG_AT)
        self.assertEqual(int.from_bytes(auth_data[33:37], "big"), 0)
        credential_length = int.from_bytes(auth_data[53:55], "big")
        credential_id = auth_data[55 : 55 + credential_length]
        cose_key = cbor.loads(auth_data[55 + credential_length :])
        self.assertEqual(cose_key[1], 2)
        self.assertEqual(cose_key[3], COSE_ALGORITHM_ES256)
        self.assertEqual(cose_key[-1], 1)
        return credential_id, cose_key

    def test_get_info_is_browser_usable_and_does_not_claim_uv(self) -> None:
        info = self._successful_value(self._ctap(CTAP2_GET_INFO))
        self.assertIn("FIDO_2_0", info[1])
        self.assertEqual(len(info[3]), 16)
        self.assertTrue(info[4]["rk"])
        self.assertTrue(info[4]["up"])
        self.assertFalse(info[4]["uv"])
        self.assertIn(
            {"alg": COSE_ALGORITHM_ES256, "type": "public-key"}, info[10]
        )

    def test_make_assert_persist_counter_and_verify_openssl_signature(self) -> None:
        credential_id, cose_key = self._create_credential()
        client_data_hash = bytes(reversed(range(32)))
        assertion_request = {
            1: "example.com",
            2: client_data_hash,
            3: [{"id": credential_id, "type": "public-key"}],
        }
        with self.assertRaises(TouchRequired):
            self._ctap(CTAP2_GET_ASSERTION, assertion_request)

        self.backend.touch()
        assertion = self._successful_value(
            self._ctap(CTAP2_GET_ASSERTION, assertion_request)
        )
        auth_data = assertion[2]
        signature = assertion[3]
        self.assertEqual(assertion[1]["id"], credential_id)
        self.assertEqual(auth_data[32], AUTH_DATA_FLAG_UP)
        self.assertEqual(int.from_bytes(auth_data[33:37], "big"), 1)
        raw = der_signature_to_raw(signature)
        self.assertEqual(raw_signature_to_der(raw), signature)

        public_der = p256_public_key_der(cose_key[-2], cose_key[-3])
        public_path = self.directory / "public.der"
        signature_path = self.directory / "signature.der"
        public_path.write_bytes(public_der)
        signature_path.write_bytes(signature)
        verified = subprocess.run(
            (
                "openssl",
                "dgst",
                "-sha256",
                "-keyform",
                "DER",
                "-verify",
                os.fspath(public_path),
                "-signature",
                os.fspath(signature_path),
            ),
            input=auth_data + client_data_hash,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )
        self.assertEqual(verified.returncode, 0, verified.stderr.decode("utf-8", "replace"))
        self.assertIn(b"Verified OK", verified.stdout)

        reloaded = FidoBackend(self.directory)
        reloaded.touch()
        response = reloaded.handle_bridge(
            bytes((CTAPHID_CBOR, CTAP2_GET_ASSERTION))
            + cbor.dumps(assertion_request)
        )
        self.assertEqual(response[:2], bytes((CTAPHID_CBOR, CTAP2_OK)))
        second_assertion = cbor.loads(response[2:])
        self.assertEqual(int.from_bytes(second_assertion[2][33:37], "big"), 2)

    def test_reset_waits_for_touch_and_removes_credentials(self) -> None:
        self._create_credential()
        with self.assertRaises(TouchRequired):
            self._ctap(CTAP2_RESET)
        self.assertEqual(self.backend.status()["credential_count"], 1)

        self.backend.touch()
        self.assertEqual(
            self._ctap(CTAP2_RESET), bytes((CTAPHID_CBOR, CTAP2_OK))
        )
        self.assertEqual(self.backend.status()["credential_count"], 0)


if __name__ == "__main__":
    unittest.main()
