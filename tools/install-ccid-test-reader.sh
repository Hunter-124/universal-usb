#!/usr/bin/env bash
set -euo pipefail
umask 022

if [[ ${EUID} -ne 0 ]]; then
    printf 'run as root: sudo %q\n' "$0" >&2
    exit 1
fi

system_bundle=
for candidate in /usr/lib64/pcsc/drivers/ifd-ccid.bundle /usr/lib/pcsc/drivers/ifd-ccid.bundle; do
    if [[ -f "$candidate/Contents/Linux/libccid.so" ]]; then
        system_bundle=$candidate
        break
    fi
done
if [[ -z $system_bundle ]]; then
    echo 'libccid driver bundle not found; install pcsc-lite-ccid first' >&2
    exit 1
fi


python3 - "$system_bundle/Contents/Info.plist" <<'PY'
import os
import plistlib
import stat
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
mode = stat.S_IMODE(path.stat().st_mode)
document = plistlib.loads(path.read_bytes())
vendors = document["ifdVendorID"]
products = document["ifdProductID"]
names = document["ifdFriendlyName"]
if not (len(vendors) == len(products) == len(names)):
    raise SystemExit("libccid Info.plist arrays are inconsistent")
matches = [
    index for index, pair in enumerate(zip(vendors, products))
    if pair == ("0x1209", "0x0010")
]
if not matches:
    vendors.append("0x1209")
    products.append("0x0010")
    names.append("Universal USB Synthetic Security Token")
payload = plistlib.dumps(document, fmt=plistlib.FMT_XML, sort_keys=False)
descriptor, temporary = tempfile.mkstemp(prefix=".uusb-ccid-", dir=path.parent)
try:
    with os.fdopen(descriptor, "wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY
printf 'registered 1209:0010 in %s\n' "$system_bundle/Contents/Info.plist"
printf 'restart pcscd or replug the security-token profile before scanning\n'
