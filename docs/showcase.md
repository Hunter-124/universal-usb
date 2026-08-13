# Showcase and verification ledger

This guide separates reproducible commands, expected target observations, software evidence, and hardware evidence. On 2026-08-13, one Fedora machine supplied both ST-Link control and target-facing USB for a controlled development HIL run. That run is evidence only for the exact results below; it is not the final separated two-PC topology and did not exercise a Windows installer.

> Complete [the power-off continuity checklist](continuity-checklist.md) before any command that opens ST-Link or connects target USB. The target PC is the only VBUS source; contacts 5/6/8/9 remain unconnected and contact 7 is never GPIO.

## Evidence labels

- **Software-verified:** deterministic host/native tests or build/link checks exercise the behavior without physical USB/SWD hardware.
- **Expected HIL gate:** the observation required from real controller/STM32/target hardware before a claim can be promoted.
- **Not measured:** no number or success result exists; the document does not substitute a model, estimate, or CI result.

The verified software baseline builds four firmware images and passes 111 controller tests plus 7 native C tests. CI repeats these software checks; the hardware evidence below comes from separate physical runs.

## Results and limitations

| Capability | Software evidence | Physical result | Release wording |
|---|---|---|---|
| Four profile images within 64 KiB flash / 20 KiB SRAM | Build/link size gates | All four private PIDs and profile-specific descriptors enumerated on Fedora | Verified on the same-machine development topology |
| Fixed mailbox/control/block/token layouts | C/Python offset, CRC, golden-vector, sequence, and parser tests | Live ST-Link command, heartbeat, flash, profile, block, and token exchanges succeeded | Verified on one board/probe; not a signal-integrity characterization |
| HID state/release and mouse splitting | Native and controller state tests | Fedora libinput captured modifier press/release, relative `+1/-1` pointer motion, and consumer Stop press/release; forced daemon loss cleared release-pending state | Full key/button/consumer matrix, boot protocol, and suspend/resume remain open |
| FAT12 image builder and MSC backing | Deterministic tool and media-state tests | Read-only image mounted/read; explicit read-write attach persisted a target-created file; 1,474,560-byte sequential read took 88.6992 s; forced daemon loss caused target `EIO` after 135,168 bytes | 16.6 kB/s and 30.8 ms/sector average on this run only; physical ST-Link unplug remains open |
| UAC1 tone/silence | Descriptor and tone-helper tests | Five-second mono S16_LE/48 kHz recording measured 1000.00 Hz, peak 8191; silence maximum was 0 | Synthetic source; no sensor claim |
| UVC patterns | Descriptor and pattern-helper tests | Coherent 128×96 frames captured; bars/checker/gradient each streamed for 120 seconds without ffmpeg transfer errors | Synthetic patterns; no camera claim |
| FIDO/CCID/OTP | Framing, CTAP2, CCID/APDU, RFC vectors, state/durability tests | libfido2/python-fido2 registration/assertion; pcscd/OpenSC reader and PIV SELECT; raw CCID CHUID; six captured OTP press/release pairs with durable counter | No browser UI, certification, production security, or full PIV VERIFY/GENERAL AUTHENTICATE claim |
| Controlled D+ disconnect/profile verification | Lifecycle and profile-state tests | Ten consecutive profile switches verified fresh mounted transitions and expected profiles | One Blue Pill board only |
| Two-PC topology | Architecture review | Not exercised; development HIL used one Fedora machine for both roles | Final separated HIL gate remains open |

No USB electrical measurements, long-run audio statistics, browser-UI transaction, Windows-install result, or two-PC result is claimed. The MSC number above is one measured run, not a performance promise.

## Preparation

Build, first-flash, and daemon startup are in the [README](../README.md). Before each workflow:

```bash
.venv/bin/uusb doctor
.venv/bin/uusb status --json
```

`doctor` must observe a running core and advancing uptime. Valid retained mailbox magic alone is insufficient.

For target-side inspection, identify device nodes dynamically; `/dev/video2`, `hw:2,0`, and reader indices in examples are placeholders, not stable assignments.

## Workflow 1: composite HID

Controller:

```bash
.venv/bin/uusb profile set hid-msc --yes
.venv/bin/uusb hid key down left-shift
.venv/bin/uusb hid key tap u --hold-ms 40
.venv/bin/uusb hid key up left-shift
.venv/bin/uusb hid text 'niversal USB test' --interval-ms 30
.venv/bin/uusb hid key tap enter --hold-ms 40
.venv/bin/uusb hid mouse move --dx 300 --dy -180
.venv/bin/uusb hid mouse button click left
.venv/bin/uusb hid mouse scroll -4
.venv/bin/uusb hid consumer tap mute
.venv/bin/uusb hid consumer tap play-pause
```

Expected HIL gate:

1. `lsusb -d 1209:000d -v` shows one interface-classed composite, not a hub.
2. Interfaces are a boot keyboard on `0x81`, boot-capable mouse on `0x82`, consumer HID on `0x83`, and BOT MSC on `0x04/0x84`.
3. Run input commands only into a deliberately focused disposable field. The text, relative movement, click, scroll, and media usages arrive once.
4. A daemon disconnect with a usage held produces accepted zero reports within the heartbeat-loss boundary. Reset and suspend/resume make zero reports the first transfers after remount/resume.
5. Boot and report mouse protocols both work; wheel is suppressed in boot protocol.

Observed subset: `1209:000d` enumerated the exact four interfaces/endpoints; Fedora libinput captured a modifier press/release, `+1/-1` relative pointer motion, and consumer Stop press/release. Forced daemon heartbeat loss cleared release-pending state. The full key/button/consumer matrix, boot protocol, and suspend/resume remain open.

## Workflow 2: deterministic FAT12 image

The builder requires local `mkfs.fat`, mtools `mcopy`, Python 3, and ordinary POSIX utilities. It downloads nothing.

```bash
tools/make-demo-image.sh /tmp/uusb-demo.img
sha256sum /tmp/uusb-demo.img
.venv/bin/uusb msc attach /tmp/uusb-demo.img
.venv/bin/uusb msc status
```

Target:

```bash
lsblk -f
# Mount through the desktop or with the target's normal removable-media policy.
# Read UUSB.TXT, then sync and unmount/eject before detaching on the controller.
```

Controller:

```bash
.venv/bin/uusb msc detach
```

Expected HIL gate:

1. Target sees one 1.44 MiB FAT12 volume labelled `UUSBDEMO` and reads `UUSB.TXT` exactly.
2. Default attach reports write protection.
3. A separate explicit `--read-write` run can create a small target file; after sync/unmount/detach, the file is present in the controller-side image.
4. Pulling ST-Link during a read yields failed I/O or medium-not-present, never a successful fabricated sector.
5. Record sector latency and throughput from the actual hardware before publishing any performance number.

Observed: the default read-only 1.44 MiB FAT12 image mounted with label `UUSBDEMO` and yielded the exact marker text. An explicit read-write attach persisted a target-created file after sync/unmount/detach. A sequential 1,474,560-byte target read took 88.6992 seconds (16.6 kB/s; 30.8 ms per sector average). Forced daemon loss during a separate read yielded target `Input/output error` after 135,168 bytes rather than false sector success. A physical ST-Link unplug remains open; this single performance measurement is not a promise.

## Workflow 3: UAC1 generated tone

Controller:

```bash
.venv/bin/uusb profile set microphone --yes
.venv/bin/uusb mic tone --frequency-hz 1000 --level-dbfs -12
.venv/bin/uusb mic status
```

Target:

```bash
lsusb -d 1209:000e -v
arecord -l
export UUSB_ALSA_DEVICE=hw:2,0
arecord -D "$UUSB_ALSA_DEVICE" --dump-hw-params
arecord -D "$UUSB_ALSA_DEVICE" -t wav -f S16_LE -r 48000 -c 1 -d 5 /tmp/uusb-tone.wav
```

Analyzer:

```bash
tools/analyze-wav.py /tmp/uusb-tone.wav \
  --expect-duration-seconds 5 --duration-tolerance-seconds 0.10 \
  --expect-frequency-hz 1000 --frequency-tolerance-hz 20
```

Then:

```bash
.venv/bin/uusb mic silence
```

Expected HIL gate:

1. Descriptors report Audio Class 1.0 capture-only, mono signed 16-bit PCM, one discrete 48 kHz rate, and 96-byte 1 ms isochronous IN packets.
2. The WAV analyzer accepts channel count, sample width, rate, duration, and dominant frequency within ±20 Hz.
3. A separate silence capture contains near-zero samples.
4. Firmware packet/underrun counters remain credible during capture.

Observed: Fedora ALSA accepted mono S16_LE at 48 kHz; a five-second capture analyzed at 1000.00 Hz with peak 8191, and a separate silence capture had maximum absolute sample 0. “Microphone” names a generated USB class function; no sensor is present.

## Workflow 4: UVC generated patterns

Controller:

```bash
.venv/bin/uusb profile set webcam --yes
.venv/bin/uusb cam pattern bars
.venv/bin/uusb cam status
```

Target:

```bash
lsusb -d 1209:000f -v
v4l2-ctl --list-devices
export UUSB_VIDEO_DEVICE=/dev/video2
v4l2-ctl -d "$UUSB_VIDEO_DEVICE" --list-formats-ext
ffplay -f v4l2 -pixel_format yuyv422 -video_size 128x96 -framerate 10 "$UUSB_VIDEO_DEVICE"
```

While streaming, switch from the controller:

```bash
.venv/bin/uusb cam pattern checker
.venv/bin/uusb cam pattern gradient
.venv/bin/uusb cam pattern bars
```

Expected HIL gate:

1. The target advertises exactly uncompressed YUYV 128×96 at 10 fps.
2. Bars, checker, and gradient are visually distinct and the embedded frame counter advances.
3. Each pattern streams for the required long-run test without malformed transfers or unstable counters.

Observed: coherent moving 128×96 frames were captured, and bars, checker, and gradient each streamed through ffmpeg for 120 seconds without transfer errors. The function has no image sensor, upload path, MJPEG encoder, audio, or full-frame allocation.

## Workflow 5: profile switch and descriptor separation

Controller:

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

Target after each switch:

```bash
lsusb -d 1209:000d -v
lsusb -d 1209:000e -v
lsusb -d 1209:000f -v
lsusb -d 1209:0010 -v
```

Expected HIL gate:

- only the active private-test PID appears;
- product and interface descriptors change with the profile;
- daemon status agrees with the profile mailbox ID;
- each reported success includes fresh boot, observed unmounted state, mounted state, and advancing uptime;
- ten complete switch cycles re-enumerate without stale descriptors.

Observed: ten consecutive profile switches verified fresh mounted transitions and expected mailbox profile IDs; target descriptors/PIDs changed across all four personalities without stale descriptors.

## Workflow 6: host-backed token interoperability

### Non-production threat model

All credentials, keys, PIN state, OTP secrets, counters, and presence decisions are controlled by the Linux controller. The F103 contains no secure element. Use disposable relying parties, project-generated certificates, test PINs, and test OTP secrets only. Do not use real accounts or identity.

Controller:

```bash
.venv/bin/uusb profile set security-token --yes
.venv/bin/uusb token status
```

Target FIDO discovery:

```bash
fido2-token -L
fido2-token -I /dev/hidrawN
```

Start a disposable browser or standards-client registration/assertion operation, then deliberately issue synthetic presence from the controller:

```bash
.venv/bin/uusb token touch
.venv/bin/uusb token credential list
```

Target CCID/PIV-like discovery uses an explicit private-test registration. The installer idempotently appends only `1209:0010` to the installed libccid metadata; rerun it after libccid package upgrades:

```bash
sudo tools/install-ccid-test-reader.sh
sudo systemctl restart pcscd.socket pcscd.service
opensc-tool --list-readers
opensc-tool --reader 0 --atr
```

Controller OTP test (the command prompts for a Base32 secret and does not echo/log it):

```bash
.venv/bin/uusb token otp provision demo --type totp --digits 6 --period 30
.venv/bin/uusb token otp select demo
.venv/bin/uusb token touch
.venv/bin/uusb token otp remove demo
```

Expected HIL gate:

1. USB descriptors show generic `1209:0010`, FIDO HID `0x01/0x81`, CCID `0x02/0x82/0x83`, and OTP keyboard `0x84` within the exact 464-byte PMA budget.
2. A standards FIDO client completes GetInfo, test registration, and test assertion with ES256 and `none` attestation; unsupported operations return defined errors.
3. `pcscd`/OpenSC sees one synthetic T=1 slot; power/status/parameters/transfer/abort/time-extension behavior works, and supported PIV-like SELECT/GET DATA/VERIFY/GENERAL AUTHENTICATE APDUs interoperate.
4. TOTP/HOTP vectors match RFC 6236/4226 tests. On target hardware, OTP types only after touch, always releases keys, and commits HOTP only after an accepted transfer.
5. Controller loss, timeout, stale sequence, and bad CRC become failures rather than fabricated FIDO/CCID success.

Observed: generic descriptors enumerated; libfido2 listed the token and decoded GetInfo; independent python-fido2 completed `none`-attestation registration and parsed an assertion, with direct ES256 signature verification. After explicit private-ID registration, pcscd/OpenSC found the T=1 reader and PIV SELECT returned `9000`; raw CCID also completed CHUID GET DATA. Target input captured six OTP press/release pairs, and the RFC 4226 counter advanced only after accepted transfer. Full browser UI, PIV VERIFY/GENERAL AUTHENTICATE through OpenSC, and certification remain outside this evidence.

## Prepared installer navigation scenario

[`../examples/windows-installer.json`](../examples/windows-installer.json) is a closed scenario demonstrating attach plus boot-menu key primitives. It contains the literal placeholder `/replace/with/your/prepared-installer.img`.

1. Legally prepare a raw installer disk image yourself. The repository contains no Microsoft media or keys.
2. Copy the example and replace the placeholder with an absolute path to that regular image.
3. Validate all steps and the image before execution:

```bash
cp examples/windows-installer.json /tmp/windows-installer.json
# Edit /tmp/windows-installer.json and replace the image placeholder.
.venv/bin/uusb scenario validate /tmp/windows-installer.json
```

4. Put the target in a controlled test state, arrange any manual reboot/power action independently, and run only when boot-menu keystrokes cannot damage data:

```bash
.venv/bin/uusb scenario run /tmp/windows-installer.json --switch-profile --json
```

Expected HIL boundary: firmware/boot-manager detection and initial boot-file reads must be observed before even a bootability claim is made. The scenario does not automate firmware reset, choose a universally correct boot menu, provide answers/keys, or claim an unattended or completed Windows installation. No installer run has been performed.

## Independent visual-feedback handoff

Visual feedback is optional and separately operated. From the companion's own checkout, a human/operator may independently run either:

```bash
./run.sh view
./run.sh shot -o /tmp/target.png
```

The operator then inspects the result and manually issues Universal USB commands. Universal USB never imports its source, invokes those commands, polls HTTP, shares a socket, or treats visual output as an automated dependency. The documentation-only companion link is in the README.

## Recording future evidence

A hardware result may replace an “expected” statement only when the exact topology, target OS/tool version, USB descriptors, command transcript, failure behavior, and measured quantity are captured. Do not promote CI, an STM32-side counter, or a D+ pulse into evidence that a target host accepted USB traffic.
