# Architecture

Universal USB separates the trusted controller process, a deliberately small STM32F103 USB/SWD transport, and the target USB host. It is a laboratory interoperability system, not a USB hub, a general USB proxy, or a production security token.

## System boundary

```text
controller CLI ── private NDJSON/Unix socket ──► uusbd
                                                     │
                                          one persistent OpenOCD
                                                     │
                                                  ST-Link/SWD
                                                     │
                                               4 KiB mailbox
                                                     │
                                          one STM32 USB profile
                                                     │
                                              target USB host
```

`uusbd` is the sole owner of:

- the persistent OpenOCD process and all mailbox reads/writes;
- profile flashing and post-flash verification;
- the attached raw media image and its advisory lock;
- FIDO credentials, PIV-like state, PIN state, and OTP secrets;
- heartbeat, timeout, release, and failure handling.

The CLI never opens ST-Link. One request and one response cross the private Unix socket for each command. Firmware owns USB framing, endpoint state, fixed-size mailbox publication, and safe local release behavior; the Linux controller owns files, protocol state, and cryptography.

## Why four firmware profiles

The STM32F103 has one USB full-speed controller, 512 bytes of packet memory, 20 KiB of guaranteed SRAM, and 64 KiB of guaranteed flash. A/V and security interfaces are therefore separate, mutually exclusive firmware personalities rather than one oversized composite. The `hid-msc` personality is itself one standards-correct composite device, not a hub.

Every profile uses EP0 with 64-byte packets. The PMA totals below include a 64-byte BTABLE and both 64-byte EP0 directions.

| Profile / ID / private test PID | Interfaces and endpoints | PMA allocation | Total |
|---|---|---|---:|
| `hid-msc` / 1 / `1209:000d` | Keyboard interrupt IN `0x81`, 8 B, 10 ms; mouse interrupt IN `0x82`, 4 B report/8 B buffer, 5 ms; consumer interrupt IN `0x83`, 2 B, 10 ms; MSC bulk OUT `0x04` and IN `0x84`, 64 B each | BTABLE 64 + EP0 128 + keyboard 8 + mouse 8 + consumer 2 + MSC 128 | **338 B** |
| `microphone` / 2 / `1209:000e` | UAC1 AudioControl plus AudioStreaming alt 0/no endpoint and alt 1; isochronous IN `0x81`, 96 B every 1 ms | BTABLE 64 + EP0 128 + audio IN 96 | **288 B** |
| `webcam` / 3 / `1209:000f` | UVC 1.5 control plus streaming alt 0/no endpoint and alt 1; isochronous IN `0x81`, 256 B every 1 ms | BTABLE 64 + EP0 128 + video IN 256 | **448 B** |
| `security-token` / 4 / `1209:0010` | FIDO HID interrupt OUT `0x01` and IN `0x81`, 64 B, 5 ms; CCID bulk OUT `0x02` and IN `0x82`, 64 B, plus interrupt IN `0x83`, 8 B, 16 ms; OTP keyboard interrupt IN `0x84`, 8 B, 10 ms | BTABLE 64 + EP0 128 + FIDO 128 + CCID bulk 128 + CCID interrupt 8 + OTP 8 | **464 B** |

These VID/PIDs are private test identities. They must be replaced with an assigned PID before redistributed, sold, or manufactured hardware is shipped.

### HID and remotely backed media

The HID/MSC profile exposes four interfaces:

1. a standard 8-byte boot-keyboard report;
2. a relative mouse whose report protocol includes buttons/X/Y/wheel and whose boot protocol emits buttons/X/Y only;
3. a 16-bit consumer-control usage;
4. one SCSI-transparent BOT LUN.

The controller maintains complete HID state. `down`, `up`, and `tap` send full reports, text uses the documented US layout, and more than six simultaneous non-modifier keys is rejected. Large mouse movement/scroll requests become deterministic signed 8-bit reports. Heartbeat loss, disconnect, command failure, reset, suspend, and remount all converge on zero reports rather than fire-and-forget usage packets.

MSC uses one 512-byte asynchronous slot. The target's READ(10), WRITE(10), and SYNCHRONIZE CACHE requests are resolved by the daemon against a locked regular raw image. The image is validated before attach, is read-only unless `--read-write` is explicit, is never truncated, and successful writes are positionally written and `fsync`ed before acknowledgement. Exactly one operation can be pending. Media epoch plus request sequence rejects late responses after detach, BOT reset, unmount, heartbeat loss, or daemon failure.

This path deliberately trades speed for a minimal existing ST-Link/SWD connection. No hardware latency or throughput result exists yet.

### Synthetic microphone

The microphone profile is a project-authored UAC1 capture function:

- Audio Class version 1.0;
- one microphone input terminal feeding one USB-streaming output terminal;
- mono PCM Type-I, signed 16-bit, exactly 48,000 Hz;
- 96-byte isochronous packets every 1 ms;
- generated phase-continuous tone or silence.

It has no sensor and is not an arbitrary audio-streaming bridge. Tone generation is allocation-free per packet. Commands bound frequency to 20–20,000 Hz and amplitude to unsigned Q15 range.

### Synthetic webcam

The webcam profile is one UVC 1.5 capture function. It advertises exactly uncompressed YUY2/YUYV at 128×96 and 10 fps over a 256-byte isochronous endpoint. Bars, checker, and gradient frames are generated procedurally with a visible frame counter. There is no 24,576-byte frame buffer, camera sensor, MJPEG encoder, arbitrary image upload, or A/V composite claim.

### Host-backed synthetic security token

The security profile is one interface-classed composite with FIDO Alliance HID, one CCID T=1 slot, and an OTP boot keyboard. Firmware handles only USB framing, bounded mailbox exchange, zero-report release, and keepalive timing. The daemon implements:

- CTAPHID `INIT`, `PING`, `WINK`, `CBOR`, `CANCEL`, `KEEPALIVE`, and `ERROR` behavior;
- CTAP2 `authenticatorGetInfo`, `authenticatorMakeCredential`, `authenticatorGetAssertion`, `authenticatorGetNextAssertion`, and `authenticatorReset`;
- ES256/P-256 key generation and signatures via the system OpenSSL executable, strict DER/COSE handling, and `none` attestation;
- a bounded PIV-like APDU surface over CCID for SELECT, GET DATA, VERIFY of a test PIN, and GENERAL AUTHENTICATE;
- controller-triggered RFC 4226/6238 test OTP typing.

The controller can read/replace all of that state and can synthesize presence. The F103 has no secure element. The profile must never protect real accounts, keys, or identity, and it claims no product identity or certification.

## Firmware platform and boot lifecycle

All images share project-owned startup, clock, mailbox, CRC, watchdog, status, descriptor-string, and USB lifecycle code. Startup:

1. sets flash latency before increasing the clock;
2. bounds every oscillator/PLL/clock-switch wait;
3. requires an 8 MHz HSE, PLL×9, 72 MHz SYSCLK, and 48 MHz USB clock;
4. leaves USB disabled and reports `CLOCK_HSE_FAILED` through mailbox status when that contract cannot be reached;
5. excludes the first 4096 SRAM bytes at `0x20000000` from `.data`/`.bss` initialization.

The pinned TinyUSB source remains an immutable submodule. A source-hash-gated patch is applied only to a generated build copy to omit unsupported F1 suspend/wakeup interrupt behavior; the submodule itself remains pristine.

A profile boot or switch disables USB, makes PA11 high impedance, drives PA12 low for 20 ms to sink only D+ pull-up current, then restores USB mode before TinyUSB initialization. Boards on which the fixed pull-up prevents reliable re-enumeration require a controlled pull-up/transistor or manual unplug. Software does not equate the disconnect pulse with host re-enumeration.

Profile selection has one post-flash deadline and succeeds only after it observes:

- a different boot counter from the pre-flash snapshot;
- the requested numeric profile;
- a post-flash unmounted observation followed by mounted state;
- a stable boot counter across two reads;
- monotonically advancing firmware uptime.

## Fixed mailbox and failure model

The linker reserves a 4096-byte NOLOAD mailbox at `0x20000000`. The ABI contains:

- a header/liveness region;
- one host-to-firmware control command/response;
- one firmware-to-host 512-byte block request/response;
- one firmware-to-host token request and one host-to-firmware token response, each bounded to 512 bytes;
- zero-reserved gaps and tail.

[The protocol document](protocol.md) is the byte-exact contract.

Triggers and acknowledgements are written last. Every operation is CRC-32C protected, sequence checked, length bounded, and profile checked. On a retained valid mailbox, boot increments `boot_counter`; a command left with `host_seq != host_ack` becomes `RESET_DURING_COMMAND`, is acknowledged without execution, and is never automatically retried. Invalid retained layout causes all 4096 bytes to be zeroed before the header is initialized.

The daemon writes `host_heartbeat` every 250 ms. After one second without advancement, firmware schedules HID release while mounted, fails pending block/token work, increments `media_epoch`, and makes media absent. Retained magic is not liveness: uptime must advance between daemon reads.

## OpenOCD ownership

Normal access uses one persistent OpenOCD 0.12 subprocess:

```text
openocd -l LOG -f interface/stlink.cfg -f target/stm32f1x.cfg \
  -c 'gdb_port disabled' -c 'telnet_port disabled' \
  -c 'tcl_port pipe' -c init
```

Commands and responses use OpenOCD's Tcl machine interface with byte `0x1a` terminators. Normal mailbox traffic does not halt the core. Each RPC has a deadline. EOF, timeout, malformed response, or wrong word count terminates and reaps that exact child, fails the in-flight operation without retrying side effects, and allows a fresh session only for a later command.

Flashing stops/reaps the persistent child, runs a separate `program ABSOLUTE_ELF verify reset` process to completion, and only then restarts persistent access.

## Private controller surface

Runtime socket:

```text
$XDG_RUNTIME_DIR/universal-usb/control.sock
```

The containing directory is mode `0700`; the socket is mode `0600`. Requests are one-line UTF-8 JSON objects with exactly `{id, method, params}`. Responses contain the same `id` and exactly one of `result` or `error`. There is no network listener.

Credential/state directories are mode `0700`; files are mode `0600`. Sensitive state writes use `fsync` plus atomic replacement. OTP secrets are never logged and no default secret ships. HOTP counters advance only after the target keyboard transfer is accepted.

Stable CLI exits are:

| Exit | Meaning |
|---:|---|
| 0 | success |
| 2 | syntax, schema, or preflight error |
| 3 | daemon or ST-Link unavailable |
| 4 | active profile mismatch |
| 5 | mailbox timeout, CRC, or liveness failure |
| 6 | flash or verification failure |
| 7 | firmware or media rejection |
| 8 | post-action verification failure |

## Closed scenarios

Scenario version 1 is a closed JSON format with `version`, one required `profile`, and an ordered `steps` array. The controller rejects unknown keys/actions and preflights the complete file before step one. Supported actions are waits/marks; all documented HID/consumer operations; media attach/detach; microphone tone/silence; camera pattern; token touch; and OTP selection.

Scenarios deliberately have no shell execution, interpolation, loops, conditionals, or parallel branches. Token provisioning, PIN entry, credential deletion, and reset are excluded. Runtime failure stops the scenario and releases all held HID state. A profile mismatch fails unless `--switch-profile` was explicit.

## Verification boundary

Software builds and tests can prove fixed layouts, parser behavior, bounds, deterministic state transitions, generated data, descriptor bytes, and PMA accounting. They cannot prove wiring, HSE quality, ST-Link transport integrity, USB host timing, target-driver behavior, safe physical disconnect, or two-PC interoperability. Those remain separate HIL gates in [the showcase ledger](showcase.md).
