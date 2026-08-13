#!/usr/bin/env python3
"""Prepare an immutable, patched TinyUSB build copy.

The checked-in submodule is never modified.  Tracked sources are exported from
its pinned commit, the project patch is applied to the export, and TinyUSB's
own pinned dependency helper populates the generated copy.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

EXPECTED_COMMIT = "dae3f9a366bfcddbf9dcf1b48d7500286a849539"
EXPECTED_TREE = "a2dfc80a1f63f2a4ae3bb3942809bca58b35ae7d"
STAMP_NAME = ".uusb-tinyusb-prepared.json"
PATCHED_DCD = Path("src/portable/st/stm32_fsdev/dcd_stm32_fsdev.c")


def run_git(source: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def validate_source(source: Path) -> None:
    if run_git(source, "rev-parse", "HEAD") != EXPECTED_COMMIT:
        raise RuntimeError(f"TinyUSB must be checked out at {EXPECTED_COMMIT}")
    if run_git(source, "rev-parse", "HEAD^{tree}") != EXPECTED_TREE:
        raise RuntimeError(f"TinyUSB tree must be {EXPECTED_TREE}")
    status = run_git(source, "status", "--porcelain", "--untracked-files=all")
    if status:
        raise RuntimeError("TinyUSB submodule is not pristine:\n" + status)


def patch_digest(patch: Path) -> str:
    return hashlib.sha256(patch.read_bytes()).hexdigest()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_source(source: Path, destination: Path) -> None:
    archive = subprocess.run(
        ["git", "-C", str(source), "archive", "--format=tar", EXPECTED_COMMIT],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        for member in tar.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RuntimeError(f"unsafe path in TinyUSB archive: {member.name}")
        tar.extractall(destination, filter="data")


def apply_patch(destination: Path, patch: Path, *, check_only: bool) -> None:
    command = ["git", "apply", "--whitespace=error-all"]
    if check_only:
        command.append("--check")
    command.append(str(patch))
    subprocess.run(command, cwd=destination, check=True)


def expected_stamp(patch: Path) -> dict[str, str]:
    return {
        "commit": EXPECTED_COMMIT,
        "tree": EXPECTED_TREE,
        "patch_sha256": patch_digest(patch),
    }


def already_prepared(destination: Path, patch: Path) -> bool:
    stamp_path = destination / STAMP_NAME
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    expected = expected_stamp(patch)
    if any(stamp.get(key) != value for key, value in expected.items()):
        return False
    dcd = destination / PATCHED_DCD
    return dcd.is_file() and stamp.get("patched_dcd_sha256") == file_digest(dcd)


def prepare(source: Path, destination: Path, patch: Path) -> None:
    validate_source(source)
    if already_prepared(destination, patch):
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=destination.name + ".", dir=destination.parent)
    )
    try:
        export_source(source, staging)
        apply_patch(staging, patch, check_only=True)
        apply_patch(staging, patch, check_only=False)
        subprocess.run(
            [sys.executable, "tools/get_deps.py", "stm32f1"],
            cwd=staging,
            check=True,
        )
        stamp = expected_stamp(patch)
        stamp["patched_dcd_sha256"] = file_digest(staging / PATCHED_DCD)
        (staging / STAMP_NAME).write_text(
            json.dumps(stamp, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        if destination.exists():
            shutil.rmtree(destination)
        staging.replace(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def check_patch(source: Path, patch: Path) -> None:
    validate_source(source)
    with tempfile.TemporaryDirectory(prefix="uusb-tinyusb-check-") as temporary:
        exported = Path(temporary) / "tinyusb"
        export_source(source, exported)
        apply_patch(exported, patch, check_only=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.check_only == (args.destination is not None):
        parser.error("use exactly one of --check-only or --destination")
    return args


def main() -> int:
    args = parse_args()
    source = args.source.resolve(strict=True)
    patch = args.patch.resolve(strict=True)
    if args.check_only:
        check_patch(source, patch)
    else:
        destination = args.destination.resolve()
        if destination == source or source in destination.parents:
            raise RuntimeError("generated TinyUSB copy must not be inside the submodule")
        prepare(source, destination, patch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
