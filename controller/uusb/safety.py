"""Electrical safety warnings and in-process acknowledgement gating.

This module deliberately has no hardware dependencies. A caller can always print the
warnings, including when ST-Link or the target board is absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import sys
from typing import TextIO

WARNING_REVISION = "hardware-safety-v1"
ACKNOWLEDGEMENT_PHRASE = "I HAVE VERIFIED THE POWER-OFF WIRING CHECKLIST"

SAFETY_WARNING = f"""\
UNIVERSAL USB — MANDATORY HARDWARE SAFETY CHECK ({WARNING_REVISION})

POWER EVERYTHING OFF before probing or changing wiring. From the actual connector
solder side, record the viewed orientation and continuity-map all nine Standard-A
contacts:
  1 VBUS: target-PC supply only.
  2 D-: must reach STM32 PA11, package pin 32, USB_DM.
  3 D+: must reach STM32 PA12, package pin 33, USB_DP.
  4 GND: signal ground, subject to the ground-potential rule below.
  5 StdA_SSRX-: leave unconnected; never connect to F103 GPIO.
  6 StdA_SSRX+: leave unconnected; never connect to F103 GPIO.
  7 GND_DRAIN: use only in a verified shield/drain design; never connect to GPIO.
  8 StdA_SSTX-: leave unconnected; never connect to F103 GPIO.
  9 StdA_SSTX+: leave unconnected; never connect to F103 GPIO.
Disconnect PB4-PB8 from contacts 5-9 before applying power.

The STM32F103 is USB 2.0 full-speed only (12 Mbit/s); it has no USB 3.x PHY.
With power off, measure approximately 1.5 kOhm from D+ to the 3.3 V domain.
Repair a wrong-value or missing Blue Pill D+ pull-up before USB testing.

The target PC is the ONLY VBUS source. Do not connect an ST-Link power-output pin
or another PC's 5 V or 3.3 V rail. Permitted ST-Link signals are SWDIO, SWCLK,
GND, NRST, and genuine ST-Link VTref as a voltage-sense INPUT ONLY.
Verify the two PCs' ground potential or use an isolated debug link. Never defeat
protective earth.

With the fixed D+ pull-up, firmware disconnect must disable USB, hold PA11 as an
input/high-impedance pin while driving PA12 low for 20 ms, restore both pins to
USB mode, and only then initialize TinyUSB. If that cannot force re-enumeration,
use a GPIO-controlled D+ pull-up/transistor or manually unplug USB.

A firmware-controlled D+ disconnect is not proof of re-enumeration. A profile
switch must not be reported successful until the target host re-enumerates and
the expected profile is independently verified.

Read and sign docs/continuity-checklist.md before hardware access.
To acknowledge this warning for this process only, enter exactly:
  {ACKNOWLEDGEMENT_PHRASE}
"""


class SafetyAcknowledgementRequired(RuntimeError):
    """Hardware access was attempted without a current acknowledgement."""


class SafetyAcknowledgementInvalid(ValueError):
    """Acknowledgement text was not the one exact accepted phrase."""


def emit_safety_warnings(stream: TextIO | None = None) -> None:
    """Print the complete safety warning without opening or probing hardware."""

    destination = sys.stderr if stream is None else stream
    destination.write(SAFETY_WARNING)
    if not SAFETY_WARNING.endswith("\n"):
        destination.write("\n")
    destination.flush()


@dataclass(slots=True)
class SafetyAcknowledgementState:
    """Process-local gate for the first hardware operation.

    Acknowledgement is deliberately not persisted. It is accepted only after this
    object displayed the current warning revision, so constructing an uninitialized
    state cannot bypass the warning.
    """

    _displayed_revision: str | None = field(default=None, init=False, repr=False)
    _acknowledged_revision: str | None = field(default=None, init=False, repr=False)

    def display(self, stream: TextIO | None = None) -> None:
        """Display warnings and make this state eligible for acknowledgement."""

        emit_safety_warnings(stream)
        self._displayed_revision = WARNING_REVISION
        self._acknowledged_revision = None

    def acknowledge(self, phrase: str) -> None:
        """Accept only the exact phrase after the current warning was displayed."""

        if self._displayed_revision != WARNING_REVISION:
            raise SafetyAcknowledgementRequired(
                "display the current hardware safety warning before acknowledging it"
            )
        if phrase != ACKNOWLEDGEMENT_PHRASE:
            raise SafetyAcknowledgementInvalid(
                "hardware safety acknowledgement phrase did not match exactly"
            )
        self._acknowledged_revision = WARNING_REVISION

    @property
    def acknowledged(self) -> bool:
        """Whether this process displayed and acknowledged the current warning."""

        return (
            self._displayed_revision == WARNING_REVISION
            and self._acknowledged_revision == WARNING_REVISION
        )

    def require_before_hardware_operation(self) -> None:
        """Reject access unless current warnings were displayed and acknowledged."""

        if not self.acknowledged:
            raise SafetyAcknowledgementRequired(
                "hardware access requires the displayed safety warning and exact "
                "process-local acknowledgement"
            )
