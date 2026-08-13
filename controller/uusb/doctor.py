"""Hardware-independent safety preflight for the future ``uusb doctor`` command."""

from __future__ import annotations

from typing import TextIO

from .safety import SafetyAcknowledgementState


def display_doctor_safety(
    state: SafetyAcknowledgementState, stream: TextIO | None = None
) -> None:
    """Display all doctor warnings without touching ST-Link or target hardware."""

    state.display(stream)


def authorize_first_hardware_operation(
    state: SafetyAcknowledgementState, acknowledgement: str
) -> None:
    """Authorize later hardware access after an exact, process-local acknowledgement.

    This function performs no hardware operation. Callers must invoke it only after
    :func:`display_doctor_safety`, then call ``require_before_hardware_operation``
    immediately before opening ST-Link or otherwise accessing hardware.
    """

    state.acknowledge(acknowledgement)
    state.require_before_hardware_operation()
