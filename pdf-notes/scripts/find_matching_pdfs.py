#!/usr/bin/env python3
"""
Locate candidate customer PDFs in Downloads.

Usage:
    python find_matching_pdfs.py "Laura Follo"
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path


DOWNLOADS = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads"
SUFFIXES = ("auto", "home", "condo", "renters")


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def split_candidate(stem: str) -> tuple[str, str, str] | None:
    normalized = normalize(stem)
    match = re.fullmatch(r"(.+?)(auto|home|condo|renters)(\d*)", normalized)
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


def policy_type_for_path(path: Path) -> str | None:
    parts = split_candidate(path.stem)
    if not parts:
        return None
    return parts[1]


def score_candidate(stem: str, target: str) -> int:
    parts = split_candidate(stem)
    if not parts:
        return 99

    name_part, _, ordinal = parts
    if name_part == target and not ordinal:
        return 0
    if name_part == target:
        return 1
    if name_part.startswith(target):
        return 2
    return 99


def find_candidates(customer: str) -> list[Path]:
    target = normalize(customer)
    results: list[tuple[int, str, Path]] = []
    for path in DOWNLOADS.glob("*.pdf"):
        rank = score_candidate(path.stem, target)
        if rank < 99:
            results.append((rank, path.name.lower(), path))
    results.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in results]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("customer", help="Customer first and last name")
    args = parser.parse_args()

    matches = find_candidates(args.customer)
    if not matches:
        print("No matching PDFs found.")
        return 1

    for match in matches:
        print(match)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
