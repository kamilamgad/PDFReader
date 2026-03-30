#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "pdf-notes" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import generate_fast_notes  # type: ignore  # noqa: E402
import generate_notes  # type: ignore  # noqa: E402


CUSTOMER = "Jordan Parker"
HOME_PATH = ROOT / "demo" / "inputs" / "JordanParkerHome.pdf"
AUTO_PATH = ROOT / "demo" / "inputs" / "JordanParkerAuto.pdf"
HOME_TEXT = (ROOT / "demo" / "inputs" / "JordanParkerHome.txt").read_text(encoding="utf-8")
AUTO_TEXT = (ROOT / "demo" / "inputs" / "JordanParkerAuto.txt").read_text(encoding="utf-8")


def fake_choose_policy_pdfs(customer: str):
    if customer != CUSTOMER:
        return [], [], []
    return [HOME_PATH], [AUTO_PATH], []


def fake_read_pdf_text(path: Path) -> str:
    name = path.name.lower()
    if "home" in name:
        return HOME_TEXT
    if "auto" in name:
        return AUTO_TEXT
    return ""


def main() -> int:
    output_path = ROOT / "demo" / "output" / "JordanParkerNotes.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    generate_notes.choose_policy_pdfs = fake_choose_policy_pdfs
    generate_notes.read_pdf_text = fake_read_pdf_text
    generate_fast_notes.choose_policy_pdfs = fake_choose_policy_pdfs
    generate_fast_notes.read_pdf_text = fake_read_pdf_text

    rendered = generate_fast_notes.render(CUSTOMER)
    output_path.write_text(rendered, encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
