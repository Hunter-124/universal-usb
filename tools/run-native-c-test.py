#!/usr/bin/env python3
"""Compile and execute one allocation-free firmware helper test on the host."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--include", action="append", default=[])
    parser.add_argument("source", nargs="+")
    args = parser.parse_args()

    sources = [str(Path(source).resolve(strict=True)) for source in args.source]
    with tempfile.TemporaryDirectory(prefix="uusb-native-test-") as directory:
        executable = Path(directory) / "test"
        command = [
            args.compiler,
            "-std=c17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-O2",
            *(flag for include in args.include for flag in ("-I", str(Path(include).resolve(strict=True)))),
            *sources,
            "-o",
            str(executable),
        ]
        subprocess.run(command, check=True)
        subprocess.run((str(executable),), check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
