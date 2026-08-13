# Mailbox, USB profile, and controller protocol

This document is the byte-exact ABI shared by firmware and the Linux controller. The authoritative declarations are `firmware/include/uusb_mailbox.h` and `controller/uusb/protocol.py`; both are protected by size/offset assertions and golden vectors.

## Encoding and publication rules

- SRAM base: `0x20000000`
- total layout: exactly `0x1000` (4096) bytes
- ABI version: `1`
- magic: `0x31425355` (the bytes `USB1` in little-endian memory)
- all multi-byte integers: unsigned little-endian unless a payload field explicitly says signed
- sequence arithmetic: unsigned 32-bit
- CRC: CRC-32C/Castagnoli, polynomial `0x1EDC6F41`, reflected input/output, initial value `0xffffffff`, final XOR `0xffffffff`
- mandatory CRC check vector: ASCII `123456789` → `0xe3069283`

A producer fills metadata, payload, and CRC before publishing a trigger sequence. The trigger is written last. A consumer validates the complete slot before changing state, then writes the acknowledgement last. Invalid length, reserved field, CRC, sequence, opcode, transport, profile, or state never produces a successful side effect.

Zero-reserved ranges are cleared when an invalid retained mailbox is initialized and otherwise left untouched. Reserved fields inside active structures must be zero.

## Complete memory map

| Absolute offset | Size | Owner / meaning |
|---:|---:|---|
| `0x000` | `0x040` | shared header and liveness |
| `0x040` | `0x100` | host control request and firmware response |
| `0x140` | `0x224` | firmware block request and host response |
| `0x364` | `0x09c` | zero-reserved |
| `0x400` | `0x214` | firmware token request and host copy acknowledgement |
| `0x614` | `0x02c` | zero-reserved |
| `0x640` | `0x214` | host token response and firmware consumption acknowledgement |
| `0x854` | `0x7ac` | zero-reserved through `0xfff` |

## Header: `0x000..0x03f`

| Offset | Field | Meaning |
|---:|---|---|
| `0x000` | `magic:u32` | `0x31425355` |
| `0x004` | `abi_version:u16` | `1` |
| `0x006` | `layout_size:u16` | `4096` |
| `0x008` | `fw_version:u32` | `major<<16 | minor<<8 | patch` |
| `0x00c` | `profile:u32` | `1=hid-msc`, `2=microphone`, `3=webcam`, `4=security-token` |
| `0x010` | `boot_counter:u32` | increments on a valid retained boot |
| `0x014` | `uptime_ms:u32` | firmware monotonic millisecond counter, modulo 2³² |
| `0x018` | `usb_flags:u32` | bit field below |
| `0x01c` | `host_heartbeat:u32` | host-owned; daemon advances every 250 ms |
| `0x020` | `last_error:u32` | most recent control status/error |
| `0x024` | `control_completed:u32` | completed control-command counter |
| `0x028` | `media_epoch:u32` | generation for media requests |
| `0x02c` | `block_completed:u32` | completed block-operation counter |
| `0x030` | `reserved[4]:u32` | zero through `0x03f` |

USB flag bits:

| Bit | Name | Meaning |
|---:|---|---|
| 0 | `CLOCK_VALID` | required 72 MHz system / 48 MHz USB clock is valid |
| 1 | `MOUNTED` | TinyUSB reports configured/mounted |
| 2 | `SUSPENDED` | target suspended USB |
| 3 | `MEDIA_PRESENT` | one valid MSC image is attached |
| 4 | `MEDIA_WRITABLE` | attach was explicitly read-write |
| 5 | `HID_RELEASE_PENDING` | zero reports still need accepted IN transfers |
| 6 | `BLOCK_PENDING` | one block request is outstanding |

A valid retained header is not liveness. The daemon requires advancing uptime across two reads. On invalid magic/version/layout, firmware clears all 4096 bytes before initializing the header. On a valid retained boot, it clears local HID/media/token state and increments `boot_counter`. If `host_seq != host_ack`, it records `RESET_DURING_COMMAND`, sets `host_ack=host_seq` last, and never executes or automatically retries the retained command.

## Host control slot: `0x040..0x13f`

### Host request: `0x040..0x0bf`

| Offset | Field | Rule |
|---:|---|---|
| `0x040` | `host_seq:u32` | host trigger; written last |
| `0x044` | `opcode:u16` | one opcode below |
| `0x046` | `request_length:u16` | `0..112` |
| `0x048` | `request_flags:u32` | must be zero in ABI v1 |
| `0x04c` | `request_crc:u32` | CRC rule below |
| `0x050` | `request_payload[112]` | first `request_length` bytes are meaningful |

Request CRC covers bytes `0x044..0x04b` (`opcode`, `request_length`, `request_flags`) followed by exactly `request_length` bytes beginning at `0x050`. It excludes `host_seq` and `request_crc`.

### Firmware response: `0x0c0..0x13f`

| Offset | Field | Rule |
|---:|---|---|
| `0x0c0` | `host_ack:u32` | completion/acknowledgement; written last |
| `0x0c4` | `response_status:u32` | one status below |
| `0x0c8` | `response_length:u16` | `0..112` |
| `0x0ca` | `reserved:u16` | zero |
| `0x0cc` | `response_crc:u32` | CRC rule below |
| `0x0d0` | `response_payload[112]` | first `response_length` bytes are meaningful |

Response CRC covers bytes `0x0c4..0x0cb` (`response_status`, `response_length`, reserved zero) followed by exactly `response_length` bytes beginning at `0x0d0`. The host accepts the response only when `host_ack` equals its submitted `host_seq` and the boot counter has not changed. A reboot before acknowledgement makes the command indeterminate and failed; side-effecting commands are not retried automatically.

### Control opcodes and payloads

| Opcode | Name | Request payload | Allowed profile |
|---:|---|---|---|
| `0x0001` | `GET_INFO` | empty | all |
| `0x0002` | `RELEASE_ALL` | empty | all; meaningful for HID/token release |
| `0x0100` | `HID_KEYBOARD` | standard boot report `{modifiers:u8,reserved:u8=0,keys[6]}` | `hid-msc`; also OTP report transfer in `security-token` |
| `0x0101` | `HID_MOUSE` | `{buttons:u8,dx:i8,dy:i8,wheel:i8}` | `hid-msc` |
| `0x0102` | `HID_CONSUMER` | `{usage:u16}` | `hid-msc` |
| `0x0200` | `MEDIA_ATTACH` | `{block_count:u32,writable:u8,reserved[3]=0}` | `hid-msc` |
| `0x0201` | `MEDIA_DETACH` | empty | `hid-msc` |
| `0x0300` | `MIC_CONFIG` | `{mode:u8,reserved:u8=0,frequency_hz:u16,amplitude_q15:u16}` | `microphone` |
| `0x0400` | `UVC_PATTERN` | `{pattern:u8}` | `webcam` |
| `0x7f00` | `RESET` | empty | all |

Fixed values:

- microphone mode: `0=silence`, `1=tone`; tone frequency is 20–20,000 Hz and amplitude is `0..32767`;
- camera pattern: `0=bars`, `1=checker`, `2=gradient`;
- consumer usages: play/pause `0x00cd`, stop `0x00b7`, next `0x00b5`, previous `0x00b6`, mute `0x00e2`, volume up `0x00e9`, volume down `0x00ea`.

`GET_INFO` responses are profile-specific:

- `hid-msc`: empty response; common state is read from the header;
- `microphone`: 16 bytes `{mode:u8,mounted:u8,streaming:u8,suspended:u8,frequency_hz:u16,amplitude_q15:u16,packet_count:u32,underrun_count:u32}`;
- `webcam`: 16 bytes `{pattern:u8,mounted:u8,streaming:u8,suspended:u8,width:u16,height:u16,frames_completed:u32,payloads_generated:u32}`;
- `security-token`: 4 bytes `{mounted:u8,suspended:u8,bridge_pending:u8,ccid_powered:u8}`.

### Status values

| Value | Name | Meaning |
|---:|---|---|
| 0 | `OK` | operation accepted/completed |
| 1 | `BAD_ABI` | incompatible ABI/header |
| 2 | `BAD_LENGTH` | fixed or bounded length failed |
| 3 | `BAD_CRC` | CRC mismatch |
| 4 | `BAD_OPCODE` | unknown/unsupported opcode |
| 5 | `WRONG_PROFILE` | opcode is not available in the active profile |
| 6 | `BAD_STATE` | lifecycle/state precondition failed |
| 7 | `OUT_OF_RANGE` | numeric or address range failed |
| 8 | `BUSY` | sole slot/resource is occupied |
| 9 | `TIMEOUT` | bounded operation expired |
| 10 | `IO_ERROR` | backing I/O failed |
| 11 | `RESET_DURING_COMMAND` | retained in-flight command was discarded at boot |

## Block slot: `0x140..0x363`

| Offset | Field | Rule |
|---:|---|---|
| `0x140` | `request_seq:u32` | firmware trigger; written last |
| `0x144` | `epoch:u32` | current `media_epoch` |
| `0x148` | `op:u8` | `1=READ`, `2=WRITE`, `3=FLUSH` |
| `0x149` | `reserved[3]` | zero |
| `0x14c` | `lba:u32` | zero-based 512-byte block |
| `0x150` | `data_length:u32` | 512 for READ/WRITE; 0 for FLUSH |
| `0x154` | `request_crc:u32` | request CRC |
| `0x158` | `data[512]` | WRITE input or successful READ output |
| `0x358` | `response_seq:u32` | host completion; written last and equal to request sequence |
| `0x35c` | `response_status:u32` | control status value |
| `0x360` | `response_crc:u32` | response CRC |

Request CRC covers bytes `0x144..0x153` (`epoch`, operation plus zero reserve, `lba`, `data_length`). A WRITE appends all 512 data bytes to the CRC. READ and FLUSH append no data.

Response CRC always begins with `response_status:u32`. For a successful READ only, it additionally covers the request's `data_length:u32` and exactly 512 data bytes. A successful WRITE/FLUSH and every error response cover status only.

Exactly one block request may be pending. `(epoch,request_seq,op,lba)` identifies it. Firmware accepts a response only when this tuple still describes the active request and the response CRC/status is valid. BOT reset, unmount, detach, heartbeat loss, timeout, or I/O failure abandons the request, advances media generation where required, completes USB exactly once with an error when applicable, and ignores a late response without a second completion.

## Token request slot: `0x400..0x613`

| Offset | Field | Rule |
|---:|---|---|
| `0x400` | `request_seq:u32` | firmware trigger; written last |
| `0x404` | `transport:u16` | `1=FIDO_HID`, `2=CCID` |
| `0x406` | `length:u16` | `0..512` |
| `0x408` | `flags:u32` | zero in ABI v1 |
| `0x40c` | `request_crc:u32` | request CRC |
| `0x410` | `data[512]` | exactly `length` meaningful bytes through at most `0x60f` |
| `0x610` | `request_ack:u32` | daemon copied/validated request; written last |

Request CRC covers `transport`, `length`, and `flags` at `0x404..0x40b`, followed by exactly `length` bytes at `0x410`. It excludes both sequence and CRC fields.

Firmware publishes one complete logical FIDO-HID or CCID bridge message after bounded USB framing. `request_ack` means the daemon copied the request; it is not a successful protocol response.

## Token response slot: `0x640..0x853`

| Offset | Field | Rule |
|---:|---|---|
| `0x640` | `response_seq:u32` | daemon trigger; written last |
| `0x644` | `request_seq:u32` | token request being answered |
| `0x648` | `length:u16` | `0..512` |
| `0x64a` | `reserved:u16` | zero |
| `0x64c` | `response_crc:u32` | response CRC |
| `0x650` | `data[512]` | exactly `length` meaningful bytes through at most `0x84f` |
| `0x850` | `response_ack:u32` | firmware consumed response; written last |

Response CRC covers `request_seq`, `length`, and reserved zero at `0x644..0x64b`, followed by exactly `length` bytes at `0x650`. `response_seq` advances independently when the preceding response has been acknowledged; `request_seq` ties the payload to the pending request.

Only one token exchange may be pending. Heartbeat loss, reset, USB unmount, stale sequence, bad CRC, invalid length/transport, or timeout fails it and never fabricates success bytes. Firmware acknowledges consumption only after the response belongs to the active request and has been accepted for the relevant USB transport.

### FIDO bridge boundary

USB uses 64-byte CTAPHID reports: 57 payload bytes in an initial report and 59 in each continuation report, with a 512-byte logical-message ceiling. The controller handles `INIT`, `PING`, `WINK`, `CBOR`, `CANCEL`, and the protocol's `KEEPALIVE`/`ERROR` behavior. The CTAP2 command surface is GetInfo, MakeCredential, GetAssertion, GetNextAssertion, and Reset; unsupported commands/options return CTAP-defined errors.

User presence is an explicit synthetic controller event. ES256/P-256 private keys and credential state live on the controller; OpenSSL performs key generation/signatures; attestation is `none`. This is an interoperability surface, not a secure element or certified authenticator.

### CCID bridge boundary

The USB function describes CCID 1.10, one slot, protocol T=1, and short-APDU exchange with a 512-byte CCID message ceiling. The state machine covers power on/off, slot status, parameter negotiation, transfer block, abort, and time extension. The bounded PIV-like application supports SELECT of the PIV AID, GET DATA for project-generated CHUID/certificate objects, VERIFY of a test PIN, and GENERAL AUTHENTICATE with a controller-backed P-256 key. Other commands/APDUs return protocol-correct unsupported status. No PIV/OpenPGP completeness or certification is claimed.

## USB profile identities

| Profile | Product string | Interfaces/endpoints | PMA |
|---|---|---|---:|
| `hid-msc` | `Universal USB HID + Disk` | HID IN `81/82/83`; MSC OUT `04`, IN `84` | 338 B |
| `microphone` | `Universal USB Test Microphone` | UAC1 isochronous IN `81`, 96 B/1 ms | 288 B |
| `webcam` | `Universal USB Test Camera` | UVC isochronous IN `81`, 256 B/1 ms | 448 B |
| `security-token` | `Universal USB Synthetic Security Token` | FIDO OUT `01`/IN `81`; CCID OUT `02`/IN `82`, interrupt IN `83`; OTP IN `84` | 464 B |

Manufacturer is `Universal USB`; serial strings derive from the STM32 96-bit UID. Test PIDs are respectively `000d`, `000e`, `000f`, and `0010` under private-test VID `1209`. They are not distributable product allocations.

## Daemon RPC

Socket path: `$XDG_RUNTIME_DIR/universal-usb/control.sock`, mode `0600` inside a mode-`0700` directory. The transport is private newline-delimited JSON over Unix stream sockets.

Request, with no additional keys:

```json
{"id":1,"method":"status","params":{}}
```

Success:

```json
{"id":1,"result":{"profile":"hid-msc"}}
```

Failure:

```json
{"id":1,"error":{"code":5,"message":"mailbox liveness check failed"}}
```

`id` is a non-null scalar other than boolean; the response echoes it. `method` is a string, `params` is an object, and the encoded request/response is exactly one line. The bounded server rejects unknown top-level fields and malformed/oversized lines.

Public methods map one-to-one from the CLI:

- `doctor`, `status`, `events`
- `profile.list`, `profile.show`, `profile.set`
- `hid.key`, `hid.text`, `hid.mouse`, `hid.consumer`
- `msc.attach`, `msc.detach`, `msc.status`
- `mic.tone`, `mic.silence`, `mic.status`
- `cam.pattern`, `cam.status`
- `scenario.validate`, `scenario.run`
- `token.status`, `token.touch`, `token.reset`, `token.pin.set`
- `token.credential.list`, `token.credential.delete`
- `token.otp.provision`, `token.otp.remove`, `token.otp.select`

RPC error codes are the CLI's stable exits: `2` preflight, `3` daemon/ST-Link unavailable, `4` profile mismatch, `5` mailbox/CRC/liveness, `6` flash, `7` firmware/media rejection, and `8` post-action verification.

## Scenario v1

A scenario object has exactly:

- required `version: 1`;
- required `profile` in `hid-msc`, `microphone`, `webcam`, `security-token`;
- optional nonempty `name` and `description`;
- required ordered `steps` of 1–1000 closed action objects.

Actions are `wait`, `mark`, `hid.key.tap/down/up`, `hid.text`, `hid.mouse.move`, `hid.mouse.button.down/up/click`, `hid.mouse.scroll`, `hid.consumer.tap/down/up`, `msc.attach`, `msc.detach`, `mic.tone`, `mic.silence`, `cam.pattern`, `token.touch`, and `token.otp.select`.

Unknown properties/actions, shell commands, interpolation, loops, and parallel branches are rejected. The daemon validates every step, key name, bound, active profile, and media path/size before executing step one. Runtime failure stops immediately and releases held HID state. Token provisioning, PIN entry, credential deletion, and reset are intentionally not scenario actions.
