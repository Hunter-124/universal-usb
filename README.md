# Universal USB

Universal USB is a proof of concept in which a Linux controller PC drives an STM32F103C8T6 over ST-Link/SWD while the STM32 appears to a separate target PC as one of four USB 2.0 full-speed test devices. The original “USB hub” idea is implemented as standards-correct composite interfaces: this project does not emulate a hub.

> [!DANGER]
> **Do not connect or power the hardware until every contact has been continuity-mapped with all equipment powered off.** PA11/package pin 32 is USB D− and PA12/package pin 33 is USB D+. The target PC must be the only VBUS source. Never join an ST-Link power-output pin or another PC's 5 V/3.3 V rail. Standard-A SuperSpeed contacts 5, 6, 8, and 9 are unused and must not reach STM32 GPIO; contact 7 is shield/drain only under a verified design. Disconnect PB4–PB8 from contacts 5–9, verify the D+ pull-up is approximately 1.5 kΩ to 3.3 V, and verify PC ground potential or use an isolated debug link. Read [the hardware contract](docs/hardware.md) and sign [the continuity checklist](docs/continuity-checklist.md) first.

## Security-token threat model

The security-token profile is a **host-backed synthetic interoperability device, not a security boundary**. The STM32F103 has no secure element. The controller PC can read or replace every emulated credential, generate signatures, and approve synthetic user presence. Controller compromise or disconnection compromises or breaks the token. Never use it to protect real accounts, production keys, identity, or valuable secrets. It makes no Yubico identity, compatibility, certification, or attestation-root claim. Its purpose is browser, OS, installer, PC/SC, and automation testing.

## Topology

```text
 Linux controller PC                    STM32F103 Blue Pill                  target PC
 ┌──────────────────┐   ST-Link/SWD    ┌────────────────────┐   USB 2.0 FS  ┌───────────┐
 │ uusbd (sole owner)├─────────────────►│ 4 KiB SRAM mailbox │              │           │
 │ uusb CLI          │ SWDIO/SWCLK/GND │                    │ PA11 D− ─────►│ USB host  │
 │ images/credentials│ NRST + VTref     │ one flashed profile│ PA12 D+ ─────►│           │
 └──────────────────┘ (no power output)└────────────────────┘ GND ─────────►└───────────┘
                                                                          sole VBUS source

 Standard-A contacts 5/6/8/9: unconnected. Contact 7: verified shield/drain only.
 The controller and target are separate roles; same-machine development is not the final HIL topology.
```

## Capability matrix

All four personalities are mutually exclusive firmware images. VID `1209` and PIDs `000d`–`0010` are **private-test identities only**; obtain an assigned PID and update firmware, tests, and documentation before redistributing, selling, or shipping manufactured hardware.

| Profile | Target-facing interfaces | Test identity | Backing/generation | Verification status |
|---|---|---|---|---|
| `hid-msc` | Boot keyboard, boot-capable relative mouse, consumer HID, one BOT MSC LUN | `1209:000d` | Stateful HID; regular raw image served over SWD | Software tests only; target enumeration and I/O not yet hardware-verified |
| `microphone` | UAC1 capture, mono S16_LE, 48 kHz | `1209:000e` | Generated silence or phase-continuous tone; no sensor/input stream | Software tests only; ALSA capture not yet hardware-verified |
| `webcam` | UVC 1.5, YUY2 128×96 at 10 fps | `1209:000f` | Procedural bars/checker/gradient; no sensor, upload, or frame buffer | Software tests only; V4L2 streaming not yet hardware-verified |
| `security-token` | FIDO HID, CCID T=1 slot, OTP boot keyboard | `1209:0010` | Controller-backed FIDO/PIV-like state and test OTP | Software tests only; libfido2, PC/SC, and target enumeration not yet hardware-verified |

The USB 3.x wires are unused; the F103 has only a 12 Mbit/s USB 2.0 full-speed peripheral. Host-backed MSC crosses SWD and is intentionally slow; no sector latency or throughput has been measured on hardware yet.

## Prerequisites

Controller PC:

- Linux, Python 3.12+, Git with recursive submodule support
- CMake 3.24+, Ninja, GNU Arm Embedded GCC, and OpenOCD 0.12
- an ST-Link with SWDIO, SWCLK, GND, NRST, and genuine sense-only VTref

Target PC verification tools are optional until HIL work: `usbutils`, `evtest`, `dosfstools`/mtools, ALSA utilities, `v4l2-ctl`, `ffplay`, libfido2 tools, `pcscd`, and OpenSC.

Hardware prerequisites are not optional: complete the power-off continuity checklist, remove every forbidden SuperSpeed-to-GPIO connection, verify the D+ pull-up, and establish a safe grounding arrangement before attaching either PC.

## Build and first flash

```bash
git submodule update --init --recursive
cmake -S firmware -B build/firmware -G Ninja \
  -DBOARD=stm32f103_bluepill -DCMAKE_BUILD_TYPE=MinSizeRel
cmake --build build/firmware
python3 -m venv .venv
.venv/bin/python -m pip install -e controller
```

The build emits `.elf`, `.bin`, `.hex`, and `.map` artifacts for `uusb-hid-msc`, `uusb-microphone`, `uusb-webcam`, and `uusb-security-token`. It links against the guaranteed 64 KiB flash and 20 KiB SRAM limits, including the 4 KiB NOLOAD mailbox.

For a blank board, with the daemon stopped, flash one known image directly:

```bash
openocd -l /tmp/uusb-first-flash.log \
  -f interface/stlink.cfg -f target/stm32f1x.cfg \
  -c "program $PWD/build/firmware/uusb-hid-msc.elf verify reset" \
  -c shutdown
```

Start the daemon in one terminal only after reading the warning it prints. The acknowledgement is exact and process-local:

```bash
export UUSB_SAFETY_ACK='I HAVE VERIFIED THE POWER-OFF WIRING CHECKLIST'
.venv/bin/uusbd --firmware-dir "$PWD/build/firmware"
```

Use another terminal for the CLI:

```bash
.venv/bin/uusb doctor
.venv/bin/uusb status --json
```

`uusbd` alone owns ST-Link/OpenOCD, the attached image, and token state. `uusb` communicates over `$XDG_RUNTIME_DIR/universal-usb/control.sock`; it never opens ST-Link itself.

## Copy/paste showcase workflows

These are **expected HIL verification gates, not observations from completed hardware tests**. Run them only after the safety checklist. Commands for the controller use `.venv/bin/uusb`; commands marked “target” run on the target PC.

### 1. HID typing, pointer, and media controls

```bash
.venv/bin/uusb profile set hid-msc --yes
.venv/bin/uusb hid text 'Universal USB test' --interval-ms 30
.venv/bin/uusb hid key tap enter --hold-ms 40
.venv/bin/uusb hid mouse move --dx 240 --dy -120
.venv/bin/uusb hid mouse button click left
.venv/bin/uusb hid mouse scroll -3
.venv/bin/uusb hid consumer tap volume-up
.venv/bin/uusb hid consumer tap play-pause
```

**Expected target gate:** one composite device exposes keyboard, mouse, consumer control, and MSC interfaces; the exact text appears in a deliberately focused test field, pointer motion is relative, and usages release without sticking. Large pointer values are split into bounded signed 8-bit reports by the controller.

### 2. Attach a tiny FAT12 image read-only

```bash
tools/make-demo-image.sh /tmp/uusb-demo.img
.venv/bin/uusb msc attach /tmp/uusb-demo.img
.venv/bin/uusb msc status
# Inspect UUSB.TXT on the target, then safely unmount/eject it there.
.venv/bin/uusb msc detach
```

**Expected target gate:** a 1.44 MiB FAT12 volume labelled `UUSBDEMO` becomes readable and contains `UUSB.TXT`. The default attach is read-only. Remote storage over SWD is intentionally slow and remains unmeasured; a controller failure must become failed I/O or medium-absent, never a successful zero-filled sector.

### 3. Select and record the generated microphone tone

Controller:

```bash
.venv/bin/uusb profile set microphone --yes
.venv/bin/uusb mic tone --frequency-hz 1000 --level-dbfs -12
.venv/bin/uusb mic status
```

Target (replace the ALSA device after inspecting `arecord -l`):

```bash
arecord -l
export UUSB_ALSA_DEVICE=hw:2,0
arecord -D "$UUSB_ALSA_DEVICE" -t wav -f S16_LE -r 48000 -c 1 -d 5 /tmp/uusb-tone.wav
```

Analyze on either Linux machine:

```bash
tools/analyze-wav.py /tmp/uusb-tone.wav \
  --expect-duration-seconds 5 --duration-tolerance-seconds 0.10 \
  --expect-frequency-hz 1000 --frequency-tolerance-hz 20
.venv/bin/uusb mic silence
```

**Expected target gate:** the device enumerates as UAC1 capture-only, records mono signed 16-bit PCM at 48 kHz, and the analyzer finds a five-second tone whose dominant frequency is within ±20 Hz of 1 kHz. This is generated audio, not a microphone sensor.

### 4. View the UVC test pattern

Controller:

```bash
.venv/bin/uusb profile set webcam --yes
.venv/bin/uusb cam pattern bars
.venv/bin/uusb cam status
```

Target (replace the device node after inspecting the list):

```bash
v4l2-ctl --list-devices
export UUSB_VIDEO_DEVICE=/dev/video2
v4l2-ctl -d "$UUSB_VIDEO_DEVICE" --list-formats-ext
ffplay -f v4l2 -pixel_format yuyv422 -video_size 128x96 -framerate 10 "$UUSB_VIDEO_DEVICE"
```

Switch patterns from the controller with:

```bash
.venv/bin/uusb cam pattern checker
.venv/bin/uusb cam pattern gradient
```

**Expected target gate:** exactly YUYV 128×96 at 10 fps is advertised, the selected procedural pattern is visible, and its frame counter moves. This profile has no camera sensor, arbitrary image upload, or audio function.

### 5. Verify profile-dependent descriptors

```bash
.venv/bin/uusb profile set hid-msc --yes
.venv/bin/uusb status --json
.venv/bin/uusb profile set microphone --yes
.venv/bin/uusb status --json
.venv/bin/uusb profile set webcam --yes
.venv/bin/uusb status --json
.venv/bin/uusb profile set security-token --yes
.venv/bin/uusb status --json
```

On a Linux target after each switch:

```bash
lsusb -d 1209:000d -v
lsusb -d 1209:000e -v
lsusb -d 1209:000f -v
lsusb -d 1209:0010 -v
```

Only the command matching the active profile should find a device. **Expected target gate:** each successful switch includes a fresh boot, an observed unmounted-to-mounted transition, advancing mailbox uptime, the expected PID/product, and target re-enumeration. A firmware-controlled 20 ms D+ disconnect alone is not proof of success.

### 6. Test-only FIDO, CCID, and OTP interoperability

```bash
.venv/bin/uusb profile set security-token --yes
.venv/bin/uusb token status
# In a disposable WebAuthn registration/assertion flow, issue synthetic presence:
.venv/bin/uusb token touch
# Inspect the synthetic smart-card slot on the target:
opensc-tool --list-readers
# Provisioning prompts for a Base32 test secret; no default secret is shipped.
.venv/bin/uusb token otp provision demo --type totp --digits 6 --period 30
.venv/bin/uusb token otp select demo
.venv/bin/uusb token touch
```

**Expected target gate:** a standards client can exercise the bounded FIDO CTAP2 surface, PC/SC/OpenSC can exchange supported PIV-like APDUs through one T=1 slot, and the selected test OTP is typed only after explicit controller touch. Use a disposable relying party, test PIN, and test secret. This is not production authentication and no successful target-host interoperability is claimed yet.

The installer navigation example is described in [the showcase guide](docs/showcase.md) and checked in as [`examples/windows-installer.json`](examples/windows-installer.json). It requires a user-supplied, legally prepared raw image; the repository includes no Microsoft media, keys, or unattended-install claim.

## Architecture and protocol

- [Architecture](docs/architecture.md): firmware profiles, endpoint/PMA budgets, daemon ownership, lifecycle, and scenario boundary.
- [Mailbox and RPC protocol](docs/protocol.md): the fixed 4 KiB ABI, CRC and publication rules, operations, statuses, and private newline-delimited JSON RPC.
- [Showcase and verification ledger](docs/showcase.md): expected target checks and the distinction between software and hardware evidence.
- [Hardware wiring](docs/hardware.md): complete electrical contract and controlled D+ disconnect.

The mailbox is reserved at `0x20000000` and is never zeroed by normal startup. Multi-byte values are little-endian, CRCs are CRC-32C, triggers and acknowledgements are written last, and firmware uptime—not retained magic alone—proves liveness. The controller heartbeat advances every 250 ms; loss for one second releases HID state and invalidates pending media/token work.

## Limitations and results

| Item | Current evidence | Honest boundary |
|---|---|---|
| Four 64 KiB firmware profiles | Build configuration and software tests | No target USB enumeration has been observed in this release work |
| Mailbox, HID state, MSC state, tone, pattern, FIDO/CCID/OTP helpers | Native C and Python tests | Tests do not prove SWD signal integrity, USB timing, OS interoperability, or physical release behavior |
| USB packet-memory budgets | Compile-time assertions and descriptor tests | Static accounting only; not a hardware trace |
| HID/MSC throughput | Not measured | SWD-backed MSC is intentionally slow; no latency or throughput promise |
| Microphone/webcam stability | Not measured on target hardware | Synthetic data only; no sensor ingestion claim |
| Security-token interoperability | Host software tests only | No certification, production security, or completed libfido2/PCSC target gate |
| Two-PC topology | Designed and documented | Final two-PC HIL remains pending; same-machine development is a different topology |

## Troubleshooting

- **`uusbd` rejects startup:** use the exact process-local safety phrase only after completing the current checklist. Confirm `XDG_RUNTIME_DIR` is set.
- **OpenOCD cannot connect:** stop any other OpenOCD/debugger, check SWDIO/SWCLK/GND/NRST/VTref, BOOT0 low, and genuine sense-only VTref. Do not add a power rail.
- **Mailbox magic exists but commands time out:** retained bytes are not liveness. Run `uusb doctor`; uptime must advance across two reads and the core must be running.
- **Profile switch times out with exit 8:** the target did not prove a fresh unmounted-to-mounted transition. Manually unplug/reconnect target USB or implement the documented controlled pull-up remedy; do not treat flashing alone as success.
- **Target does not enumerate:** re-check D−/D+ continuity, the approximately 1.5 kΩ D+ pull-up, sole-source VBUS, 8 MHz HSE, and forbidden SuperSpeed contacts before debugging software.
- **MSC reports not ready:** keep `uusbd` alive, use a nonzero regular image whose size is divisible by 512, and unmount/eject on the target before detach.
- **Commands report profile mismatch:** switch explicitly with `uusb profile set PROFILE --yes`; commands never flash implicitly.
- **Token operation waits:** issue `uusb token touch` only for a deliberate disposable test. A controller disconnect is a failure, not successful presence.

## Development verification

CI performs software-only checks:

```bash
python3 tools/verify-tinyusb.py
cmake -S firmware -B build/firmware -G Ninja \
  -DBOARD=stm32f103_bluepill -DCMAKE_BUILD_TYPE=MinSizeRel
cmake --build build/firmware
ctest --test-dir build/firmware --output-on-failure
python3 -m unittest discover -s controller/tests
```

CI recursively initializes submodules, keeps the TinyUSB gitlink pristine, builds all four profiles, checks flash/SRAM limits, runs six native helper tests, and runs the Python suite including descriptor/PMA goldens. It **does not** test physical USB, SWD, ST-Link, USB disconnect/re-enumeration, target OS interoperability, or DMA hardware.

## License

Project-owned firmware, controller, tools, examples, and documentation are licensed under the [MIT License](LICENSE). TinyUSB remains under its own upstream license as a pinned submodule.

## Related project

[https://github.com/Hunter-124/dma-display](https://github.com/Hunter-124/dma-display) is an optional, separately operated, hardware-specific visual-feedback companion. Universal USB does not import, clone, launch, configure, version, build, or test it. An operator may independently run the following from that project's own checkout, then issue Universal USB commands by hand:

```bash
./run.sh view
./run.sh shot -o /tmp/target.png
```

There is no source import, HTTP polling, subprocess invocation, shared socket, or automated dependency between the projects.
