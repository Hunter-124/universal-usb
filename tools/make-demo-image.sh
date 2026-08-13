#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'
umask 077

usage() {
  printf 'usage: %s OUTPUT.img\n' "${0##*/}" >&2
  exit 2
}

[[ $# -eq 1 ]] || usage
output=$1
output_dir=$(dirname -- "$output")

[[ -d "$output_dir" ]] || {
  printf 'make-demo-image: output directory does not exist: %s\n' "$output_dir" >&2
  exit 2
}
[[ ! -e "$output" && ! -L "$output" ]] || {
  printf 'make-demo-image: refusing to replace existing path: %s\n' "$output" >&2
  exit 2
}

for program in mkfs.fat mcopy python3 truncate mktemp touch ln sha256sum; do
  command -v "$program" >/dev/null 2>&1 || {
    printf 'make-demo-image: required local program not found: %s\n' "$program" >&2
    exit 3
  }
done

temporary=$(mktemp -d "$output_dir/.uusb-fat12.XXXXXX")
cleanup() {
  rm -rf -- "$temporary"
}
trap cleanup EXIT HUP INT TERM

image=$temporary/uusb-demo.img
marker=$temporary/UUSB.TXT

printf '%s\r\n%s\r\n' \
  'Universal USB deterministic FAT12 demo image.' \
  'This file is test data; the target-facing image is read-only unless explicitly attached read-write.' \
  >"$marker"
touch -t 198001010000.00 "$marker"
truncate -s 1474560 "$image"

export LC_ALL=C
export TZ=UTC
export SOURCE_DATE_EPOCH=315532800
mkfs.fat -F 12 --invariant -n UUSBDEMO "$image" >/dev/null
mcopy -m -i "$image" "$marker" ::UUSB.TXT

# mcopy preserves the fixed modification time, but some versions still stamp
# creation/access fields. Normalize every live root entry to the earliest FAT
# timestamp so two runs produce identical bytes.
python3 - "$image" <<'PY'
from pathlib import Path
import struct
import sys

path = Path(sys.argv[1])
data = bytearray(path.read_bytes())
if len(data) != 1_474_560:
    raise SystemExit("make-demo-image: image size changed")
if data[510:512] != b"\x55\xaa":
    raise SystemExit("make-demo-image: missing FAT boot signature")

bytes_per_sector = struct.unpack_from("<H", data, 11)[0]
reserved_sectors = struct.unpack_from("<H", data, 14)[0]
fat_count = data[16]
root_entries = struct.unpack_from("<H", data, 17)[0]
sectors_per_fat = struct.unpack_from("<H", data, 22)[0]
if (bytes_per_sector, fat_count, root_entries) != (512, 2, 224):
    raise SystemExit("make-demo-image: unexpected 1.44 MiB FAT12 geometry")

root = (reserved_sectors + fat_count * sectors_per_fat) * bytes_per_sector
fixed_date = 0x0021  # 1980-01-01 in FAT date encoding
found_marker = False
for index in range(root_entries):
    offset = root + index * 32
    first = data[offset]
    if first == 0x00:
        break
    if first == 0xE5:
        continue
    entry = memoryview(data)[offset : offset + 32]
    if bytes(entry[:11]) == b"UUSB    TXT":
        found_marker = True
    entry[12] = 0
    entry[13] = 0
    struct.pack_into("<H", entry, 14, 0)
    struct.pack_into("<H", entry, 16, fixed_date)
    struct.pack_into("<H", entry, 18, fixed_date)
    struct.pack_into("<H", entry, 22, 0)
    struct.pack_into("<H", entry, 24, fixed_date)

if not found_marker:
    raise SystemExit("make-demo-image: mcopy did not create UUSB.TXT")
path.write_bytes(data)
PY

chmod 0600 "$image"
# A hard link publishes with create-if-absent semantics and cannot follow or
# overwrite a competing path. The temporary directory is on the output FS.
ln -- "$image" "$output"
printf 'created %s (1474560-byte FAT12, label UUSBDEMO)\n' "$output"
sha256sum -- "$output"
