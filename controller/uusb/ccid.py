"""Synthetic one-slot CCID and PIV backend.

The USB-facing parser is intentionally small: one slot, T=1 parameters, short or
bounded extended APDUs, and no vendor escape commands.  Firmware normally
forwards only ``[PC_to_RDR_XfrBlock][APDU]`` through the controller mailbox;
``handle_bridge`` implements that compact form while ``handle_message`` keeps a
strict CCID message boundary for direct users and tests.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final

from .state import atomic_write_json, read_json, secure_state_dir

CCID_HEADER_SIZE: Final = 10
MAX_CCID_MESSAGE_SIZE: Final = 512
MAX_CCID_PAYLOAD_SIZE: Final = MAX_CCID_MESSAGE_SIZE - CCID_HEADER_SIZE
MAX_BRIDGE_MESSAGE_SIZE: Final = 512
SLOT: Final = 0

PC_TO_RDR_SET_PARAMETERS: Final = 0x61
PC_TO_RDR_ICC_POWER_ON: Final = 0x62
PC_TO_RDR_ICC_POWER_OFF: Final = 0x63
PC_TO_RDR_GET_SLOT_STATUS: Final = 0x65
PC_TO_RDR_ESCAPE: Final = 0x6B
PC_TO_RDR_GET_PARAMETERS: Final = 0x6C
PC_TO_RDR_XFR_BLOCK: Final = 0x6F
PC_TO_RDR_ABORT: Final = 0x72

RDR_TO_PC_DATA_BLOCK: Final = 0x80
RDR_TO_PC_SLOT_STATUS: Final = 0x81
RDR_TO_PC_PARAMETERS: Final = 0x82
RDR_TO_PC_ESCAPE: Final = 0x83

ICC_ACTIVE: Final = 0x00
ICC_INACTIVE: Final = 0x01
COMMAND_FAILED: Final = 0x40
TIME_EXTENSION: Final = 0x80

ERROR_NONE: Final = 0x00
ERROR_COMMAND_NOT_SUPPORTED: Final = 0x00
ERROR_BAD_LENGTH: Final = 0x01
ERROR_BAD_SLOT: Final = 0x05
ERROR_BAD_POWER_SELECT: Final = 0x07
ERROR_BAD_PROTOCOL: Final = 0x07
ERROR_BAD_LEVEL_PARAMETER: Final = 0x08
ERROR_BAD_IFSC: Final = 0x0F
ERROR_SLOT_BUSY: Final = 0xE0
ERROR_ICC_MUTE: Final = 0xFE
ERROR_COMMAND_ABORTED: Final = 0xFF

PROTOCOL_T1: Final = 0x01
ATR: Final = bytes.fromhex("3b800181")
DEFAULT_T1_PARAMETERS: Final = bytes.fromhex("1110004d002000")
PIV_AID: Final = bytes.fromhex("a000000308000010000100")
CHUID_OBJECT_ID: Final = bytes.fromhex("5fc102")
PIV_AUTH_CERT_OBJECT_ID: Final = bytes.fromhex("5fc105")
DIGITAL_SIGNATURE_CERT_OBJECT_ID: Final = bytes.fromhex("5fc10a")
PIV_AUTH_KEY_REFERENCE: Final = 0x9A
DIGITAL_SIGNATURE_KEY_REFERENCE: Final = 0x9C
ECDSA_SHA256_ALGORITHM: Final = 0x11
DEFAULT_TEST_PIN: Final = "123456"
MAX_PIN_RETRIES: Final = 3
PIN_HASH_ITERATIONS: Final = 120_000
STATE_VERSION: Final = 1
MAX_PRIVATE_KEY_SIZE: Final = 4096
MAX_CERTIFICATE_SIZE: Final = 4096
MAX_TLV_VALUE_SIZE: Final = MAX_BRIDGE_MESSAGE_SIZE


class CcidError(RuntimeError):
    """Base class for controller-side CCID failures."""


class CcidProtocolError(CcidError):
    """A bridge or CCID message cannot be safely decoded."""


class CcidStateError(CcidError):
    """Persistent PIV state is absent, corrupt, or internally inconsistent."""


class CcidCryptoError(CcidError):
    """The required real OpenSSL P-256 operation failed."""


class CcidBusyError(CcidError):
    """The sole synthetic slot is already processing a command."""


@dataclass(frozen=True, slots=True)
class _Apdu:
    cla: int
    ins: int
    p1: int
    p2: int
    data: bytes
    le: int | None


@dataclass(frozen=True, slots=True)
class _Header:
    command: int
    length: int
    slot: int
    sequence: int
    specific: bytes
    payload: bytes


class CcidBackend:
    """Bounded one-slot T=1 reader with a deliberately synthetic PIV applet.

    ``time_extension_hook`` receives only a non-zero CCID time multiplier.  It
    is intended to call the firmware command-engine time-extension operation;
    neither APDU data nor controller state is disclosed to the callback.
    """

    def __init__(
        self,
        state_directory: str | os.PathLike[str] | None = None,
        *,
        openssl: str = "openssl",
        time_extension_hook: Callable[[int], None] | None = None,
    ) -> None:
        self._state_dir = secure_state_dir(state_directory)
        self._state_path = self._state_dir / "ccid.json"
        self._openssl = self._resolve_openssl(openssl)
        self._time_extension_hook = time_extension_hook
        self._lock = threading.RLock()
        self._operation_gate = threading.Lock()
        self._powered = False
        self._selected = False
        self._pin_verified = False
        self._parameters = DEFAULT_T1_PARAMETERS
        self._operation_active = False
        self._time_extension_multiplier: int | None = None
        self._private_key_pem = b""
        self._certificate = b""
        self._pin_salt = b""
        self._pin_hash = b""
        self._pin_retries = MAX_PIN_RETRIES
        self._load_or_create_state()

    def handle_bridge(self, message: bytes | bytearray | memoryview) -> bytes:
        """Handle firmware mailbox form ``[0x6f][APDU]``.

        The returned mailbox message is ``[0x80][APDU response]``.  Firmware
        owns the USB CCID header, slot, sequence, power, and parameter state.
        """
        data = _byte_buffer(message, "bridge message")
        if not data or len(data) > MAX_BRIDGE_MESSAGE_SIZE:
            raise CcidProtocolError("bridge message length is invalid")
        if data[0] != PC_TO_RDR_XFR_BLOCK:
            raise CcidProtocolError("unsupported CCID bridge command")
        if not self._operation_gate.acquire(blocking=False):
            raise CcidBusyError("CCID slot is busy")
        try:
            response = self._handle_apdu(data[1:])
            if len(response) + 1 > MAX_BRIDGE_MESSAGE_SIZE:
                response = _status_word(0x6700)
            return bytes((RDR_TO_PC_DATA_BLOCK,)) + response
        finally:
            self._operation_gate.release()

    def handle_message(self, message: bytes | bytearray | memoryview) -> bytes:
        """Parse one complete, strictly bounded USB CCID bulk message."""
        data = _byte_buffer(message, "CCID message")
        if len(data) < CCID_HEADER_SIZE:
            raise CcidProtocolError("CCID message is shorter than its header")

        command = data[0]
        sequence = data[6]
        if len(data) > MAX_CCID_MESSAGE_SIZE:
            return self._command_error(command, sequence, ERROR_BAD_LENGTH)
        declared = int.from_bytes(data[1:5], "little")
        if declared > MAX_CCID_PAYLOAD_SIZE or declared != len(data) - CCID_HEADER_SIZE:
            return self._command_error(command, sequence, ERROR_BAD_LENGTH)
        header = _Header(command, declared, data[5], sequence, data[7:10], data[10:])
        if header.slot != SLOT:
            return self._command_error(command, sequence, ERROR_BAD_SLOT)
        if not self._operation_gate.acquire(blocking=False):
            return self._command_error(command, sequence, ERROR_SLOT_BUSY)
        try:
            return self._dispatch(header)
        finally:
            self._operation_gate.release()

    def time_extension_response(self, sequence: int, multiplier: int = 1) -> bytes:
        """Build the standards-coded empty DataBlock time extension."""
        if not isinstance(sequence, int) or not 0 <= sequence <= 0xFF:
            raise ValueError("sequence must fit in one byte")
        if not isinstance(multiplier, int) or not 1 <= multiplier <= 0xFF:
            raise ValueError("multiplier must be between 1 and 255")
        return self._response(
            RDR_TO_PC_DATA_BLOCK,
            sequence,
            self._icc_status() | TIME_EXTENSION,
            multiplier,
            0,
            b"",
        )

    def set_pin(self, pin: str) -> None:
        """Set the administrative test PIN without returning or retaining it."""
        encoded = _validated_pin(pin)
        if not self._operation_gate.acquire(blocking=False):
            raise CcidBusyError("CCID slot is busy")
        try:
            salt = os.urandom(16)
            digest = _pin_digest(encoded, salt)
            with self._lock:
                self._pin_salt = salt
                self._pin_hash = digest
                self._pin_retries = MAX_PIN_RETRIES
                self._pin_verified = False
                self._persist_state()
        finally:
            self._operation_gate.release()

    def reset(self) -> None:
        """Replace the synthetic controller identity and restore the test PIN."""
        if not self._operation_gate.acquire(blocking=False):
            raise CcidBusyError("CCID slot is busy")
        try:
            with self._lock:
                self._powered = False
                self._selected = False
                self._pin_verified = False
                self._parameters = DEFAULT_T1_PARAMETERS
                self._operation_active = False
                self._time_extension_multiplier = None
                self._create_state()
        finally:
            self._operation_gate.release()

    def status(self) -> dict[str, object]:
        """Return non-secret reader and PIV state."""
        with self._lock:
            return {
                "powered": self._powered,
                "protocol": "T=1",
                "selected": self._selected,
                "pin_verified": self._pin_verified,
                "pin_retries": self._pin_retries,
                "pin_blocked": self._pin_retries == 0,
                "busy": self._operation_active,
                "time_extension_multiplier": self._time_extension_multiplier,
                "certificate_present": bool(self._certificate),
            }

    def _dispatch(self, header: _Header) -> bytes:
        command = header.command
        if command == PC_TO_RDR_ICC_POWER_ON:
            if header.payload or header.specific[1:] != b"\x00\x00":
                return self._command_error(command, header.sequence, ERROR_BAD_LENGTH)
            if header.specific[0] not in (0, 1):
                return self._command_error(command, header.sequence, ERROR_BAD_POWER_SELECT)
            with self._lock:
                self._powered = True
                self._selected = False
                self._pin_verified = False
            return self._response(
                RDR_TO_PC_DATA_BLOCK,
                header.sequence,
                ICC_ACTIVE,
                ERROR_NONE,
                0,
                ATR,
            )

        if command == PC_TO_RDR_ICC_POWER_OFF:
            error = self._empty_command_error(header)
            if error is not None:
                return error
            with self._lock:
                self._powered = False
                self._selected = False
                self._pin_verified = False
            return self._slot_status(header.sequence)

        if command == PC_TO_RDR_GET_SLOT_STATUS:
            error = self._empty_command_error(header)
            return error if error is not None else self._slot_status(header.sequence)

        if command == PC_TO_RDR_GET_PARAMETERS:
            error = self._empty_command_error(header)
            if error is not None:
                return error
            with self._lock:
                parameters = self._parameters
            return self._response(
                RDR_TO_PC_PARAMETERS,
                header.sequence,
                self._icc_status(),
                ERROR_NONE,
                PROTOCOL_T1,
                parameters,
            )

        if command == PC_TO_RDR_SET_PARAMETERS:
            if header.specific[1:] != b"\x00\x00":
                return self._command_error(command, header.sequence, ERROR_BAD_PROTOCOL)
            if header.specific[0] != PROTOCOL_T1:
                return self._command_error(command, header.sequence, ERROR_BAD_PROTOCOL)
            if len(header.payload) != len(DEFAULT_T1_PARAMETERS):
                return self._command_error(command, header.sequence, ERROR_BAD_LENGTH)
            if header.payload[5] == 0 or header.payload[5] == 0xFF:
                return self._command_error(command, header.sequence, ERROR_BAD_IFSC)
            with self._lock:
                self._parameters = header.payload
            return self._response(
                RDR_TO_PC_PARAMETERS,
                header.sequence,
                self._icc_status(),
                ERROR_NONE,
                PROTOCOL_T1,
                header.payload,
            )

        if command == PC_TO_RDR_XFR_BLOCK:
            if not self._powered:
                return self._command_error(command, header.sequence, ERROR_ICC_MUTE)
            if header.specific[1:] != b"\x00\x00":
                return self._command_error(command, header.sequence, ERROR_BAD_LEVEL_PARAMETER)
            apdu_response = self._handle_apdu(header.payload)
            if len(apdu_response) > MAX_CCID_PAYLOAD_SIZE:
                return self._command_error(command, header.sequence, ERROR_BAD_LENGTH)
            return self._response(
                RDR_TO_PC_DATA_BLOCK,
                header.sequence,
                ICC_ACTIVE,
                ERROR_NONE,
                0,
                apdu_response,
            )

        if command == PC_TO_RDR_ESCAPE:
            if header.specific != b"\x00\x00\x00":
                return self._command_error(command, header.sequence, ERROR_BAD_LENGTH)
            return self._response(
                RDR_TO_PC_ESCAPE,
                header.sequence,
                self._icc_status() | COMMAND_FAILED,
                ERROR_COMMAND_NOT_SUPPORTED,
                0,
                b"",
            )

        if command == PC_TO_RDR_ABORT:
            error = self._empty_command_error(header)
            if error is not None:
                return error
            with self._lock:
                self._operation_active = False
                self._time_extension_multiplier = None
            return self._response(
                RDR_TO_PC_SLOT_STATUS,
                header.sequence,
                self._icc_status() | COMMAND_FAILED,
                ERROR_COMMAND_ABORTED,
                self._clock_status(),
                b"",
            )

        return self._command_error(command, header.sequence, ERROR_COMMAND_NOT_SUPPORTED)

    def _handle_apdu(self, raw: bytes) -> bytes:
        try:
            apdu = _parse_apdu(raw)
        except CcidProtocolError:
            return _status_word(0x6700)

        if apdu.ins == 0xA4:
            if apdu.cla != 0x00 or apdu.p1 != 0x04 or apdu.p2 != 0x00:
                return _status_word(0x6A86)
            if apdu.data != PIV_AID:
                with self._lock:
                    self._selected = False
                    self._pin_verified = False
                return _status_word(0x6A82)
            with self._lock:
                self._selected = True
                self._pin_verified = False
            fci = _tlv(b"\x61", _tlv(b"\x4f", PIV_AID))
            return fci + _status_word(0x9000)

        with self._lock:
            selected = self._selected
        if not selected:
            return _status_word(0x6985)
        if apdu.cla != 0x00:
            return _status_word(0x6E00)

        if apdu.ins == 0xCB:
            return self._get_data(apdu)
        if apdu.ins == 0x20:
            return self._verify(apdu)
        if apdu.ins == 0x87:
            return self._general_authenticate(apdu)
        return _status_word(0x6D00)

    def _get_data(self, apdu: _Apdu) -> bytes:
        if apdu.p1 != 0x3F or apdu.p2 != 0xFF:
            return _status_word(0x6A86)
        try:
            entries = _parse_tlvs(apdu.data)
        except CcidProtocolError:
            return _status_word(0x6A80)
        if entries != [(b"\x5c", CHUID_OBJECT_ID)] and entries != [
            (b"\x5c", PIV_AUTH_CERT_OBJECT_ID)
        ] and entries != [(b"\x5c", DIGITAL_SIGNATURE_CERT_OBJECT_ID)]:
            if len(entries) == 1 and entries[0][0] == b"\x5c":
                return _status_word(0x6A88)
            return _status_word(0x6A80)

        object_id = entries[0][1]
        if object_id == CHUID_OBJECT_ID:
            value = self._chuid_object()
        else:
            with self._lock:
                certificate = self._certificate
            value = _tlv(b"\x70", certificate) + _tlv(b"\x71", b"\x00") + _tlv(
                b"\xfe", b""
            )
        response = _tlv(b"\x53", value) + _status_word(0x9000)
        if len(response) > MAX_CCID_PAYLOAD_SIZE:
            return _status_word(0x6A84)
        return response

    def _verify(self, apdu: _Apdu) -> bytes:
        if apdu.p1 != 0x00 or apdu.p2 != 0x80:
            return _status_word(0x6A88)
        with self._lock:
            retries = self._pin_retries
        if retries == 0:
            return _status_word(0x6983)
        if not apdu.data:
            return _status_word(0x63C0 | retries)
        candidate = _decode_pin_field(apdu.data)
        with self._lock:
            matches = candidate is not None and hmac.compare_digest(
                _pin_digest(candidate, self._pin_salt), self._pin_hash
            )
            if matches:
                self._pin_verified = True
                if self._pin_retries != MAX_PIN_RETRIES:
                    self._pin_retries = MAX_PIN_RETRIES
                    self._persist_state()
                return _status_word(0x9000)
            self._pin_verified = False
            self._pin_retries -= 1
            self._persist_state()
            retries = self._pin_retries
        return _status_word(0x6983 if retries == 0 else 0x63C0 | retries)

    def _general_authenticate(self, apdu: _Apdu) -> bytes:
        if apdu.p1 != ECDSA_SHA256_ALGORITHM or apdu.p2 not in (
            PIV_AUTH_KEY_REFERENCE,
            DIGITAL_SIGNATURE_KEY_REFERENCE,
        ):
            return _status_word(0x6A86)
        with self._lock:
            if not self._pin_verified:
                return _status_word(0x6982)
        try:
            outer = _parse_tlvs(apdu.data)
            if len(outer) != 1 or outer[0][0] != b"\x7c":
                return _status_word(0x6A80)
            inner = _parse_tlvs(outer[0][1])
        except CcidProtocolError:
            return _status_word(0x6A80)
        challenge: bytes | None = None
        saw_empty_response = False
        for tag, value in inner:
            if tag == b"\x81" and challenge is None:
                challenge = value
            elif tag == b"\x82" and not value and not saw_empty_response:
                saw_empty_response = True
            else:
                return _status_word(0x6A80)
        if challenge is None or len(challenge) != hashlib.sha256().digest_size:
            return _status_word(0x6A80)
        try:
            self._begin_time_extension(1)
            signature = self._sign_digest(challenge)
        except CcidCryptoError:
            return _status_word(0x6F00)
        finally:
            self._end_time_extension()
        response = _tlv(b"\x7c", _tlv(b"\x82", signature))
        return response + _status_word(0x9000)

    def _begin_time_extension(self, multiplier: int) -> None:
        with self._lock:
            self._operation_active = True
            self._time_extension_multiplier = multiplier
        hook = self._time_extension_hook
        if hook is not None:
            try:
                hook(multiplier)
            except Exception as error:
                self._end_time_extension()
                raise CcidCryptoError("time-extension hook failed") from error

    def _end_time_extension(self) -> None:
        with self._lock:
            self._operation_active = False
            self._time_extension_multiplier = None

    def _sign_digest(self, digest: bytes) -> bytes:
        output = self._run_with_key(
            ["pkeyutl", "-sign", "-pkeyopt", "digest:sha256"],
            digest,
        )
        if not 8 <= len(output) <= 80 or output[0] != 0x30:
            raise CcidCryptoError("OpenSSL returned an invalid ECDSA signature")
        return output

    def _chuid_object(self) -> bytes:
        with self._lock:
            certificate = self._certificate
        guid = hashlib.sha256(certificate).digest()[:16]
        fasc_n = bytes.fromhex("d4e739da739ced39ce739d836858210842108421c84210c3")
        return (
            _tlv(b"\x30", fasc_n)
            + _tlv(b"\x34", guid)
            + _tlv(b"\x35", b"20361231")
            + _tlv(b"\x3e", b"")
            + _tlv(b"\xfe", b"")
        )

    def _empty_command_error(self, header: _Header) -> bytes | None:
        if header.payload or header.specific != b"\x00\x00\x00":
            return self._command_error(header.command, header.sequence, ERROR_BAD_LENGTH)
        return None

    def _slot_status(self, sequence: int) -> bytes:
        return self._response(
            RDR_TO_PC_SLOT_STATUS,
            sequence,
            self._icc_status(),
            ERROR_NONE,
            self._clock_status(),
            b"",
        )

    def _command_error(self, command: int, sequence: int, error: int) -> bytes:
        response_type = _response_type(command)
        parameter = PROTOCOL_T1 if response_type == RDR_TO_PC_PARAMETERS else 0
        if response_type == RDR_TO_PC_SLOT_STATUS:
            parameter = self._clock_status()
        return self._response(
            response_type,
            sequence,
            self._icc_status() | COMMAND_FAILED,
            error,
            parameter,
            b"",
        )

    def _response(
        self,
        message_type: int,
        sequence: int,
        status: int,
        error: int,
        parameter: int,
        payload: bytes,
    ) -> bytes:
        if len(payload) > MAX_CCID_PAYLOAD_SIZE:
            raise CcidProtocolError("CCID response payload exceeds its fixed bound")
        return (
            bytes((message_type,))
            + len(payload).to_bytes(4, "little")
            + bytes((SLOT, sequence, status, error, parameter))
            + payload
        )

    def _icc_status(self) -> int:
        with self._lock:
            return ICC_ACTIVE if self._powered else ICC_INACTIVE

    def _clock_status(self) -> int:
        with self._lock:
            return 0 if self._powered else 1

    def _load_or_create_state(self) -> None:
        value = read_json(self._state_path, None)
        if value is None:
            self._create_state()
            return
        if not isinstance(value, dict) or value.get("version") != STATE_VERSION:
            raise CcidStateError("unsupported CCID state")
        try:
            private_key = value["private_key_pem"].encode("ascii")
            certificate = base64.b64decode(value["certificate_der"], validate=True)
            salt = base64.b64decode(value["pin_salt"], validate=True)
            pin_hash = base64.b64decode(value["pin_hash"], validate=True)
            retries = value["pin_retries"]
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise CcidStateError("invalid CCID state encoding") from error
        if (
            not private_key.startswith(b"-----BEGIN PRIVATE KEY-----\n")
            or not private_key.endswith(b"-----END PRIVATE KEY-----\n")
            or len(private_key) > MAX_PRIVATE_KEY_SIZE
            or not certificate
            or len(certificate) > MAX_CERTIFICATE_SIZE
            or len(salt) != 16
            or len(pin_hash) != hashlib.sha256().digest_size
            or isinstance(retries, bool)
            or not isinstance(retries, int)
            or not 0 <= retries <= MAX_PIN_RETRIES
        ):
            raise CcidStateError("invalid CCID state values")
        self._private_key_pem = private_key
        self._certificate = certificate
        self._pin_salt = salt
        self._pin_hash = pin_hash
        self._pin_retries = retries

    def _create_state(self) -> None:
        private_key = self._run_openssl(
            ["genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"],
            None,
        )
        if (
            not private_key.startswith(b"-----BEGIN PRIVATE KEY-----\n")
            or not private_key.endswith(b"-----END PRIVATE KEY-----\n")
            or len(private_key) > MAX_PRIVATE_KEY_SIZE
        ):
            raise CcidCryptoError("OpenSSL returned an invalid private key")
        self._private_key_pem = private_key
        certificate = self._run_with_key(
            [
                "req",
                "-new",
                "-x509",
                "-subj",
                "/CN=Universal USB Synthetic PIV/",
                "-days",
                "3650",
                "-sha256",
                "-outform",
                "DER",
            ],
            None,
            key_option="-key",
        )
        if not certificate or len(certificate) > MAX_CERTIFICATE_SIZE:
            raise CcidCryptoError("OpenSSL returned an invalid certificate")
        salt = os.urandom(16)
        self._certificate = certificate
        self._pin_salt = salt
        self._pin_hash = _pin_digest(DEFAULT_TEST_PIN.encode("ascii"), salt)
        self._pin_retries = MAX_PIN_RETRIES
        self._persist_state()

    def _persist_state(self) -> None:
        atomic_write_json(
            self._state_path,
            {
                "version": STATE_VERSION,
                "private_key_pem": self._private_key_pem.decode("ascii"),
                "certificate_der": base64.b64encode(self._certificate).decode("ascii"),
                "pin_salt": base64.b64encode(self._pin_salt).decode("ascii"),
                "pin_hash": base64.b64encode(self._pin_hash).decode("ascii"),
                "pin_retries": self._pin_retries,
            },
        )

    def _run_with_key(
        self,
        arguments: list[str],
        input_data: bytes | None,
        *,
        key_option: str = "-inkey",
    ) -> bytes:
        read_descriptor, write_descriptor = os.pipe()
        try:
            offset = 0
            while offset < len(self._private_key_pem):
                written = os.write(write_descriptor, self._private_key_pem[offset:])
                if written <= 0:
                    raise CcidCryptoError("private-key pipe made no progress")
                offset += written
            os.close(write_descriptor)
            write_descriptor = -1
            command = [
                self._openssl,
                *arguments,
                key_option,
                f"/proc/self/fd/{read_descriptor}",
            ]
            return self._run_process(command, input_data, (read_descriptor,))
        finally:
            if write_descriptor >= 0:
                os.close(write_descriptor)
            os.close(read_descriptor)

    def _run_openssl(self, arguments: list[str], input_data: bytes | None) -> bytes:
        return self._run_process([self._openssl, *arguments], input_data, ())

    @staticmethod
    def _run_process(
        command: list[str], input_data: bytes | None, pass_fds: tuple[int, ...]
    ) -> bytes:
        try:
            completed = subprocess.run(
                command,
                input=input_data,
                stdin=subprocess.DEVNULL if input_data is None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=10.0,
                pass_fds=pass_fds,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CcidCryptoError("OpenSSL operation failed") from error
        if completed.returncode != 0:
            raise CcidCryptoError("OpenSSL operation failed")
        if len(completed.stdout) > MAX_PRIVATE_KEY_SIZE + MAX_CERTIFICATE_SIZE:
            raise CcidCryptoError("OpenSSL output exceeds its fixed bound")
        return completed.stdout

    @staticmethod
    def _resolve_openssl(executable: str) -> str:
        if not isinstance(executable, str) or not executable or "\x00" in executable:
            raise ValueError("openssl must name an executable")
        resolved = shutil.which(executable)
        if resolved is None:
            raise CcidCryptoError("OpenSSL executable is required for P-256 operations")
        return resolved


def _response_type(command: int) -> int:
    if command in (PC_TO_RDR_ICC_POWER_ON, PC_TO_RDR_XFR_BLOCK):
        return RDR_TO_PC_DATA_BLOCK
    if command in (PC_TO_RDR_GET_PARAMETERS, PC_TO_RDR_SET_PARAMETERS):
        return RDR_TO_PC_PARAMETERS
    if command == PC_TO_RDR_ESCAPE:
        return RDR_TO_PC_ESCAPE
    return RDR_TO_PC_SLOT_STATUS


def _byte_buffer(value: bytes | bytearray | memoryview, name: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} must be bytes-like")
    view = memoryview(value)
    if view.ndim != 1 or not view.c_contiguous:
        raise TypeError(f"{name} must be a contiguous one-dimensional buffer")
    try:
        return bytes(view.cast("B"))
    except (TypeError, ValueError) as error:
        raise TypeError(f"{name} must be a byte buffer") from error


def _parse_apdu(raw: bytes) -> _Apdu:
    if len(raw) < 4 or len(raw) > MAX_BRIDGE_MESSAGE_SIZE - 1:
        raise CcidProtocolError("APDU length is invalid")
    cla, ins, p1, p2 = raw[:4]
    if len(raw) == 4:
        return _Apdu(cla, ins, p1, p2, b"", None)
    first = raw[4]
    if len(raw) == 5:
        return _Apdu(cla, ins, p1, p2, b"", 256 if first == 0 else first)
    if first != 0:
        lc = first
        end = 5 + lc
        if len(raw) not in (end, end + 1):
            raise CcidProtocolError("short APDU length is inconsistent")
        le = None if len(raw) == end else (256 if raw[end] == 0 else raw[end])
        return _Apdu(cla, ins, p1, p2, raw[5:end], le)
    if len(raw) < 7:
        raise CcidProtocolError("extended APDU header is incomplete")
    extended = int.from_bytes(raw[5:7], "big")
    if len(raw) == 7:
        return _Apdu(cla, ins, p1, p2, b"", 65536 if extended == 0 else extended)
    if extended == 0:
        raise CcidProtocolError("extended APDU has zero data length")
    end = 7 + extended
    if len(raw) not in (end, end + 2):
        raise CcidProtocolError("extended APDU length is inconsistent")
    if len(raw) == end:
        le = None
    else:
        raw_le = int.from_bytes(raw[end : end + 2], "big")
        le = 65536 if raw_le == 0 else raw_le
    return _Apdu(cla, ins, p1, p2, raw[7:end], le)


def _parse_tlvs(data: bytes) -> list[tuple[bytes, bytes]]:
    if len(data) > MAX_TLV_VALUE_SIZE:
        raise CcidProtocolError("TLV input exceeds its fixed bound")
    entries: list[tuple[bytes, bytes]] = []
    offset = 0
    while offset < len(data):
        first = data[offset]
        offset += 1
        if first == 0 or first == 0xFF:
            raise CcidProtocolError("invalid TLV tag")
        tag = bytearray((first,))
        if first & 0x1F == 0x1F:
            for _ in range(2):
                if offset >= len(data):
                    raise CcidProtocolError("truncated TLV tag")
                octet = data[offset]
                offset += 1
                tag.append(octet)
                if not octet & 0x80:
                    break
            else:
                raise CcidProtocolError("TLV tag is too long")
            if tag[1] & 0x7F == 0:
                raise CcidProtocolError("non-canonical TLV tag")
        if offset >= len(data):
            raise CcidProtocolError("missing TLV length")
        first_length = data[offset]
        offset += 1
        if first_length < 0x80:
            length = first_length
        else:
            count = first_length & 0x7F
            if count == 0 or count > 2 or offset + count > len(data):
                raise CcidProtocolError("invalid TLV length")
            length_bytes = data[offset : offset + count]
            offset += count
            if length_bytes[0] == 0:
                raise CcidProtocolError("non-canonical TLV length")
            length = int.from_bytes(length_bytes, "big")
            if length < 0x80 or (length <= 0xFF and count != 1):
                raise CcidProtocolError("non-canonical TLV length")
        if length > MAX_TLV_VALUE_SIZE or offset + length > len(data):
            raise CcidProtocolError("TLV value is truncated")
        entries.append((bytes(tag), data[offset : offset + length]))
        offset += length
        if len(entries) > 16:
            raise CcidProtocolError("too many TLV entries")
    return entries


def _tlv(tag: bytes, value: bytes) -> bytes:
    if not tag or len(tag) > 3 or len(value) > MAX_TLV_VALUE_SIZE:
        raise CcidProtocolError("TLV output exceeds its fixed bound")
    length = len(value)
    if length < 0x80:
        encoded_length = bytes((length,))
    elif length <= 0xFF:
        encoded_length = bytes((0x81, length))
    else:
        encoded_length = b"\x82" + length.to_bytes(2, "big")
    return tag + encoded_length + value


def _validated_pin(pin: str) -> bytes:
    if not isinstance(pin, str):
        raise TypeError("PIN must be a string")
    if not 6 <= len(pin) <= 8 or not pin.isascii() or not pin.isdigit():
        raise ValueError("test PIN must contain six to eight ASCII digits")
    return pin.encode("ascii")


def _decode_pin_field(field: bytes) -> bytes | None:
    if len(field) != 8:
        return None
    try:
        padding = field.index(0xFF)
    except ValueError:
        padding = len(field)
    if any(value != 0xFF for value in field[padding:]):
        return None
    candidate = field[:padding]
    if not 6 <= len(candidate) <= 8 or any(value < 0x30 or value > 0x39 for value in candidate):
        return None
    return candidate


def _pin_digest(pin: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin, salt, PIN_HASH_ITERATIONS)


def _status_word(value: int) -> bytes:
    return value.to_bytes(2, "big")
