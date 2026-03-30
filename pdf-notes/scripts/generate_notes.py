#!/usr/bin/env python3
"""
Generate customer notes from matching Home and Auto PDFs plus NotesTemplate.txt.

Usage:
    python generate_notes.py "Jordan Parker"
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader

from find_matching_pdfs import find_candidates, normalize, policy_type_for_path


DOWNLOADS = Path(
    os.environ.get(
        "PDF_NOTES_INPUT_DIR",
        str(Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Downloads"),
    )
)
DEFAULT_TEMPLATE = Path(
    os.environ.get(
        "PDF_NOTES_TEMPLATE",
        str(DOWNLOADS / "NotesTemplate.txt"),
    )
)
DEFAULT_OUTPUT_DIR = Path(
    os.environ.get(
        "PDF_NOTES_OUTPUT_DIR",
        str(DOWNLOADS),
    )
)
DEFAULT_OUTPUT_SUFFIX = "Notes.txt"
OMIT_LINE = "__OMIT_LINE__"
PLAIN_LABELS = (
    "Date of birth",
    "Social security number",
    "Drivers license #",
    "Phone number",
    "Discounts",
)
SECTION_MARKERS = {
    "///////////Home/////////": "home",
    "///////////Auto/////////": "auto",
}
SECTION_FIELD_ALIASES = {
    "home": {
        "Policy Number:": ("Policy Number:", "Home Policy Number:"),
        "Effective:": ("Effective:",),
        "Policy Premium": ("Policy Premium",),
        "Discounts": ("Discounts",),
    },
    "auto": {
        "Policy Number:": ("Auto Policy Number", "Policy Number:"),
        "Effective:": ("Auto Effective", "Effective:"),
        "Policy Premium": ("Auto policy premium", "Policy Premium"),
        "Discounts": ("Auto Discounts", "Discounts"),
    },
}
SHARED_SINGLETON_LABELS = {
    "e-mail Address(es):",
}


def read_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def clean_value(value: str) -> str:
    value = value.replace("\r", " ")
    value = value.replace("\x96", "-")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+,", ",", value)
    value = re.sub(r",(?=\D)", ", ", value)
    value = re.sub(r"(?<=\d),\s+(?=\d)", ",", value)
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    return value.strip(" :;-")


def clean_label(value: str) -> str:
    value = value.replace("\r", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def search(text: str, pattern: str) -> str:
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    for group in match.groups():
        if group:
            return clean_value(group)
    return clean_value(match.group(0))


def search_first(text: str, *patterns: str) -> str:
    for pattern in patterns:
        value = search(text, pattern)
        if value:
            return value
    return ""


def split_name(full_name: str) -> tuple[str, str]:
    parts = [part for part in clean_value(full_name).split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def extract_person_names(raw: str) -> list[str]:
    names = re.findall(
        r"[A-Z][a-z]+(?: [A-Z])?(?: [A-Z][a-z]+){1,3}",
        clean_value(raw),
    )
    return unique_preserve(names)


def split_shared_last_name_names(raw: str) -> list[str]:
    cleaned = clean_value(raw)
    if " and " not in cleaned.lower():
        return []

    match = re.search(
        r"^([A-Z][a-z]+(?: [A-Z])?)\s+and\s+([A-Z][a-z]+(?: [A-Z])?)\s+([A-Z][a-z]+)$",
        cleaned,
        re.IGNORECASE,
    )
    if not match:
        return []

    first_one = format_name(f"{match.group(1)} {match.group(3)}")
    first_two = format_name(f"{match.group(2)} {match.group(3)}")
    return unique_preserve([first_one, first_two])


def split_two_full_names(raw: str) -> list[str]:
    cleaned = clean_value(raw)
    parts = [part for part in cleaned.split() if part]
    if len(parts) != 4:
        return []
    if not all(re.fullmatch(r"[A-Z][a-z]+(?:-[A-Z][a-z]+)?", part) for part in parts):
        return []
    return unique_preserve(
        [
            format_name(f"{parts[0]} {parts[1]}"),
            format_name(f"{parts[2]} {parts[3]}"),
        ]
    )


def split_dear_names(text: str) -> list[str]:
    raw = search(
        text,
        r"Dear\s+(.*?),",
    )
    if not raw:
        return []
    names = split_shared_last_name_names(raw)
    if not names:
        names = [
            format_name(part)
            for part in re.split(r"\band\b", raw, flags=re.IGNORECASE)
            if clean_value(part)
        ]
    if not names:
        names = split_two_full_names(raw)
    return unique_preserve(names)


def format_policy_number(value: str) -> str:
    digits = re.sub(r"[^0-9]", "", value)
    if len(digits) == 8:
        return f"{digits[:5]}-{digits[5:7]}-{digits[7:]}"
    return clean_value(value)


def format_name(full_name: str) -> str:
    return clean_value(full_name.title())


def unique_preserve(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = clean_value(value)
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def choose_primary_names(names: list[str], customer: str) -> tuple[tuple[str, str], tuple[str, str]]:
    ordered = unique_preserve(names)
    customer_norm = normalize(customer)
    ordered.sort(key=lambda name: (normalize(name) != customer_norm, normalize(name)))
    primary = split_name(ordered[0]) if ordered else ("", "")
    secondary = split_name(ordered[1]) if len(ordered) > 1 else ("", "")
    return primary, secondary


def extract_home_names(text: str, customer: str) -> list[str]:
    raw = search_first(
        text,
        r"Named Insured\(s\):\s*(.*?)e-mail\s*Address\(es\):",
        r"Named Insured\(s\):\s*(.*?)Property Insured:",
        r"Named Insured\(s\):\s*(.*?)Underwritten By:",
    )
    raw = re.sub(r"\s+[0-9]{1,5}\s+.*$", "", raw).strip()
    names = split_shared_last_name_names(raw)
    if not names:
        names = split_two_full_names(raw)
    if not names:
        names = [format_name(part) for part in extract_person_names(raw)]
    if not names:
        names = [
            format_name(part)
            for part in re.split(r"\band\b", raw, flags=re.IGNORECASE)
            if clean_value(part)
        ]
    if not names:
        names = split_dear_names(text)
    if not names:
        return []
    primary, secondary = choose_primary_names(names, customer)
    result = [" ".join(part for part in primary if part).strip()]
    secondary_name = " ".join(part for part in secondary if part).strip()
    if secondary_name:
        result.append(secondary_name)
    return [name for name in result if name]


def extract_auto_names(text: str, customer: str) -> list[str]:
    raw = search(
        text,
        r"Policy Number:\s*[0-9-]+\s+Effective:\s*[0-9/]+\s+\d{1,2}:\d{2}\s*[AP]M\s+"
        r"Expiration:\s*[0-9/]+\s+\d{1,2}:\d{2}\s*[AP]M\s+Named Insured\(s\):\s*(.*?)\s+[0-9]{1,5}\s+.*?e-mail Address\(es\):",
    )
    if not raw:
        raw = search(
            text,
            r"Named Insured\(s\):\s*(.*?)e-mail Address\(es\):",
        )
    names = split_shared_last_name_names(raw)
    if not names:
        names = split_two_full_names(raw)
    if not names:
        names = extract_person_names(raw)
    if not names:
        names = split_dear_names(text)
    if not names:
        return []
    primary, secondary = choose_primary_names(names, customer)
    result = [" ".join(part for part in primary if part).strip()]
    secondary_name = " ".join(part for part in secondary if part).strip()
    if secondary_name:
        result.append(secondary_name)
    return [name for name in result if name]


def extract_discounts(text: str) -> tuple[str, str, str]:
    block = search_first(
        text,
        r"Discounts Applied to Policy.*?Discount Type Discount Type(.*?)Total Discount Savings",
        r"Discounts Applied to Policy.*?Discount Type Discount Type(.*?)Other Policy Features",
        r"Discounts Applied to Policy.*?Discount Type Discount Type(.*?)Mortgagee / Other Interest",
        r"Discounts Applied to Policy.*?Discount Type Discount Type(.*?)Policy and Endorsements",
        r"Discounts Applied to Policy.*?Discount Type(.*?)Other Policy Features",
        r"Discounts Applied to Policy.*?Discount Type(.*?)Mortgagee / Other Interest",
        r"Discounts Applied to Policy.*?Discount Type(.*?)Policy and Endorsements",
    )
    names = (
        "New Home",
        "Group - Educator",
        "Group - Scientist",
        "Preferred Payment Plan",
        "Non Smoker",
        "Central Burglar Alarm",
        "Claim Free",
        "Central Fire Alarm",
        "Auto/Home Good Payer",
        "ePolicy",
        "New Roof",
        "Loyalty",
    )
    found_positions: list[tuple[int, str]] = []
    for name in names:
        match = re.search(re.escape(name), block, re.IGNORECASE)
        if match:
            found_positions.append((match.start(), name))
    found_positions.sort(key=lambda item: item[0])
    found = [name for _, name in found_positions]
    return ", ".join(found), ("Yes" if "Non Smoker" in found else ""), ("Yes" if "New Roof" in found else "")


def extract_loss_settlement_values(text: str) -> dict[str, str]:
    value_pattern = r"(Replacement Cost|Actual Cash Value|Extended Replacement Cost|Scheduled Payment|Scheduled|Covered|Not Covered)"
    match = re.search(
        rf"Roof Materials\s*Wall-to-Wall Carpet\s*Fence\s*Rest of Dwelling\s*"
        rf"{value_pattern}\s*{value_pattern}\s*{value_pattern}\s*{value_pattern}\s*"
        rf"Personal Property Contents.*?{value_pattern}",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return {}

    values = [clean_value(group) for group in match.groups()]
    return {
        "Roof Materials": values[0],
        "Wall-to-Wall Carpet": values[1],
        "Fence": values[2],
        "Rest of Dwelling": values[3],
        "Personal Property Contents (Pays up to the limit for Coverage C)": values[4],
    }


def extract_home_deductible(text: str) -> str:
    value_pattern = r"(\$[0-9,]+(?:\.\d{2})?|[0-9]+(?:\.[0-9]+)?%)"
    hurricane_combo = re.search(
        r"Deductible\s*Type of Loss Deductible\s*Applicable to each covered loss except Hurricane loss\s*"
        r"(\$[0-9,]+(?:\.\d{2})?)\s*"
        r"Hurricane Loss\s*\(([^)]+)\)\s*"
        r"(\$[0-9,]+(?:\.\d{2})?)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if hurricane_combo:
        all_perils = clean_value(hurricane_combo.group(1))
        hurricane_basis = clean_value(hurricane_combo.group(2))
        hurricane_amount = clean_value(hurricane_combo.group(3))
        hurricane_percent = search(hurricane_basis, r"([0-9]+(?:\.[0-9]+)?%)")
        if hurricane_percent:
            return f"All Perils {all_perils}; Hurricane {hurricane_percent} ({hurricane_amount})"
        return f"All Perils {all_perils}; Hurricane {hurricane_basis} {hurricane_amount}".strip()

    all_perils = search_first(
        text,
        rf"All Perils Deductible\s*{value_pattern}",
        rf"All Perils\s*Deductible\s*{value_pattern}",
    )
    wind_hail = search_first(
        text,
        rf"Wind\s*/?\s*Hail Deductible\s*{value_pattern}",
        rf"Wind and Hail Deductible\s*{value_pattern}",
    )
    if all_perils and wind_hail:
        if all_perils == wind_hail:
            return all_perils
        return f"All Perils {all_perils}; Wind/Hail {wind_hail}"
    if wind_hail:
        return f"Wind/Hail {wind_hail}"
    if all_perils:
        return all_perils

    return search_first(
        text,
        rf"Deductible\s*Type of Loss Deductible\s*Applicable to each covered loss\s*{value_pattern}",
        rf"DeductibleTypeofLoss.*?Applicabletoeachcovered(?:property)?loss\s*{value_pattern}",
        rf"Deductible\s*Type of Loss.*?Applicable to each covered(?: property)? loss\s*{value_pattern}",
    )


def extract_home_fields(text: str, customer: str) -> dict[str, str]:
    names = extract_home_names(text, customer)
    primary, secondary = choose_primary_names(names, customer)
    discounts, non_smoker, new_roof = extract_discounts(text)
    loss_settlement = extract_loss_settlement_values(text)
    coverage_a_match = re.search(
        r"Coverage A - Dwelling\s*Extended Replacement Cost\s*\(In Addition to Coverage A Limit\)\s*"
        r"(\$[0-9]{1,3}(?:,[0-9]{3})*)\s*(\d+%|Not Covered)(?:\s*\(\$[0-9,]+\))?",
        text,
        re.IGNORECASE | re.DOTALL,
    )

    condensed_property_match = re.search(
        r"Description of Property Year of Construction Construction Type Roof Type Number of Units Occupancy\s*"
        r"([0-9]{4})\s+"
        r"(Frame.*?|Plastic/Vinyl Siding|Wood Siding Over Frame)\s+"
        r"(Asphalt Shingle|Composition - [A-Za-z0-9 /-]+)\s+"
        r"[0-9]+\s+Owner Occupied",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    condensed_construction = clean_value(condensed_property_match.group(2)) if condensed_property_match else ""
    condensed_roof = clean_value(condensed_property_match.group(3)) if condensed_property_match else ""

    fields = {
        "First name": primary[0],
        "Last Name": primary[1],
        "Second named insured First name": secondary[0],
        "Second named insured Last Name": secondary[1],
        "e-mail Address(es):": search_first(
            text,
            r"e-mail\s*Address\(es\):\s*(.*?)Property Insured:",
            r"Named Insured\(s\):.*?([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\s+Property Insured:",
        ),
        "Property Insured": search_first(
            text,
            r"Property Insured:?\s*(.*?)Your\s*Farmers\s*Agent",
            r"Property Insured:?\s*(.*?)Underwritten By:",
        ),
        "Home Policy Number:": format_policy_number(
            search_first(
                text,
                r"Policy Number:\s*([A-Z0-9-]+)\s+Effective:",
                r"Policy No\.\s*([A-Z0-9-]+)",
            )
        ),
        "Effective:": search(text, r"Effective:?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})"),
        "Year Built": search_first(
            text,
            r"ZIP Code:[0-9-]+Roof Material:.*?Year Built:([0-9]{4})\s+Garage Type:",
            r"Year of Construction\s*([0-9]{4})",
            r"Description of Property Year of Construction Construction Type Roof Type Number of Units Occupancy\s*([0-9]{4})",
        ),
        "Square Footage": search(text, r"Square Footage:\s*([0-9,]+)\s+Interior Wall Construction"),
        "Style or Number of Stories": search(text, r"Style or Number of Stories:\s*(.*?)\s+Material:"),
        "Dwelling Quality Grade": search(text, r"Dwelling Quality Grade:\s*(.*?)\s+Basement:"),
        "Foundation Type": search_first(
            text,
            r"Foundation Type:\s*(.*?)Number of Units:",
            r"Foundation Type:\s*(.*?)Foundation Shape:",
        ),
        "Foundation Shape:": search(text, r"Foundation Shape:?\s*(.*?)Please note"),
        "Roof Material:": search_first(
            text,
            r"ZIP Code:[0-9-]+Roof Material:\s*(.*?)Year Built:",
            r"Roof Material:\s*(.*?)Year Built:",
            r"Roof Type\s*(.*?)Number of Units",
            re.escape(condensed_roof) if condensed_roof else r"$a",
        ),
        "Garage Type:": search(text, r"Garage Type:\s*(.*?)Square Footage:"),
        "Interior Wall Construction": search(text, r"Interior Wall Construction\s*(.*?)Style or Number of Stories:"),
        "Basement:": search(text, r"Basement:\s*(.*?)Foundation Type:"),
        "Age of Roof": search(text, r"Age of Roof\s*([0-9]+)"),
        "Roof Type": search_first(
            text,
            r"Roof Type\s*(.*?)Number of Units",
            r"Roof Type\s*(.*?)Roof Surface Material Type",
            re.escape(condensed_roof) if condensed_roof else r"$a",
        ),
        "Roof Surface Material Type": search_first(
            text,
            r"Roof Surface Material Type\s*(.*?)Property Coverage",
            re.escape(condensed_roof) if condensed_roof else r"$a",
        ),
        "Construction Type": search_first(
            text,
            r"Construction Type\s*(.*?)Occupancy",
            re.escape(condensed_construction) if condensed_construction else r"$a",
        ),
        "Deductible": extract_home_deductible(text),
        "Current Coverage A (Dwelling) Amount with Reconstruction Cost Factor:": search(
            text,
            r"Current Coverage A \(Dwelling\) Amount with Reconstruction Cost Factor:\s*(\$[0-9,]+)",
        ),
        "Recalculated Reconstruction Cost Estimate:": search(
            text,
            r"Recalculated Reconstruction Cost Estimate:\s*(\$[0-9,]+)",
        ),
        "Coverage A (Dwelling) Amount offered for this renewal:": search(
            text,
            r"Coverage A \(Dwelling\) Amount offered for this renewal:\s*(\$[0-9,]+)",
        ),
        "Coverage A - Dwelling": (
            clean_value(coverage_a_match.group(1))
            if coverage_a_match
            else search_first(
                text,
                r"Coverage A - Dwelling\s*(\$[0-9]{1,3}(?:,[0-9]{3})*)",
                r"Coverage A - Dwelling\s*Extended Replacement Cost\s*(\$[0-9]{1,3}(?:,[0-9]{3})*)",
            )
        ),
        "Extended Replacement Cost %": (
            ""
            if coverage_a_match and clean_value(coverage_a_match.group(2)).lower() == "not covered"
            else (
                clean_value(coverage_a_match.group(2))
                if coverage_a_match
                else search_first(
                    text,
                    r"Coverage A - Dwelling\s*Extended Replacement Cost\s*\(In Addition to Coverage A Limit\)\s*\$[0-9,]+\s*(\d+%)",
                    r"Coverage A - Dwelling\s*Extended Replacement Cost\s*\$[0-9,]+\s*(\$[0-9,]+)",
                )
            )
        ),
        "Coverage B - Separate Structures": search(text, r"Coverage B - Separate Structures\s*(\$[0-9,]+)"),
        "Coverage C - Personal Property": search_first(
            text,
            r"Coverage C - Personal Property\s*Contents Replacement Coverage\s*(\$[0-9,]+)",
            r"Coverage C - Personal Property\s*Contents Replacement Cost\s*(\$[0-9,]+)",
        ),
        "Coverage D - Loss of Use": search_first(
            text,
            r"Coverage D - Loss of Use\s*Additional Living Expense Term\s*(\$[0-9,]+)",
            r"Coverage D - Loss of Use\s*(\$[0-9,]+)",
        ),
        "Coverage F - Medical Payments to Others": search(text, r"Coverage F - Medical Payments to Others\s*(\$[0-9,]+)"),
        "Coverage E - Personal Liability": search_first(
            text,
            r"Coverage E - Personal Liability\s*Personal Injury\s*(\$[0-9,]+)\s*(?:Covered|Not Covered)",
            r"Coverage E - Personal Liability\s*(\$[0-9,]+)",
        ),
        "Personal Injury": search(
            text,
            r"Coverage E - Personal Liability\s*Personal Injury\s*\$[0-9,]+\s*(Covered|Not Covered)",
        ),
        "Sewer & Drain Damage": search_first(
            text,
            r"Sewer\s*&\s*Drain Damage\s*-\s*Higher Limits\s*(\$[0-9,]+)",
            r"Sewer\s*&\s*Drain Damage - Extended Contents\s*(\$[0-9,]+)",
            r"Sewer\s*&\s*Drain - Basic Contents\s*(\$[0-9,]+)",
            r"Sewer\s*&\s*Drain Damage\s*(Full Limit)",
            r"Sewer\s*&\s*Drain Damage\s*(Yes)",
            r"Sewer\s*&\s*Drain Damage\s*(See endorsement\s*[A-Z0-9-]+)",
        ),
        "Limited Matching Coverage for Siding and Roof Materials": search(
            text,
            r"Limited Matching Coverage for Siding and\s*Roof Materials\s*(\$[0-9,]+)",
        ),
        "Roof Materials": loss_settlement.get("Roof Materials", ""),
        "Wall-to-Wall Carpet": loss_settlement.get("Wall-to-Wall Carpet", ""),
        "Fence": loss_settlement.get("Fence", ""),
        "Rest of Dwelling": loss_settlement.get("Rest of Dwelling", ""),
        "Personal Property Contents (Pays up to the limit for Coverage C)": loss_settlement.get(
            "Personal Property Contents (Pays up to the limit for Coverage C)",
            "",
        ),
        "1st Mortgagee": search(
            text,
            r"1st Mortgagee\s*Loan Number\s*(.*?)\s*(?:\d{6,}|[A-Z0-9]{10,})\s*Policy and Endorsements",
        ),
        "Loan Number": search(
            text,
            r"1st Mortgagee\s*Loan Number\s*.*?\s+(\d{6,}|[A-Z0-9]{10,})\s*Policy and Endorsements",
        ),
        "Discounts": discounts,
        "Non Smoker": non_smoker,
        "New Roof": new_roof,
        "Policy Premium": search_first(
            text,
            r"Policy Premium\s*(\$[0-9,]+(?:\.\d{2})?)",
            r"Renewal\s+Premium\s*(\$[0-9,]+(?:\.\d{2})?)",
        ),
    }

    ordinance = re.search(
        r"Building Ordinance or Law\s*\((.*?)\)\s*Coverage A\s*Coverage B\s*(\$[0-9,]+)\s*(\$[0-9,]+)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if ordinance:
        fields["Building Ordinance or Law"] = (
            f"{clean_value(ordinance.group(1))}; "
            f"Coverage A {clean_value(ordinance.group(2))}; "
            f"Coverage B {clean_value(ordinance.group(3))}"
        )
    else:
        simple_ordinance = search(text, r"Building Ordinance or Law\s*(\d+%)")
        fields["Building Ordinance or Law"] = simple_ordinance

    if fields.get("Age of Roof"):
        fields["Age of Roof"] = f"{fields['Age of Roof']} years"

    if condensed_roof and fields.get("Roof Type") == "Roof Type Number of Units":
        fields["Roof Type"] = condensed_roof
    if condensed_construction and fields.get("Construction Type") == "Roof Type Number of Units":
        fields["Construction Type"] = condensed_construction

    extended_replacement = clean_value(fields.get("Extended Replacement Cost %", ""))
    coverage_a = clean_value(fields.get("Coverage A - Dwelling", ""))
    if extended_replacement.startswith("$") and coverage_a.startswith("$"):
        try:
            extra_amount = float(extended_replacement.replace("$", "").replace(",", ""))
            base_amount = float(coverage_a.replace("$", "").replace(",", ""))
            if base_amount > 0:
                percent = round((extra_amount / base_amount) * 100)
                fields["Extended Replacement Cost %"] = f"{percent}%"
        except ValueError:
            pass

    return fields


def parse_auto_household_drivers(text: str) -> str:
    block = search(text, r"Household Drivers\s*Name Driver Status Name Driver Status(.*?)Vehicle Information")
    pairs = re.findall(r"([A-Z][a-z]+(?: [A-Z])? [A-Z][a-z]+)\s+(Covered|Excluded|Not Rated)", block)
    return ", ".join(f"{name} ({status})" for name, status in pairs)


def parse_auto_vehicle_summary(text: str) -> dict[str, str]:
    match = re.search(
        r"Vehicle Information.*?1\s+(.*?)\s+(?:Comprehensive|Other than Collision):\s*(Not Covered|\$[0-9,]+)\s+"
        r"([A-HJ-NPR-Z0-9]{17})\s+Collision:\s*(Not Covered|\$[0-9,]+)\s+Vehicle Level Coverage Items",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return {}
    return {
        "description": clean_value(match.group(1)),
        "other_than_collision_deductible": clean_value(match.group(2)),
        "vin": clean_value(match.group(3)),
        "collision_deductible": clean_value(match.group(4)),
    }


def parse_auto_vehicles(text: str) -> list[dict[str, str]]:
    block = search(
        text,
        r"Vehicle Information\s*Veh\. # Year/Make/Model/VIN Limit Coverage Deductible(.*?)Vehicle Level Coverage Items",
    )
    if not block:
        summary = parse_auto_vehicle_summary(text)
        return [summary] if summary else []

    pattern = re.compile(
        r"(\d+)\s+"
        r"(.*?)\s+"
        r"(?:Comprehensive|Other than Collision):\s*(Not Covered|\$[0-9,]+)\s+"
        r"([A-HJ-NPR-Z0-9]{17})\s+"
        r"Collision:\s*(Not Covered|\$[0-9,]+)"
        r"(?:\s+Uninsured Motorist Property Damage:\s*\$[0-9,]+(?:\s+\$[0-9,]+ each accident)?)?"
        r"(?:\s+\$[0-9,]+ each accident)?"
        r"(?=\s*(?:\.\s*)?\d+\s+[0-9]{4}|\s*$)",
        re.IGNORECASE | re.DOTALL,
    )
    vehicles: list[dict[str, str]] = []
    for match in pattern.finditer(block):
        vehicles.append(
            {
                "number": clean_value(match.group(1)),
                "description": clean_value(match.group(2)),
                "other_than_collision_deductible": clean_value(match.group(3)),
                "vin": clean_value(match.group(4)),
                "collision_deductible": clean_value(match.group(5)),
            }
        )
    if vehicles:
        return vehicles

    summary = parse_auto_vehicle_summary(text)
    return [summary] if summary else []


def parse_auto_discount_names(text: str) -> str:
    block = search(
        text,
        r"Discounts\s*Discount Type Applies to Vehicle\(s\)\s*Discount Type Applies to Vehicle\(s\)(.*?)"
        r"(?:Total Estimated Discount Savings|Other Policy Features|Policy and Endorsements)",
    )
    block = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", block)
    block = re.sub(r"\be\s+Policy\b", "ePolicy", block, flags=re.IGNORECASE)
    names = re.findall(r"([A-Za-z/&-]+(?: [A-Za-z/&-]+)*)\s+\d", block)
    return ", ".join(unique_preserve(names))


def parse_auto_coverages(text: str) -> dict[str, str]:
    return {
        "Bodily Injury Liability": search(
            text,
            r"Bodily Injury Liability\s+(\$[0-9,]+ each person\s+\$[0-9,]+ each accident)",
        ).replace(" $", "/$"),
        "Property Damage Liability": search(
            text,
            r"Property Damage Liability\s+(\$[0-9,]+ each accident)",
        ),
        "Uninsured Motorist Property": search(
            text,
            r"Uninsured Motorist Property Damage(?: \(Alternative Coverage\))?\s+(\$[0-9,]+ each (?:person|accident))",
        ),
        "Uninsured Motorist Bodily Injury": search(
            text,
            r"Uninsured Motorist Bodily Injury(?: \(Alternative Coverage\))?\s+(\$[0-9,]+ each person\s+\$[0-9,]+ each accident)",
        ).replace(" $", "/$"),
        "Towing and Labor Costs": search(
            text,
            r"Towing and Labor Costs\s+(Not Covered|\$[0-9,]+(?:\.\d{2})?)",
        ),
        "Transportation Expense Coverage": search(
            text,
            r"Transportation Expense Coverage\s+(Not Covered|\$[0-9,]+(?:\.\d{2})?)",
        ),
        "Glass Deductible": search(
            text,
            r"Other than Collision - (\$[0-9,]+) Glass Deductible",
        ),
    }


def extract_vehicle_level_row_values(text: str, label: str, next_label: str) -> list[str]:
    pattern = rf"{re.escape(label)}\s+(.*?)(?={re.escape(next_label)})"
    block = search(text, pattern)
    if not block:
        return []
    return re.findall(r"Not Covered|\$[0-9,]+(?:\.\d{2})?", block)


def parse_auto_lienholder(text: str) -> str:
    block = search(text, r"Lienholder and Additional Interest(.*?)Policy and Endorsements")
    match = re.search(
        r"Vehicle Lienholder Loan Number\s+(.*?)\s+VIN:\s+[A-HJ-NPR-Z0-9]{17}\s+(.*?)\s+Not Applicable",
        block,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    vehicle_name = clean_value(match.group(1))
    details = clean_value(match.group(2))
    return f"{vehicle_name} Lienholder - {details}".strip()


def extract_auto_fields(text: str, customer: str) -> dict[str, str]:
    names = extract_auto_names(text, customer)
    primary, secondary = choose_primary_names(names, customer)
    vehicles = parse_auto_vehicles(text)
    vehicle = vehicles[0] if vehicles else {}
    coverages = parse_auto_coverages(text)
    policy_number = format_policy_number(search(text, r"Policy Number:\s*([0-9-]+)"))
    effective = search(text, r"Effective:\s*([0-9/]+)")
    expiration = search(text, r"Expiration:\s*([0-9/]+)")
    premium = search(text, r"Policy Premium\s*(\$[0-9,]+(?:\.\d{2})?)")
    header_address = search(
        text,
        r"Auto Insurance\s+Renewal\s+farmers\.com\s+.*?[A-Z][A-Z0-9 .'-]+\s+([0-9]{1,5}\s+[A-Z0-9#.,' -]+?\s+[A-Z]{2}\s+[0-9]{5}(?:-[0-9]{4})?)\s+Your Farmers\s+Policy",
    )

    towing_values = extract_vehicle_level_row_values(
        text,
        "Towing and Labor Costs",
        "Uninsured Motorist Property Damage",
    )
    transportation_values = extract_vehicle_level_row_values(
        text,
        "Transportation Expense Coverage",
        "farmers.com",
    )

    vehicle_lines: list[str] = []
    if vehicle:
        vehicle_lines.append(
            f"{vehicle['description']} - {vehicle['vin']}"
        )
        if vehicle.get("other_than_collision_deductible"):
            vehicle_lines.append(
                f"Other than Collision deductible {vehicle['other_than_collision_deductible']}"
            )
        if vehicle.get("collision_deductible"):
            vehicle_lines.append(
                f"Collision deductible {vehicle['collision_deductible']}"
            )

    fields = {
        "First name": primary[0],
        "Last Name": primary[1],
        "Second named insured First name": secondary[0],
        "Second named insured Last Name": secondary[1],
        "Property Insured": header_address or search(
            text,
            r"Named Insured\(s\):.*?\s([0-9]{1,5}\s+[A-Za-z0-9#.,' -]+?\s+[A-Z]{2}\s+[0-9]{5}(?:-[0-9]{4})?)\s+(?:e-mail Address\(es\):|Underwritten By:)",
        ),
        "e-mail Address(es):": search(text, r"e-mail Address\(es\):\s*(.*?)Underwritten By:"),
        "Auto Policy Number": policy_number,
        "Auto Effective": effective,
        "Auto Expiration": expiration,
        "Vehicle Information": "\n".join(vehicle_lines),
        "Bodily Injury Liability": coverages["Bodily Injury Liability"],
        "Property Damage Liability": coverages["Property Damage Liability"],
        "Other than Collision": "Yes" if vehicle.get("other_than_collision_deductible") and vehicle["other_than_collision_deductible"] != "Not Covered" else "No",
        "Collision": "Yes" if vehicle.get("collision_deductible") and vehicle["collision_deductible"] != "Not Covered" else "No",
        "Towing and Labor Costs": "Yes" if coverages["Towing and Labor Costs"] and coverages["Towing and Labor Costs"] != "Not Covered" else "No",
        "Uninsured Motorist Property": coverages["Uninsured Motorist Property"],
        "Uninsured Motorist Bodily Injury": coverages["Uninsured Motorist Bodily Injury"],
        "Discounts": parse_auto_discount_names(text),
        "Auto policy premium": premium,
        "Household Drivers": parse_auto_household_drivers(text),
        "_vehicles": vehicles,
        "_vehicle_description": vehicle.get("description", ""),
        "_vehicle_vin": vehicle.get("vin", ""),
        "_vehicle_year": search(vehicle.get("description", ""), r"^([0-9]{4})"),
        "_other_than_collision_deductible": vehicle.get("other_than_collision_deductible", ""),
        "_collision_deductible": vehicle.get("collision_deductible", ""),
        "_towing_value": coverages["Towing and Labor Costs"],
        "_transportation_value": coverages["Transportation Expense Coverage"],
        "_glass_deductible": coverages["Glass Deductible"],
        "_lienholder": parse_auto_lienholder(text),
        "_towing_values": towing_values,
        "_transportation_values": transportation_values,
    }
    return fields


def merge_shared_fields(shared: dict[str, str], update: dict[str, str]) -> dict[str, str]:
    for key in (
        "First name",
        "Last Name",
        "Second named insured First name",
        "Second named insured Last Name",
        "e-mail Address(es):",
        "Property Insured",
    ):
        if not shared.get(key) and update.get(key):
            shared[key] = update[key]
    return shared


def merge_nonempty_fields(target: dict[str, str], update: dict[str, str]) -> dict[str, str]:
    for key, value in update.items():
        if value or value == OMIT_LINE:
            target[key] = value
    return target


def summarize_documents(values: list[tuple[str, dict[str, str]]], key: str) -> str:
    lines: list[str] = []
    for policy_number, data in values:
        value = clean_value(data.get(key, ""))
        if not value:
            continue
        if len(values) == 1:
            lines.append(value)
        else:
            lines.append(f"{policy_number}: {value}" if policy_number else value)
    return "\n".join(lines)


def summarize_unique(values: list[str]) -> str:
    cleaned = unique_preserve(values)
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return "\n".join(cleaned)


def sort_auto_docs(auto_docs: list[dict[str, str]]) -> list[dict[str, str]]:
    def sort_key(doc: dict[str, str]) -> tuple[int, str, str]:
        year_text = clean_value(doc.get("_vehicle_year", ""))
        year = int(year_text) if year_text.isdigit() else 0
        return (-year, clean_value(doc.get("_vehicle_description", "")), clean_value(doc.get("_vehicle_vin", "")))

    return sorted(auto_docs, key=sort_key)


def flatten_auto_vehicles(auto_docs: list[dict[str, str]]) -> list[dict[str, str]]:
    vehicles: list[dict[str, str]] = []
    for doc in auto_docs:
        doc_vehicles = doc.get("_vehicles") or []
        if doc_vehicles:
            for vehicle in doc_vehicles:
                merged = dict(vehicle)
                merged["policy_number"] = doc.get("Auto Policy Number", "")
                merged["policy_premium"] = doc.get("Auto policy premium", "")
                merged["towing_values"] = doc.get("_towing_values", [])
                merged["transportation_values"] = doc.get("_transportation_values", [])
                merged["glass_deductible"] = doc.get("_glass_deductible", "")
                vehicles.append(merged)
            continue

        vehicles.append(
            {
                "description": doc.get("_vehicle_description", ""),
                "vin": doc.get("_vehicle_vin", ""),
                "other_than_collision_deductible": doc.get("_other_than_collision_deductible", ""),
                "collision_deductible": doc.get("_collision_deductible", ""),
                "policy_number": doc.get("Auto Policy Number", ""),
                "policy_premium": doc.get("Auto policy premium", ""),
                "towing_values": [doc.get("_towing_value", "")],
                "transportation_values": [doc.get("_transportation_value", "")],
                "glass_deductible": doc.get("_glass_deductible", ""),
            }
        )
    return vehicles


def combine_auto_fields(auto_docs: list[dict[str, str]]) -> dict[str, str]:
    if not auto_docs:
        return {}

    auto_docs = sort_auto_docs(auto_docs)
    vehicles = flatten_auto_vehicles(auto_docs)
    vehicles.sort(
        key=lambda vehicle: (
            -(int(search(vehicle.get("description", ""), r"^([0-9]{4})")) if search(vehicle.get("description", ""), r"^([0-9]{4})").isdigit() else 0),
            clean_value(vehicle.get("description", "")),
            clean_value(vehicle.get("vin", "")),
        )
    )
    policy_numbers = unique_preserve([doc.get("Auto Policy Number", "") for doc in auto_docs])
    effective_values = unique_preserve([doc.get("Auto Effective", "") for doc in auto_docs])
    expiration_values = unique_preserve([doc.get("Auto Expiration", "") for doc in auto_docs])

    discount_names: list[str] = []
    for doc in auto_docs:
        discount_names.extend(part.strip() for part in doc.get("Discounts", "").split(","))

    vehicle_info_lines: list[str] = []
    other_collision_lines: list[str] = []
    towing_lines: list[str] = []
    premium_lines: list[str] = []
    lienholder_lines: list[str] = []

    for index, vehicle in enumerate(vehicles, start=1):
        description = clean_value(vehicle.get("description", ""))
        vin = clean_value(vehicle.get("vin", ""))
        if description and vin:
            vehicle_info_lines.append(f"Vehicle VIN {index}: {vin} - {description}")

        other_deductible = clean_value(vehicle.get("other_than_collision_deductible", ""))
        collision_deductible = clean_value(vehicle.get("collision_deductible", ""))
        if description and (other_deductible or collision_deductible):
            parts: list[str] = []
            if other_deductible:
                parts.append(f"Other than Collision deductible {other_deductible}")
            if collision_deductible:
                parts.append(f"Collision deductible {collision_deductible}")
            other_collision_lines.append(f"Vehicle {index} {description}: {', '.join(parts)}")

        towing_values = vehicle.get("towing_values") or []
        vehicle_number = int(vehicle.get("number", str(index))) if clean_value(vehicle.get("number", "")).isdigit() else index
        towing_value = clean_value(towing_values[vehicle_number - 1]) if vehicle_number - 1 < len(towing_values) else ""
        if towing_value:
            towing_text = "covered" if towing_value != "Not Covered" else "not covered"
            towing_lines.append(f"Vehicle {index}: Towing and Labor Costs {towing_text}")

        transportation_values = vehicle.get("transportation_values") or []
        transportation_value = clean_value(transportation_values[vehicle_number - 1]) if vehicle_number - 1 < len(transportation_values) else ""
        if transportation_value:
            transport_text = f"{transportation_value} /month" if transportation_value != "Not Covered" else "not included"
        else:
            transport_text = "not included (not found)"
        towing_lines.append(f"Vehicle {index}: Transportation expense coverage {transport_text}")

        premium = clean_value(vehicle.get("policy_premium", ""))
        if premium and len(auto_docs) > 1:
            premium_lines.append(f"Premium Vehicle {index}: {premium}")

    if len(auto_docs) == 1:
        premium = clean_value(auto_docs[0].get("Auto policy premium", ""))
        if premium:
            premium_lines.append(premium)

    lienholders = unique_preserve([doc.get("_lienholder", "") for doc in auto_docs])
    if lienholders:
        lienholder_lines.extend(lienholders)

    glass_values = unique_preserve([vehicle.get("glass_deductible", "") for vehicle in vehicles])
    if glass_values:
        if len(glass_values) == 1 and len(vehicles) > 1:
            other_collision_lines.append(f"{glass_values[0]} Other than Collision Glass deductible on both vehicles")
        else:
            for index, vehicle in enumerate(vehicles, start=1):
                glass_value = clean_value(vehicle.get("glass_deductible", ""))
                if glass_value:
                    other_collision_lines.append(f"Vehicle {index}: {glass_value} Other than Collision Glass deductible")

    if lienholder_lines:
        vehicle_info_lines.extend(lienholder_lines)

    bodily_injury = summarize_unique([doc.get("Bodily Injury Liability", "") for doc in auto_docs])
    property_damage = summarize_unique([doc.get("Property Damage Liability", "") for doc in auto_docs])
    uninsured_property = summarize_unique([doc.get("Uninsured Motorist Property", "") for doc in auto_docs])
    uninsured_bi = summarize_unique([doc.get("Uninsured Motorist Bodily Injury", "") for doc in auto_docs])

    combined = {
        "Auto Policy Number": ", ".join(policy_numbers),
        "Auto Effective": effective_values[0] if len(effective_values) == 1 else "\n".join(effective_values),
        "Auto Expiration": expiration_values[0] if len(expiration_values) == 1 else "\n".join(expiration_values),
        "Vehicle Information": "\n".join(vehicle_info_lines),
        "Bodily Injury Liability": bodily_injury,
        "Property Damage Liability": property_damage,
        "Other than Collision": "\n".join(other_collision_lines),
        "Collision": OMIT_LINE,
        "Towing and Labor Costs": "\n".join(towing_lines),
        "Uninsured Motorist Property": uninsured_property,
        "Uninsured Motorist Bodily Injury": uninsured_bi,
        "Auto Discounts": ", ".join(unique_preserve(discount_names)),
        "Auto policy premium": "\n".join(premium_lines),
        "Household Drivers": ", ".join(
            unique_preserve([doc.get("Household Drivers", "") for doc in auto_docs])
        ),
        "e-mail Address(es):__2": "",
    }
    return combined


def combine_home_fields(home_docs: list[dict[str, str]]) -> dict[str, str]:
    if not home_docs:
        return {}
    if len(home_docs) == 1:
        combined = home_docs[0].copy()
        if combined.get("Home Policy Number:") and not combined.get("Policy Number:"):
            combined["Policy Number:"] = combined["Home Policy Number:"]
        return combined

    tuples = [(doc.get("Home Policy Number:", ""), doc) for doc in home_docs]
    combined = home_docs[0].copy()
    for key in (
        "Home Policy Number:",
        "Effective:",
        "Policy Premium",
        "Year Built",
        "Age of Roof",
        "Square Footage",
        "Deductible",
        "Coverage A - Dwelling",
        "Coverage B - Separate Structures",
        "Coverage C - Personal Property",
        "Coverage D - Loss of Use",
        "Coverage E - Personal Liability",
        "Coverage F - Medical Payments to Others",
    ):
        combined[key] = summarize_documents(tuples, key)
    if combined.get("Home Policy Number:") and not combined.get("Policy Number:"):
        combined["Policy Number:"] = combined["Home Policy Number:"]
    return combined


def strip_template_guidance(template_text: str) -> str:
    template_text = template_text.replace("{Discounts}", "{Auto Discounts}")
    template_text = re.sub(r"\}(?:\([^{}\n]*\))+", "}", template_text)
    template_text = re.sub(
        r"\}(?:\([^{}\n]*?(?:include|This one is|Need each)[^{}\n]*\}?)+",
        "}",
        template_text,
        flags=re.IGNORECASE,
    )
    template_text = template_text.replace("(if found)", "")
    # Some template placeholders are wrapped across lines; normalize internal
    # whitespace so token matching stays stable during line-by-line rendering.
    template_text = re.sub(
        r"\{([^{}]+)\}",
        lambda match: "{" + clean_label(match.group(1)) + "}",
        template_text,
        flags=re.DOTALL,
    )
    return template_text


def render_auto_only_notes(fields: dict[str, str]) -> str:
    lines = [
        " ".join(part for part in (fields.get("First name", ""), fields.get("Last Name", "")) if part).strip(),
        f"Date of birth: {fields.get('Date of birth', '')}".rstrip(),
        f"Social security number: {fields.get('Social security number', '')}".rstrip(),
        f"Drivers license #: {fields.get('Drivers license #', '')}".rstrip(),
        "",
    ]

    if fields.get("__has_second_insured__"):
        lines.extend(
            [
                " ".join(
                    part
                    for part in (
                        fields.get("Second named insured First name", ""),
                        fields.get("Second named insured Last Name", ""),
                    )
                    if part
                ).strip(),
                f"Date of birth: {fields.get('Date of birth__2', '')}".rstrip(),
                f"Social security number: {fields.get('Social security number__2', '')}".rstrip(),
                f"Drivers license #: {fields.get('Drivers license #__2', '')}".rstrip(),
                "",
            ]
        )

    lines.extend(
        [
            f"Phone number: {fields.get('Phone number', '')}".rstrip(),
            f"e-mail Address(es): {fields.get('e-mail Address(es):', '')}".rstrip(),
        ]
    )
    lines.extend(render_auto_section_lines(fields, include_marker=True))
    return "\n".join(collapse_blank_lines([line.rstrip() for line in lines])).rstrip() + "\n"


def render_auto_section_lines(fields: dict[str, str], include_marker: bool) -> list[str]:
    lines = [
        "///////////Auto/////////" if include_marker else "",
    ]

    for label in (
        "Auto Policy Number",
        "Auto Effective",
        "Auto Expiration",
        "Vehicle Information",
        "Household Drivers",
        "Bodily Injury Liability",
        "Property Damage Liability",
        "Other than Collision",
        "Towing and Labor Costs",
        "Uninsured Motorist Property",
        "Uninsured Motorist Bodily Injury",
        "Auto Discounts",
        "Auto policy premium",
    ):
        value = clean_value(fields.get(label, ""))
        if not value or value == OMIT_LINE:
            continue
        display_label = "Discounts" if label == "Auto Discounts" else label
        lines.append(f"{display_label}: {value}")

    return [line.rstrip() for line in lines if line.rstrip()]


def render_template(template_text: str, fields: dict[str, str]) -> str:
    lines: list[str] = []
    counters: dict[str, int] = {}
    current_section: str | None = None

    def resolve_value(raw_label: str, section: str | None) -> tuple[str, str]:
        counters[raw_label] = counters.get(raw_label, 0) + 1
        occurrence = counters[raw_label]

        if raw_label in SHARED_SINGLETON_LABELS:
            if occurrence > 1:
                return raw_label, OMIT_LINE
            return raw_label, fields.get(raw_label, "")

        alias_candidates = SECTION_FIELD_ALIASES.get(section or "", {}).get(raw_label, ())
        for key in alias_candidates:
            if key in fields:
                return raw_label, fields.get(key, "")

        key = raw_label if occurrence == 1 else f"{raw_label}__{occurrence}"
        if key in fields:
            return raw_label, fields[key]
        if occurrence == 1:
            return raw_label, fields.get(raw_label, "")
        return raw_label, ""

    def replace_token(match: re.Match[str]) -> str:
        raw_label = clean_label(match.group(1))
        display_label, value = resolve_value(raw_label, current_section)
        if display_label == "Auto Discounts":
            display_label = "Discounts"

        if display_label == "First name":
            return f"{value} " if value and fields.get("Last Name") else value
        if display_label == "Last Name":
            return value
        if display_label == "Second named insured First name":
            return f"{value} " if value and fields.get("Second named insured Last Name") else value
        if display_label == "Second named insured Last Name":
            return value

        if display_label.endswith(":"):
            return f"{display_label} {value}".rstrip()
        return f"{display_label}: {value}".rstrip()

    for template_line in strip_template_guidance(template_text).splitlines():
        line = template_line.rstrip()
        marker_section = SECTION_MARKERS.get(line.strip())
        if marker_section:
            current_section = None if current_section == marker_section else marker_section

        rendered_line = re.sub(r"\{([^{}]+)\}", replace_token, line, flags=re.DOTALL)
        line = rendered_line.rstrip()
        if OMIT_LINE in line:
            continue
        label = clean_label(line)
        if label in PLAIN_LABELS:
            display_label, value = resolve_value(label, current_section)
            if display_label == "Auto Discounts":
                display_label = "Discounts"
            lines.append(f"{label}: {value}".rstrip())
            continue
        lines.append(line.rstrip())

    lines = postprocess_rendered_lines(lines, fields)
    return "\n".join(lines).rstrip() + "\n"


def postprocess_rendered_lines(lines: list[str], fields: dict[str, str]) -> list[str]:
    output = list(lines)

    if not fields.get("__has_second_insured__"):
        output = remove_second_insured_block(output)
    if not fields.get("__has_home__"):
        output = remove_section(output, "///////////Home/////////", "///////////Auto/////////")
    if not fields.get("__has_auto__"):
        output = remove_section(output, "///////////Auto/////////", None)
        output = remove_last_marker(output, "///////////Home/////////")

    output = remove_blank_label_lines(
        output,
        {
            "Number of Units:",
            "Building Ordinance or Law:",
            "Sewer & Drain Damage:",
            "1st Mortgagee:",
            "Loan Number:",
        },
    )

    output = [line for line in output if line != "///////////Home/////////"]

    return collapse_blank_lines(output)


def remove_second_insured_block(lines: list[str]) -> list[str]:
    if len(lines) < 9:
        return lines
    block = lines[4:9]
    if len(block) == 5:
        return lines[:4] + lines[9:]
    return lines


def remove_section(lines: list[str], start_marker: str, end_marker: str | None) -> list[str]:
    try:
        start = lines.index(start_marker)
    except ValueError:
        return lines

    end = len(lines)
    if end_marker is not None:
        try:
            end = lines.index(end_marker, start + 1)
        except ValueError:
            end = len(lines)
    return lines[:start] + lines[end:]


def remove_last_marker(lines: list[str], marker: str) -> list[str]:
    for index in range(len(lines) - 1, -1, -1):
        if lines[index] == marker:
            return lines[:index] + lines[index + 1 :]
    return lines


def collapse_blank_lines(lines: list[str]) -> list[str]:
    collapsed: list[str] = []
    blank_run = 0
    for line in lines:
        if line.strip():
            blank_run = 0
            collapsed.append(line)
            continue
        if blank_run == 0:
            collapsed.append("")
        blank_run += 1
    while collapsed and collapsed[-1] == "":
        collapsed.pop()
    return collapsed


def remove_blank_label_lines(lines: list[str], labels: set[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped in labels:
            continue
        result.append(line)
    return result


def choose_policy_pdfs(customer: str) -> tuple[list[Path], list[Path]]:
    home_paths: list[Path] = []
    auto_paths: list[Path] = []
    condo_paths: list[Path] = []
    for candidate in find_candidates(customer):
        policy_type = policy_type_for_path(candidate)
        if policy_type == "home":
            home_paths.append(candidate)
        elif policy_type == "auto":
            auto_paths.append(candidate)
        elif policy_type in ("condo", "renters"):
            condo_paths.append(candidate)
    return home_paths, auto_paths, condo_paths


def build_output_path(customer: str) -> Path:
    compact_name = re.sub(r"[^A-Za-z0-9]", "", customer)
    return DEFAULT_OUTPUT_DIR / f"{compact_name}{DEFAULT_OUTPUT_SUFFIX}"


def extract_condo_fields(text: str, customer: str) -> dict[str, str]:
    names = extract_home_names(text, customer)
    primary, secondary = choose_primary_names(names, customer)
    doc_type = "renters" if re.search(r"\bRenters\b", text, re.IGNORECASE) else "condo"
    property_insured = search_first(
        text,
        r"Property Insured:?\s*(.*?)Your\s*Farmers\s*Agent",
        r"Property Insured:?\s*(.*?)Underwritten By:",
        r"Property Insured\s*(.*?)Your\s*Farmers\s*Agent",
    )
    year_built = search(text, r"Description of Property Year of Construction.*?Occupancy\s*([0-9]{4})")
    if not year_built:
        year_built = search(text, r"Year of Construction\s*([0-9]{4})")
    condo_coverage = search(
        text,
        r"Property Coverage Coverage\s*Limit Coverage\s*Limit\s*"
        r"Coverage C - Personal Property\s*Contents Replacement Cost\s*"
        r"Unit Owner'?s Building Property\s*(\$[0-9,]+)\s*(?:Covered|Not Covered)\s*(\$[0-9,]+)\s*"
        r"Coverage D - Loss of Use\s*(\$[0-9,]+)",
    )
    personal_property = ""
    building_property = ""
    loss_of_use_amount = ""
    coverage_match = re.search(
        r"Property Coverage Coverage\s*Limit Coverage\s*Limit\s*"
        r"Coverage C - Personal Property\s*Contents Replacement Cost\s*"
        r"Unit Owner'?s Building Property\s*(\$[0-9,]+)\s*(?:Covered|Not Covered)\s*(\$[0-9,]+)\s*"
        r"Coverage D - Loss of Use\s*(\$[0-9,]+)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if coverage_match:
        personal_property = clean_value(coverage_match.group(1))
        building_property = clean_value(coverage_match.group(2))
        loss_of_use_amount = clean_value(coverage_match.group(3))
    if not personal_property:
        personal_property = search(text, r"Coverage C - Personal Property.*?(\$[0-9,]+)")
    if not building_property:
        building_property = search_first(
            text,
            r"Unit Owner'?s Building Property\s*(\$[0-9,]+)",
            r"Coverage A - Unit Owner'?s Building Property\s*(\$[0-9,]+)",
        )
    if not loss_of_use_amount:
        loss_of_use_amount = search(text, r"Coverage D - Loss of Use.*?(\$[0-9,]+)")
    loss_of_use_percent = search_first(
        text,
        r"Coverage D - Loss of Use\s*(\d+%)",
        r"Loss of Use\s*(\d+%)",
        r"Additional Living Expense\s*(\d+%)",
    )
    expiration = search(text, r"Expiration:\s*([0-9/]+)")

    return {
        "doc_type": doc_type,
        "primary_name": " ".join(part for part in primary if part).strip(),
        "secondary_name": " ".join(part for part in secondary if part).strip(),
        "email": search_first(
            text,
            r"Named Insured\(s\):.*?([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})\s+Property Insured",
            r"e-mail\s*Address\(es\):\s*(.*?)Property Insured",
        ),
        "property_insured": property_insured,
        "policy_number": format_policy_number(
            search_first(
                text,
                r"Policy Number:\s*([A-Z0-9-]+)\s+Effective:",
                r"Policy No\.\s*([A-Z0-9-]+)",
            )
        ),
        "effective": search(text, r"Effective:\s*([0-9/]+)"),
        "expiration": expiration,
        "year_built": year_built,
        "deductible": search_first(
            text,
            r"All other covered property losses\s*(\$[0-9,]+)",
            r"All Perils Deductible\s*(\$[0-9,]+)",
            r"Deductible\s*(\$[0-9,]+)",
            r"Applicable to each covered loss\s*(\$[0-9,]+)",
        ),
        "personal_property": personal_property,
        "loss_of_use": loss_of_use_amount,
        "loss_of_use_percent": loss_of_use_percent,
        "personal_liability": search(text, r"Coverage E - Personal Liability.*?(\$[0-9,]+)"),
        "medical_payments": search(text, r"Coverage F - Medical Payments to Others\s*(\$[0-9,]+)"),
        "building_property": building_property,
        "loss_assessment": search(text, r"Association Loss Assessment\s*(\$[0-9,]+)"),
        "mortgagee": search(
            text,
            r"1st Mortgagee\s*Loan Number\s*(.*?)\s*(?:\d{6,}|[A-Z0-9]{10,})\s*Policy and Endorsements",
        ),
        "loan_number": search(
            text,
            r"1st Mortgagee\s*Loan Number\s*.*?\s+(\d{6,}|[A-Z0-9]{10,})\s*Policy and Endorsements",
        ),
        "policy_premium": search_first(
            text,
            r"Renewal\s+Premium\s*(\$[0-9,]+(?:\.\d{2})?)",
            r"Policy Premium\s*(\$[0-9,]+(?:\.\d{2})?)",
        ),
    }


def parse_existing_condo_overrides(note_text: str) -> dict[str, str]:
    overrides: dict[str, str] = {}
    lines = note_text.splitlines()
    if lines:
        overrides["primary_name"] = lines[0].strip()
    if len(lines) >= 2 and lines[1].startswith("Date of birth:"):
        overrides["date_of_birth"] = lines[1].split(":", 1)[1].strip()
    if (
        len(lines) >= 5
        and lines[3].strip()
        and ":" not in lines[3]
        and lines[4].startswith("Date of birth:")
    ):
        overrides["secondary_name"] = lines[3].strip()
        overrides["date_of_birth__2"] = lines[4].split(":", 1)[1].strip()

    for label, key in (
        ("Phone number:", "phone_number"),
        ("e-mail Address(es):", "email"),
        ("Property Insured:", "property_insured"),
        ("Policy Number:", "policy_number"),
        ("Effective:", "effective"),
        ("Expiration:", "expiration"),
        ("Year Built:", "year_built"),
        ("Deductible:", "deductible_rendered"),
        ("Loss of Use:", "loss_of_use"),
        ("Loss of Use %:", "loss_of_use_percent"),
        ("Medical Payments to Others:", "medical_payments"),
        ("Guest Medical:", "medical_payments"),
        ("Personal Liability:", "personal_liability"),
        ("Personal Property:", "personal_property"),
        ("Personal Property Limit:", "personal_property"),
        ("Building Property:", "building_property"),
        ("Loss Assessment:", "loss_assessment"),
        ("Personal Property ", "personal_property"),
        ("Building Property ", "building_property"),
        ("Loss Assessment ", "loss_assessment"),
        ("1st Mortgagee:", "mortgagee"),
        ("Loan Number:", "loan_number"),
        ("Policy Premium:", "policy_premium"),
    ):
        for line in lines:
            if line.startswith(label):
                overrides[key] = line[len(label) :].strip()
                break
    return overrides


def render_condo_notes(customer: str, condo_paths: list[Path], existing_text: str = "") -> str:
    text_by_path = {path: read_pdf_text(path) for path in condo_paths}
    docs = [extract_condo_fields(text_by_path[path], customer) for path in condo_paths]
    overrides = parse_existing_condo_overrides(existing_text) if existing_text else {}
    primary_name = next((doc["primary_name"] for doc in docs if doc.get("primary_name")), customer)
    secondary_name = next((doc["secondary_name"] for doc in docs if doc.get("secondary_name")), "")
    primary_doc = docs[0] if docs else {}
    primary_name = overrides.get("primary_name", primary_name)
    secondary_name = overrides.get("secondary_name", secondary_name)
    is_renters = primary_doc.get("doc_type") == "renters"

    lines = [
        primary_name,
        f"Date of birth: {overrides.get('date_of_birth', '')}".rstrip(),
        "",
    ]
    if secondary_name:
        lines.extend(
            [
                secondary_name,
                f"Date of birth: {overrides.get('date_of_birth__2', '')}".rstrip(),
                "",
            ]
        )

    lines.extend(
        [
            f"Phone number: {overrides.get('phone_number', '')}".rstrip(),
            f"e-mail Address(es): {overrides.get('email', primary_doc.get('email', ''))}".rstrip(),
            f"Property Insured: {overrides.get('property_insured', primary_doc.get('property_insured', ''))}".rstrip(),
            f"Policy Number: {overrides.get('policy_number', primary_doc.get('policy_number', ''))}".rstrip(),
            f"Effective: {overrides.get('effective', primary_doc.get('effective', ''))}".rstrip(),
            f"Expiration: {overrides.get('expiration', primary_doc.get('expiration', ''))}".rstrip(),
            (f"Year Built: {overrides.get('year_built', primary_doc.get('year_built', ''))}".rstrip() if not is_renters else ""),
            "",
        ]
    )

    deductible = clean_value(primary_doc.get("deductible", ""))
    deductible_line = overrides.get("deductible_rendered", "")
    if deductible_line:
        lines.append(f"Deductible: {deductible_line}".rstrip())
    else:
        lines.append(f"Deductible: {deductible} All perils".rstrip() if deductible else "Deductible:")
    lines.append("")
    lines.append("")

    if is_renters:
        lines.append(
            f"Personal Property Limit: {clean_value(overrides.get('personal_property', primary_doc.get('personal_property', '')))}".rstrip()
        )
        lines.append(
            f"Loss of Use %: {clean_value(overrides.get('loss_of_use_percent', primary_doc.get('loss_of_use_percent', '')))}".rstrip()
        )
        lines.append(
            f"Personal Liability: {clean_value(overrides.get('personal_liability', primary_doc.get('personal_liability', '')))}".rstrip()
        )
        lines.append(
            f"Guest Medical: {clean_value(overrides.get('medical_payments', primary_doc.get('medical_payments', '')))}".rstrip()
        )
    else:
        coverage_lines = [
            ("Loss of Use:", overrides.get("loss_of_use", primary_doc.get("loss_of_use", ""))),
            ("Medical Payments to Others:", overrides.get("medical_payments", primary_doc.get("medical_payments", ""))),
            ("Personal Liability:", overrides.get("personal_liability", primary_doc.get("personal_liability", ""))),
        ]
        for label, value in coverage_lines:
            lines.append(f"{label} {clean_value(value)}".rstrip())
        personal_property = overrides.get("personal_property", primary_doc.get("personal_property", ""))
        building_property = overrides.get("building_property", primary_doc.get("building_property", ""))
        loss_assessment = overrides.get("loss_assessment", primary_doc.get("loss_assessment", ""))
        lines.append(f"Personal Property: {clean_value(personal_property)}".rstrip())
        lines.append(f"Building Property: {clean_value(building_property)}".rstrip())
        lines.append(f"Loss Assessment: {clean_value(loss_assessment)}".rstrip())

    lines.extend(
        [
            "",
            (f"1st Mortgagee: {overrides.get('mortgagee', primary_doc.get('mortgagee', ''))}".rstrip() if not is_renters else ""),
            (f"Loan Number: {overrides.get('loan_number', primary_doc.get('loan_number', ''))}".rstrip() if not is_renters else ""),
            "",
            "",
            f"Policy Premium: {overrides.get('policy_premium', primary_doc.get('policy_premium', ''))}".rstrip(),
        ]
    )

    return "\n".join(collapse_blank_lines([line.rstrip() for line in lines])).rstrip() + "\n"


def write_output(output_path: Path, rendered: str) -> None:
    try:
        output_path.write_text(rendered, encoding="utf-8")
        return
    except PermissionError:
        pass

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        suffix=".txt",
        dir=Path.cwd(),
    ) as handle:
        handle.write(rendered)
        temp_path = Path(handle.name)

    try:
        try:
            shutil.copyfile(temp_path, output_path)
            return
        except PermissionError:
            temp_literal = str(temp_path).replace("'", "''")
            output_literal = str(output_path).replace("'", "''")
            command = (
                "Copy-Item -LiteralPath "
                f"'{temp_literal}' "
                "-Destination "
                f"'{output_literal}' -Force"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                check=True,
            )
    finally:
        temp_path.unlink(missing_ok=True)


def build_fields(customer: str) -> dict[str, str]:
    home_paths, auto_paths, condo_paths = choose_policy_pdfs(customer)
    if not home_paths and not auto_paths:
        raise FileNotFoundError(f"No matching home or auto PDFs found for {customer!r}.")

    customer_first, customer_last = split_name(customer)
    fields: dict[str, str] = {
        "First name": customer_first,
        "Last Name": customer_last,
        "__has_home__": "yes" if bool(home_paths) else "",
        "__has_auto__": "yes" if bool(auto_paths) else "",
        "__has_second_insured__": "",
        "Date of birth": "",
        "Date of birth__2": "",
        "Social security number": "",
        "Social security number__2": "",
        "Drivers license #": "",
        "Drivers license #__2": "",
        "Phone number": "",
        "Discounts": "",
        "Discounts__2": "",
        "e-mail Address(es):__2": "",
    }

    text_by_path = {path: read_pdf_text(path) for path in home_paths + auto_paths}
    shared_order = home_paths + auto_paths
    for path in shared_order:
        text = text_by_path[path]
        policy_type = policy_type_for_path(path)
        extracted = extract_home_fields(text, customer) if policy_type == "home" else extract_auto_fields(text, customer)
        merge_shared_fields(fields, extracted)
        if extracted.get("Second named insured First name") or extracted.get("Second named insured Last Name"):
            fields["__has_second_insured__"] = "yes"

    home_docs = [extract_home_fields(text_by_path[path], customer) for path in home_paths]
    auto_docs = [extract_auto_fields(text_by_path[path], customer) for path in auto_paths]
    merge_nonempty_fields(fields, combine_home_fields(home_docs))
    merge_nonempty_fields(fields, combine_auto_fields(auto_docs))
    if home_paths:
        fields["e-mail Address(es):__2"] = OMIT_LINE
    return fields


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("customer", help="Customer first and last name")
    parser.add_argument(
        "--template",
        default=str(DEFAULT_TEMPLATE),
        help="Path to NotesTemplate.txt",
    )
    parser.add_argument(
        "--output",
        help="Optional output path for the generated notes file",
    )
    args = parser.parse_args()

    template_path = Path(args.template)
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    output_path = Path(args.output) if args.output else build_output_path(args.customer)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    template_text = template_path.read_text(encoding="utf-8")
    home_paths, auto_paths, condo_paths = choose_policy_pdfs(args.customer)
    if condo_paths:
        existing_text = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        rendered = render_condo_notes(args.customer, condo_paths, existing_text=existing_text)
        if auto_paths:
            fields = build_fields(args.customer)
            auto_section = "\n".join(render_auto_section_lines(fields, include_marker=True)).rstrip()
            if auto_section:
                rendered = rendered.rstrip() + "\n\n" + auto_section + "\n"
    else:
        fields = build_fields(args.customer)
        if fields.get("__has_auto__") and not fields.get("__has_home__") and "///////////Auto/////////" not in template_text:
            rendered = render_auto_only_notes(fields)
        else:
            rendered = render_template(template_text, fields)
    write_output(output_path, rendered)

    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
