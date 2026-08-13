# USB wiring continuity and power-safety checklist

Print one copy for each cable/board assembly. Perform every continuity and resistance measurement with **all equipment powered off and disconnected from mains and USB**.

## Job record

Assembly or cable ID: ______________________________  Date: __________________

Board serial or marking: ____________________________  Meter ID: ______________

Operator: ___________________________________________  Witness: ________________

## Mandatory power-off hold point

- [ ] **TARGET PC IS OFF AND ITS USB CABLE IS UNPLUGGED.**
- [ ] **CONTROLLER PC IS OFF OR THE ST-LINK IS UNPLUGGED.**
- [ ] **NO ST-LINK POWER-OUTPUT, 5 V, OR 3.3 V RAIL IS CONNECTED.**
- [ ] **THE BOARD HAS NO OTHER POWER SOURCE.**
- [ ] Meter confirms no powered voltage on VBUS: __________ V
- [ ] Meter confirms no powered voltage on 3.3 V: __________ V

Power-off hold-point operator signature: __________________  Time: __________

Witness signature: _______________________________________  Time: __________

**STOP:** do not continue unless every box above is checked and both signatures are present.

## Actual solder-side orientation record

Observe the connector from its **actual solder side**. Do not infer orientation from a generic plug drawing.

Connector manufacturer/part marking: _________________________________________

Viewed with cable exit / board edge pointing: _________________________________

Viewed with shield tabs / keying feature positioned: __________________________

Contact 1 physical location as viewed: ________________________________________

Contact 9 physical location as viewed: ________________________________________

Orientation sketch and contact-number annotations:

```text
+--------------------------------------------------------------------------+
|                                                                          |
|                                                                          |
|                                                                          |
|                                                                          |
+--------------------------------------------------------------------------+
```

Orientation recorded by: __________________________  Witness: ________________

## Nine-contact continuity map

Record an actual meter result for every Standard-A contact. `OL` is expected for deliberately unconnected SuperSpeed contacts. PB4–PB8 must not have continuity to contacts 5–9.

| Contact | Signal | Required destination/disposition | Measured destination | Resistance/result | Pass |
|---:|---|---|---|---|:---:|
| 1 | VBUS | Target-PC VBUS only; isolated from ST-Link power and other PC rails | __________________ | __________ | [ ] |
| 2 | D− | PA11, package pin 32, `USB_DM` | __________________ | __________ | [ ] |
| 3 | D+ | PA12, package pin 33, `USB_DP` | __________________ | __________ | [ ] |
| 4 | GND | Board signal ground | __________________ | __________ | [ ] |
| 5 | `StdA_SSRX−` | Unconnected; no F103 GPIO | __________________ | __________ | [ ] |
| 6 | `StdA_SSRX+` | Unconnected; no F103 GPIO | __________________ | __________ | [ ] |
| 7 | `GND_DRAIN` | Verified shield/drain design only; never GPIO | __________________ | __________ | [ ] |
| 8 | `StdA_SSTX−` | Unconnected; no F103 GPIO | __________________ | __________ | [ ] |
| 9 | `StdA_SSTX+` | Unconnected; no F103 GPIO | __________________ | __________ | [ ] |

- [ ] Contact 2 reaches PA11/package pin 32 and does not reach PA12.
- [ ] Contact 3 reaches PA12/package pin 33 and does not reach PA11.
- [ ] Contact 1 does not reach an ST-Link power-output pin or another PC's 5 V/3.3 V rail.
- [ ] Contacts 5, 6, 8, and 9 are unconnected.
- [ ] Contact 7 follows the recorded, verified shield/drain design and has no GPIO connection.
- [ ] PB4, PB5, PB6, PB7, and PB8 have no continuity to contacts 5–9.

Continuity-map operator signature: __________________  Witness: ________________

## D+ pull-up measurement

Meter connection: D+ (contact 3 / PA12) to the board's 3.3 V domain, equipment off.

Measured resistance: __________________ Ω

- [ ] Result is approximately 1.5 kΩ.
- [ ] A wrong-value or missing Blue Pill pull-up was repaired before USB testing, or no repair was needed.

Repair details or `not needed`: _______________________________________________

Verified by: ________________________________________  Date: __________________

## ST-Link/SWD and grounding boundary

| Connection | Required rule | Actual pin/measurement | Pass |
|---|---|---|:---:|
| SWDIO | Data connection only | __________________ | [ ] |
| SWCLK | Clock connection only | __________________ | [ ] |
| GND | Common only after ground-potential check, unless isolated | __________________ | [ ] |
| NRST | Reset connection only | __________________ | [ ] |
| VTref | Genuine ST-Link voltage-sense input only | __________________ | [ ] |
| ST-Link power output | Must remain disconnected | __________________ | [ ] |

Ground potential measured between the two disconnected-PC grounds: __________ V

- [ ] Ground potential is verified acceptable **or** an isolated debug link is fitted.
- [ ] Protective earth has not been disconnected, lifted, or defeated.
- [ ] The target PC will be the only VBUS source.
- [ ] The STM32F103 link is understood to be USB 2.0 full-speed only (12 Mbit/s), with no USB 3.x PHY.

## Final authorization before power

- [ ] Every contact 1–9 has a recorded result.
- [ ] The solder-side viewed orientation is recorded.
- [ ] D−/D+ destinations and the D+ pull-up passed.
- [ ] SuperSpeed contacts and PB4–PB8 passed the disconnect checks.
- [ ] VBUS, ST-Link VTref, grounding, isolation, and protective-earth rules passed.
- [ ] Any deviations were corrected and re-measured.

Deviation/correction record: __________________________________________________

______________________________________________________________________________

Operator final signature: ___________________________  Date/time: ______________

Witness final signature: ____________________________  Date/time: ______________

Authorization result:  [ ] **PASS—POWER MAY BE APPLIED**  [ ] **FAIL—DO NOT POWER**
