from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uusb.ccid import (
    ATR,
    CHUID_OBJECT_ID,
    COMMAND_FAILED,
    DEFAULT_T1_PARAMETERS,
    DIGITAL_SIGNATURE_CERT_OBJECT_ID,
    ERROR_BAD_LENGTH,
    ERROR_BAD_SLOT,
    ERROR_COMMAND_ABORTED,
    ERROR_COMMAND_NOT_SUPPORTED,
    ERROR_ICC_MUTE,
    ERROR_SLOT_BUSY,
    ICC_ACTIVE,
    ICC_INACTIVE,
    MAX_BRIDGE_MESSAGE_SIZE,
    MAX_CCID_MESSAGE_SIZE,
    PC_TO_RDR_ABORT,
    PC_TO_RDR_ESCAPE,
    PC_TO_RDR_GET_PARAMETERS,
    PC_TO_RDR_GET_SLOT_STATUS,
    PC_TO_RDR_ICC_POWER_OFF,
    PC_TO_RDR_ICC_POWER_ON,
    PC_TO_RDR_SET_PARAMETERS,
    PC_TO_RDR_XFR_BLOCK,
    PIV_AID,
    RDR_TO_PC_DATA_BLOCK,
    RDR_TO_PC_ESCAPE,
    RDR_TO_PC_PARAMETERS,
    RDR_TO_PC_SLOT_STATUS,
    TIME_EXTENSION,
    CcidBackend,
    CcidBusyError,
    CcidProtocolError,
)


def ccid_message(
    command: int,
    sequence: int,
    payload: bytes = b"",
    *,
    slot: int = 0,
    specific: bytes = b"\x00\x00\x00",
) -> bytes:
    if len(specific) != 3:
        raise ValueError("specific header must have three bytes")
    return (
        bytes((command,))
        + len(payload).to_bytes(4, "little")
        + bytes((slot, sequence))
        + specific
        + payload
    )


def response_fields(message: bytes) -> tuple[int, int, int, int, int, bytes]:
    if len(message) < 10:
        raise AssertionError("truncated response")
    length = int.from_bytes(message[1:5], "little")
    if length != len(message) - 10:
        raise AssertionError("inconsistent response length")
    return message[0], message[6], message[7], message[8], message[9], message[10:]


def tlv(tag: bytes, value: bytes) -> bytes:
    if len(value) < 0x80:
        length = bytes((len(value),))
    elif len(value) <= 0xFF:
        length = bytes((0x81, len(value)))
    else:
        length = b"\x82" + len(value).to_bytes(2, "big")
    return tag + length + value

def one_tlv(data: bytes) -> tuple[bytes, bytes, bytes]:
    first = data[0]
    tag_length = 1
    if first & 0x1F == 0x1F:
        tag_length = 2
        while data[tag_length - 1] & 0x80:
            tag_length += 1
    tag = data[:tag_length]
    offset = tag_length
    first_length = data[offset]
    offset += 1
    if first_length < 0x80:
        length = first_length
    else:
        count = first_length & 0x7F
        length = int.from_bytes(data[offset : offset + count], "big")
        offset += count
    return tag, data[offset : offset + length], data[offset + length :]


def select_apdu(aid: bytes = PIV_AID) -> bytes:
    return b"\x00\xa4\x04\x00" + bytes((len(aid),)) + aid + b"\x00"


def get_data_apdu(object_id: bytes) -> bytes:
    selector = tlv(b"\x5c", object_id)
    return b"\x00\xcb\x3f\xff" + bytes((len(selector),)) + selector + b"\x00"


def verify_apdu(pin: str) -> bytes:
    field = pin.encode("ascii").ljust(8, b"\xff")
    return b"\x00\x20\x00\x80\x08" + field


def authenticate_apdu(digest: bytes) -> bytes:
    dynamic = tlv(b"\x7c", tlv(b"\x82", b"") + tlv(b"\x81", digest))
    return b"\x00\x87\x11\x9c" + bytes((len(dynamic),)) + dynamic + b"\x00"


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required by the backend")
class CcidBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.state_dir = Path(self.temporary_directory.name) / "state"
        self.backend = CcidBackend(self.state_dir)

    def bridge(self, apdu: bytes) -> bytes:
        response = self.backend.handle_bridge(bytes((PC_TO_RDR_XFR_BLOCK,)) + apdu)
        self.assertEqual(response[0], RDR_TO_PC_DATA_BLOCK)
        return response[1:]

    def power_on(self, sequence: int = 1) -> bytes:
        return self.backend.handle_message(ccid_message(PC_TO_RDR_ICC_POWER_ON, sequence))

    def test_power_slot_status_and_parameter_responses_are_ccid_shaped(self) -> None:
        message_type, sequence, status, error, clock, payload = response_fields(
            self.backend.handle_message(ccid_message(PC_TO_RDR_GET_SLOT_STATUS, 41))
        )
        self.assertEqual(
            (message_type, sequence, status, error, clock, payload),
            (RDR_TO_PC_SLOT_STATUS, 41, ICC_INACTIVE, 0, 1, b""),
        )

        message_type, sequence, status, error, parameter, payload = response_fields(
            self.power_on(42)
        )
        self.assertEqual(
            (message_type, sequence, status, error, parameter, payload),
            (RDR_TO_PC_DATA_BLOCK, 42, ICC_ACTIVE, 0, 0, ATR),
        )
        message_type, sequence, status, error, protocol, payload = response_fields(
            self.backend.handle_message(ccid_message(PC_TO_RDR_GET_PARAMETERS, 43))
        )
        self.assertEqual(
            (message_type, sequence, status, error, protocol, payload),
            (RDR_TO_PC_PARAMETERS, 43, ICC_ACTIVE, 0, 1, DEFAULT_T1_PARAMETERS),
        )

        message_type, sequence, status, error, clock, payload = response_fields(
            self.backend.handle_message(ccid_message(PC_TO_RDR_ICC_POWER_OFF, 44))
        )
        self.assertEqual(
            (message_type, sequence, status, error, clock, payload),
            (RDR_TO_PC_SLOT_STATUS, 44, ICC_INACTIVE, 0, 1, b""),
        )

    def test_strict_length_slot_sequence_and_message_bounds(self) -> None:
        wrong_slot = self.backend.handle_message(
            ccid_message(PC_TO_RDR_GET_SLOT_STATUS, 0xA5, slot=1)
        )
        self.assertEqual(
            response_fields(wrong_slot)[1:4],
            (0xA5, ICC_INACTIVE | COMMAND_FAILED, ERROR_BAD_SLOT),
        )

        malformed = bytearray(ccid_message(PC_TO_RDR_GET_SLOT_STATUS, 0x7E))
        malformed[1:5] = (1).to_bytes(4, "little")
        self.assertEqual(response_fields(self.backend.handle_message(malformed))[1:4], (
            0x7E,
            ICC_INACTIVE | COMMAND_FAILED,
            ERROR_BAD_LENGTH,
        ))

        oversized = ccid_message(
            PC_TO_RDR_XFR_BLOCK,
            0x34,
            b"x" * (MAX_CCID_MESSAGE_SIZE - 9),
        )
        self.assertGreater(len(oversized), MAX_CCID_MESSAGE_SIZE)
        self.assertEqual(response_fields(self.backend.handle_message(oversized))[1:4], (
            0x34,
            ICC_INACTIVE | COMMAND_FAILED,
            ERROR_BAD_LENGTH,
        ))
        with self.assertRaises(CcidProtocolError):
            self.backend.handle_message(b"\x65\x00")

    def test_set_parameters_requires_t1_and_bounded_ifsc(self) -> None:
        parameters = bytes.fromhex("1210014d004000")
        response = self.backend.handle_message(
            ccid_message(
                PC_TO_RDR_SET_PARAMETERS,
                2,
                parameters,
                specific=b"\x01\x00\x00",
            )
        )
        self.assertEqual(response_fields(response), (
            RDR_TO_PC_PARAMETERS,
            2,
            ICC_INACTIVE,
            0,
            1,
            parameters,
        ))

        bad = self.backend.handle_message(
            ccid_message(
                PC_TO_RDR_SET_PARAMETERS,
                3,
                parameters[:5] + b"\x00" + parameters[6:],
                specific=b"\x01\x00\x00",
            )
        )
        self.assertEqual(response_fields(bad)[2] & COMMAND_FAILED, COMMAND_FAILED)

    def test_power_is_required_for_full_xfrblock(self) -> None:
        response = self.backend.handle_message(
            ccid_message(PC_TO_RDR_XFR_BLOCK, 5, select_apdu())
        )
        self.assertEqual(response_fields(response)[0:4], (
            RDR_TO_PC_DATA_BLOCK,
            5,
            ICC_INACTIVE | COMMAND_FAILED,
            ERROR_ICC_MUTE,
        ))

    def test_escape_is_explicitly_unsupported_and_abort_is_coded(self) -> None:
        escape = response_fields(
            self.backend.handle_message(ccid_message(PC_TO_RDR_ESCAPE, 6, b"vendor"))
        )
        self.assertEqual(escape[:4], (
            RDR_TO_PC_ESCAPE,
            6,
            ICC_INACTIVE | COMMAND_FAILED,
            ERROR_COMMAND_NOT_SUPPORTED,
        ))
        abort = response_fields(
            self.backend.handle_message(ccid_message(PC_TO_RDR_ABORT, 7))
        )
        self.assertEqual(abort[:4], (
            RDR_TO_PC_SLOT_STATUS,
            7,
            ICC_INACTIVE | COMMAND_FAILED,
            ERROR_COMMAND_ABORTED,
        ))

    def test_bridge_accepts_only_bounded_xfrblock_payload(self) -> None:
        response = self.bridge(select_apdu())
        self.assertEqual(response[-2:], b"\x90\x00")
        with self.assertRaises(CcidProtocolError):
            self.backend.handle_bridge(b"\x62")
        with self.assertRaises(CcidProtocolError):
            self.backend.handle_bridge(
                bytes((PC_TO_RDR_XFR_BLOCK,)) + b"x" * MAX_BRIDGE_MESSAGE_SIZE
            )
        self.backend._operation_gate.acquire()
        try:
            with self.assertRaises(CcidBusyError):
                self.backend.handle_bridge(bytes((PC_TO_RDR_XFR_BLOCK,)) + select_apdu())
        finally:
            self.backend._operation_gate.release()

    def test_select_requires_exact_piv_aid_and_get_data_is_bounded(self) -> None:
        self.assertEqual(self.bridge(select_apdu(PIV_AID[:-1]))[-2:], b"\x6a\x82")
        self.assertEqual(self.bridge(get_data_apdu(CHUID_OBJECT_ID))[-2:], b"\x69\x85")
        self.assertEqual(self.bridge(select_apdu())[-2:], b"\x90\x00")

        chuid = self.bridge(get_data_apdu(CHUID_OBJECT_ID))
        self.assertEqual(chuid[-2:], b"\x90\x00")
        tag, value, trailing = one_tlv(chuid[:-2])
        self.assertEqual((tag, trailing), (b"\x53", b""))
        self.assertIn(b"20361231", value)
        self.assertLessEqual(len(chuid) + 1, MAX_BRIDGE_MESSAGE_SIZE)

        missing = self.bridge(get_data_apdu(bytes.fromhex("5fff00")))
        self.assertEqual(missing, b"\x6a\x88")
        malformed = self.bridge(b"\x00\xcb\x3f\xff\x05\x5c\x82\x01\x00\x01\x00")
        self.assertEqual(malformed, b"\x6a\x80")

    def test_certificate_object_contains_real_der_certificate(self) -> None:
        self.bridge(select_apdu())
        response = self.bridge(get_data_apdu(DIGITAL_SIGNATURE_CERT_OBJECT_ID))
        self.assertEqual(response[-2:], b"\x90\x00")
        outer_tag, outer_value, outer_trailing = one_tlv(response[:-2])
        cert_tag, certificate, remainder = one_tlv(outer_value)
        self.assertEqual((outer_tag, outer_trailing, cert_tag), (b"\x53", b"", b"\x70"))
        self.assertTrue(certificate.startswith(b"\x30"))
        info_tag, info, remainder = one_tlv(remainder)
        lrc_tag, lrc, remainder = one_tlv(remainder)
        self.assertEqual((info_tag, info, lrc_tag, lrc, remainder), (
            b"\x71",
            b"\x00",
            b"\xfe",
            b"",
            b"",
        ))

    def test_verify_has_bounded_retries_and_persists_without_plaintext_pin(self) -> None:
        self.bridge(select_apdu())
        self.assertEqual(self.bridge(verify_apdu("000000")), b"\x63\xc2")
        reloaded = CcidBackend(self.state_dir)
        self.backend = reloaded
        self.bridge(select_apdu())
        self.assertEqual(self.bridge(verify_apdu("111111")), b"\x63\xc1")
        self.assertEqual(self.bridge(verify_apdu("222222")), b"\x69\x83")
        self.assertEqual(self.bridge(verify_apdu("123456")), b"\x69\x83")
        state_text = (self.state_dir / "ccid.json").read_text(encoding="utf-8")
        self.assertNotIn("123456", state_text)
        self.assertEqual(stat.S_IMODE(os.stat(self.state_dir).st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE(os.stat(self.state_dir / "ccid.json").st_mode), 0o600
        )

    def test_set_pin_resets_retries_without_exposing_secret_in_status(self) -> None:
        self.backend.set_pin("654321")
        self.bridge(select_apdu())
        self.assertEqual(self.bridge(verify_apdu("654321")), b"\x90\x00")
        status = self.backend.status()
        rendered = repr(status)
        self.assertNotIn("654321", rendered)
        self.assertNotIn("PRIVATE KEY", rendered)
        self.assertEqual(status["pin_retries"], 3)
        self.assertTrue(status["pin_verified"])
        with self.assertRaises(ValueError):
            self.backend.set_pin("short")

    def test_general_authenticate_emits_extension_and_real_p256_signature(self) -> None:
        extensions: list[int] = []
        extension_statuses: list[dict[str, object]] = []

        def record_extension(multiplier: int) -> None:
            extensions.append(multiplier)
            extension_statuses.append(self.backend.status())

        self.backend = CcidBackend(
            self.state_dir,
            time_extension_hook=record_extension,
        )
        self.bridge(select_apdu())
        self.assertEqual(self.bridge(verify_apdu("123456")), b"\x90\x00")
        digest = bytes.fromhex("5f70bf18a086007016e948b04aed3b82103a36be5f0065dd65bdfa71cc104498")
        auth_response = self.bridge(authenticate_apdu(digest))
        self.assertEqual(auth_response[-2:], b"\x90\x00")
        outer_tag, outer_value, outer_trailing = one_tlv(auth_response[:-2])
        signature_tag, signature, signature_trailing = one_tlv(outer_value)
        self.assertEqual(
            (outer_tag, outer_trailing, signature_tag, signature_trailing),
            (b"\x7c", b"", b"\x82", b""),
        )
        self.assertTrue(signature.startswith(b"\x30"))
        self.assertEqual(extensions, [1])
        self.assertTrue(extension_statuses[0]["busy"])
        self.assertEqual(extension_statuses[0]["time_extension_multiplier"], 1)
        self.assertFalse(self.backend.status()["busy"])
        self.assertIsNone(self.backend.status()["time_extension_multiplier"])

        certificate_response = self.bridge(
            get_data_apdu(DIGITAL_SIGNATURE_CERT_OBJECT_ID)
        )
        _, certificate_object, _ = one_tlv(certificate_response[:-2])
        _, certificate, _ = one_tlv(certificate_object)
        certificate_path = self.state_dir / "verify-certificate.der"
        public_key_path = self.state_dir / "verify-public.pem"
        digest_path = self.state_dir / "verify-digest.bin"
        signature_path = self.state_dir / "verify-signature.der"
        certificate_path.write_bytes(certificate)
        digest_path.write_bytes(digest)
        signature_path.write_bytes(signature)
        public_key = subprocess.run(
            [
                shutil.which("openssl") or "openssl",
                "x509",
                "-inform",
                "DER",
                "-in",
                os.fspath(certificate_path),
                "-pubkey",
                "-noout",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
        public_key_path.write_bytes(public_key)
        verified = subprocess.run(
            [
                shutil.which("openssl") or "openssl",
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                os.fspath(public_key_path),
                "-pkeyopt",
                "digest:sha256",
                "-in",
                os.fspath(digest_path),
                "-sigfile",
                os.fspath(signature_path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(verified.returncode, 0)

    def test_general_authenticate_requires_pin_and_exact_bounded_template(self) -> None:
        self.bridge(select_apdu())
        digest = b"d" * 32
        self.assertEqual(self.bridge(authenticate_apdu(digest)), b"\x69\x82")
        self.assertEqual(self.bridge(verify_apdu("123456")), b"\x90\x00")
        bad_template = b"\x7c\x04\x81\x02xx"
        apdu = b"\x00\x87\x11\x9c" + bytes((len(bad_template),)) + bad_template
        self.assertEqual(self.bridge(apdu), b"\x6a\x80")

    def test_time_extension_response_is_standards_coded(self) -> None:
        message_type, sequence, status, error, parameter, payload = response_fields(
            self.backend.time_extension_response(0xFE, 7)
        )
        self.assertEqual(
            (message_type, sequence, status, error, parameter, payload),
            (RDR_TO_PC_DATA_BLOCK, 0xFE, ICC_INACTIVE | TIME_EXTENSION, 7, 0, b""),
        )

    def test_reset_replaces_identity_and_restores_default_pin(self) -> None:
        self.bridge(select_apdu())
        first = self.bridge(get_data_apdu(DIGITAL_SIGNATURE_CERT_OBJECT_ID))
        self.backend.set_pin("87654321")
        self.backend.reset()
        self.bridge(select_apdu())
        second = self.bridge(get_data_apdu(DIGITAL_SIGNATURE_CERT_OBJECT_ID))
        self.assertNotEqual(first, second)
        self.assertEqual(self.bridge(verify_apdu("123456")), b"\x90\x00")


if __name__ == "__main__":
    unittest.main()
