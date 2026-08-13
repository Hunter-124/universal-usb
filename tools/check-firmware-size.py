#!/usr/bin/env python3
"""Reject firmware that escapes the guaranteed STM32F103C8 memory map."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

FLASH_START = 0x08000000
FLASH_SIZE = 64 * 1024
RAM_START = 0x20000000
RAM_SIZE = 20 * 1024
MAILBOX_SIZE = 4096

SECTION = re.compile(
    r"^\s*\d+\s+(\S+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+"
    r"([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+\S+\s*$"
)


@dataclass(frozen=True)
class Section:
    name: str
    size: int
    vma: int
    lma: int
    alloc: bool


def contains(start: int, length: int, address: int, size: int) -> bool:
    return start <= address and address + size <= start + length


def sections_from_objdump(objdump: str, elf: Path) -> list[Section]:
    result = subprocess.run(
        [objdump, "-h", str(elf)],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    lines = result.stdout.splitlines()
    sections: list[Section] = []
    for index, line in enumerate(lines):
        match = SECTION.match(line)
        if match is None:
            continue
        flags = lines[index + 1] if index + 1 < len(lines) else ""
        sections.append(
            Section(
                name=match.group(1),
                size=int(match.group(2), 16),
                vma=int(match.group(3), 16),
                lma=int(match.group(4), 16),
                alloc="ALLOC" in {flag.strip() for flag in flags.split(",")},
            )
        )
    if not sections:
        raise RuntimeError("objdump did not report any ELF sections")
    return sections


def check(elf: Path, objdump: str) -> None:
    sections = sections_from_objdump(objdump, elf)
    allocated = [section for section in sections if section.alloc and section.size]
    for section in allocated:
        vma_valid = contains(FLASH_START, FLASH_SIZE, section.vma, section.size) or contains(
            RAM_START, RAM_SIZE, section.vma, section.size
        )
        if not vma_valid:
            raise RuntimeError(
                f"allocated section {section.name} has out-of-range VMA 0x{section.vma:08x}"
            )
        if section.lma != section.vma and not contains(
            FLASH_START, FLASH_SIZE, section.lma, section.size
        ):
            raise RuntimeError(
                f"allocated section {section.name} has out-of-range LMA 0x{section.lma:08x}"
            )

    mailbox = next((section for section in sections if section.name == ".mailbox"), None)
    if mailbox is None or mailbox.vma != RAM_START or mailbox.size != MAILBOX_SIZE:
        raise RuntimeError(".mailbox must occupy 4096 bytes at 0x20000000")

    flash_sections = [
        section
        for section in allocated
        if contains(FLASH_START, FLASH_SIZE, section.lma, section.size)
    ]
    ram_sections = [
        section
        for section in allocated
        if contains(RAM_START, RAM_SIZE, section.vma, section.size)
    ]
    flash_end = max((section.lma + section.size for section in flash_sections), default=FLASH_START)
    ram_end = max((section.vma + section.size for section in ram_sections), default=RAM_START)
    flash_used = flash_end - FLASH_START
    ram_used = ram_end - RAM_START
    if flash_used > FLASH_SIZE or ram_used > RAM_SIZE:
        raise RuntimeError("firmware exceeds guaranteed device memory")
    print(
        f"{elf.name}: flash extent {flash_used}/{FLASH_SIZE} bytes, "
        f"RAM extent {ram_used}/{RAM_SIZE} bytes"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--objdump", required=True)
    parser.add_argument("elf", type=Path)
    args = parser.parse_args()
    check(args.elf.resolve(strict=True), args.objdump)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
