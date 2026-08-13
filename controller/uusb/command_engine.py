"""Host-side control-slot sequencing, liveness, and heartbeat publication."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .openocd import OpenOCDTransport
from .protocol import (
    CONTROL_OFFSET,
    FIELD_OFFSETS,
    HEADER_SIZE,
    MAILBOX_ADDRESS,
    ControlResponse,
    Header,
    Opcode,
    Profile,
    ProtocolError,
    Status,
    pack_control_request_body,
    parse_control_response,
    validate_control_payload,
)


class CommandEngineError(RuntimeError):
    """Base host command-engine failure."""


class MailboxNotLive(CommandEngineError):
    """The mailbox identity was valid but uptime did not advance."""


class MailboxBusy(CommandEngineError):
    """Firmware already has one unacknowledged control request."""


class MailboxCommandTimeout(CommandEngineError):
    """Firmware did not acknowledge before the command deadline."""


class MailboxResetDuringCommand(CommandEngineError):
    """Firmware reset after trigger publication and before ACK."""


@dataclass(frozen=True, slots=True)
class LivenessResult:
    first: Header
    second: Header


class CommandEngine:
    """Serialize mailbox publications through one OpenOCD owner.

    A submission writes request metadata, CRC and payload first, then writes the
    host sequence in a separate RPC. Transport failures and reset-indeterminate
    operations are returned to the caller and never replayed.
    """

    def __init__(
        self,
        transport: OpenOCDTransport,
        *,
        command_timeout: float = 1.0,
        poll_interval: float = 0.01,
        liveness_interval: float = 0.01,
        heartbeat_interval: float = 0.250,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if min(command_timeout, poll_interval, liveness_interval, heartbeat_interval) <= 0:
            raise ValueError("command engine intervals must be positive")
        self.transport = transport
        self.command_timeout = command_timeout
        self.poll_interval = poll_interval
        self.liveness_interval = liveness_interval
        self.heartbeat_interval = heartbeat_interval
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.RLock()
        self._heartbeat_value: int | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()
        self._heartbeat_error: Exception | None = None

    def read_header(self) -> Header:
        raw = self.transport.read_bytes(MAILBOX_ADDRESS, HEADER_SIZE)
        return Header.unpack(raw)

    def check_liveness(self) -> LivenessResult:
        with self._lock:
            first = self.read_header()
            self._sleep(self.liveness_interval)
            second = self.read_header()
            if first.boot_counter != second.boot_counter:
                raise MailboxNotLive("firmware reset between liveness reads")
            delta = (second.uptime_ms - first.uptime_ms) & 0xFFFFFFFF
            if delta == 0 or delta >= 0x80000000:
                raise MailboxNotLive("firmware uptime did not advance monotonically")
            return LivenessResult(first, second)

    def submit(
        self,
        opcode: Opcode | int,
        payload: bytes = b"",
        *,
        timeout: float | None = None,
    ) -> ControlResponse:
        command = Opcode(opcode)
        data = bytes(payload)
        deadline_seconds = self.command_timeout if timeout is None else timeout
        if deadline_seconds <= 0:
            raise ValueError("command deadline must be positive")
        with self._lock:
            header = self.read_header()
            profile = Profile(header.profile)
            validate_control_payload(command, data, profile)
            host_sequence = self._read_u32(FIELD_OFFSETS["control.host_seq"])
            host_ack = self._read_u32(FIELD_OFFSETS["control.host_ack"])
            if host_sequence != host_ack:
                raise MailboxBusy("firmware has an unacknowledged control request")
            sequence = (host_sequence + 1) & 0xFFFFFFFF
            body = pack_control_request_body(command, data)
            # 0x044..0x0bf contains all request fields except the trigger.
            self.transport.write_bytes(
                MAILBOX_ADDRESS + FIELD_OFFSETS["control.opcode"], body
            )
            # Publication trigger: one isolated word write, always last.
            self.transport.write_words(
                MAILBOX_ADDRESS + FIELD_OFFSETS["control.host_seq"], (sequence,)
            )

            deadline = self._monotonic() + deadline_seconds
            while True:
                current = self.read_header()
                if current.boot_counter != header.boot_counter:
                    raise MailboxResetDuringCommand(
                        "firmware reset before acknowledging the command; outcome is indeterminate"
                    )
                ack = self._read_u32(FIELD_OFFSETS["control.host_ack"])
                if ack == sequence:
                    response_raw = self.transport.read_bytes(
                        MAILBOX_ADDRESS + FIELD_OFFSETS["control.host_ack"],
                        CONTROL_OFFSET + 0x100 - FIELD_OFFSETS["control.host_ack"],
                    )
                    response = parse_control_response(response_raw, sequence)
                    if response.status is Status.RESET_DURING_COMMAND:
                        raise MailboxResetDuringCommand(
                            "firmware discarded the retained request during reset"
                        )
                    return response
                if ack != host_ack:
                    raise ProtocolError("firmware published an unexpected control ACK")
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise MailboxCommandTimeout("firmware control ACK deadline expired")
                self._sleep(min(self.poll_interval, remaining))

    def heartbeat_once(self) -> int:
        """Increment host_heartbeat once; callers schedule this every 250 ms."""
        with self._lock:
            if self._heartbeat_value is None:
                self._heartbeat_value = self._read_u32(
                    FIELD_OFFSETS["header.host_heartbeat"]
                )
            self._heartbeat_value = (self._heartbeat_value + 1) & 0xFFFFFFFF
            self.transport.write_words(
                MAILBOX_ADDRESS + FIELD_OFFSETS["header.host_heartbeat"],
                (self._heartbeat_value,),
            )
            return self._heartbeat_value

    def start_heartbeat(self) -> None:
        with self._lock:
            if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
                return
            self._heartbeat_stop.clear()
            self._heartbeat_error = None
            thread = threading.Thread(
                target=self._heartbeat_loop,
                name="uusb-mailbox-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread = thread
            thread.start()

    def stop_heartbeat(self) -> None:
        with self._lock:
            thread, self._heartbeat_thread = self._heartbeat_thread, None
            self._heartbeat_stop.set()
        if thread is not None:
            thread.join()

    @property
    def heartbeat_error(self) -> Exception | None:
        return self._heartbeat_error

    def close(self) -> None:
        self.stop_heartbeat()
        self.transport.close()

    def _heartbeat_loop(self) -> None:
        next_write = self._monotonic()
        while not self._heartbeat_stop.is_set():
            next_write += self.heartbeat_interval
            if self._heartbeat_stop.wait(max(0.0, next_write - self._monotonic())):
                return
            try:
                self.heartbeat_once()
            except Exception as error:
                # Preserve the exact failure for the daemon. Never retry this write.
                self._heartbeat_error = error
                return

    def _read_u32(self, offset: int) -> int:
        return self.transport.read_words(MAILBOX_ADDRESS + offset, 1)[0]
