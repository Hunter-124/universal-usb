#!/usr/bin/env bash
set -euo pipefail

readonly ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly BUILD_DIR="${BUILD_DIR:-${ROOT}/build/firmware}"
readonly BUILD_TYPE="${BUILD_TYPE:-MinSizeRel}"

cd "${ROOT}"
git submodule update --init --recursive
"${ROOT}/tools/verify-tinyusb.py"

cmake -S "${ROOT}/firmware" -B "${BUILD_DIR}" \
  -DBOARD=stm32f103_bluepill \
  -DCMAKE_BUILD_TYPE="${BUILD_TYPE}"
cmake --build "${BUILD_DIR}" --target \
  uusb-hid-msc uusb-microphone uusb-webcam uusb-security-token

for profile in uusb-hid-msc uusb-microphone uusb-webcam uusb-security-token; do
  for extension in elf bin hex map; do
    artifact="${BUILD_DIR}/${profile}.${extension}"
    if [[ ! -s "${artifact}" ]]; then
      printf 'missing firmware artifact: %s\n' "${artifact}" >&2
      exit 1
    fi
  done
done

"${ROOT}/tools/verify-tinyusb.py"
