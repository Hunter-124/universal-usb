#!/usr/bin/env python3
"""Verify the immutable TinyUSB gitlink and project patch without building."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from prepare_tinyusb import EXPECTED_COMMIT, check_patch


def output(*command: str, cwd: Path) -> str:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    source = root / "third_party" / "tinyusb"
    patch = root / "patches" / "tinyusb-stm32f1-no-usb-suspend.patch"

    stage = output(
        "git", "ls-files", "--stage", "third_party/tinyusb", cwd=root
    ).split()
    if len(stage) < 2 or stage[0] != "160000" or stage[1] != EXPECTED_COMMIT:
        raise RuntimeError(
            f"TinyUSB gitlink must record mode 160000 at {EXPECTED_COMMIT}"
        )

    check_patch(source, patch)
    print(f"TinyUSB {EXPECTED_COMMIT}: pristine gitlink and applicable patch")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"TinyUSB verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
