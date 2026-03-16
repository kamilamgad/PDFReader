#!/usr/bin/env python3
"""
Generate compact Home/Auto customer notes directly into Downloads.

Usage:
    python generate_fast_notes.py "Nataliia Costlow"
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from generate_notes import (
    DOWNLOADS,
    DEFAULT_OUTPUT_SUFFIX,
    build_fields,
    choose_policy_pdfs,
    clean_value,
    collapse_blank_lines,
    combine_auto_fields,
    combine_home_fields,
    extract_auto_fields,
    extract_home_fields,
    extract_vehicle_level_row_values,
    read_pdf_text,
    render_auto_section_lines,
    render_condo_notes,
    search,
    sort_auto_docs,
    unique_preserve,
    write_output,
)


def compact_name(customer: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", customer)


def build_output_path(customer: str) -> Path:
    return DOWNLOADS / f"{compact_name(customer)}{DEFAULT_OUTPUT_SUFFIX}"


def first_nonempty(*values: str) -> str:
    for value in values:
        cleaned = clean_value(value)
        if cleaned:
            return cleaned
    return ""


def join_name(first: str, last: str) -> str:
    return " ".join(part for part in (clean_value(first), clean_value(last)) if part).strip()


def search_first(text: str, *patterns: str) -> str:
    for pattern in patterns:
        value = search(text, pattern)
        if value:
            return value
    return ""


def parse_auto_vehicles_detailed(text: str) -> list[dict[str, str]]:
    block = search(
        text,
        r"Vehicle Information\s*Veh\. # Year/Make/Model/VIN Limit Coverage Deductible(.*?)Vehicle Level Coverage Items",
    )
    if not block:
        return []

    pattern = re.compile(
        r"(\d+)\s+"
        r"(.*?)\s+"
        r"(?:Comprehensive|Other than Collision):\s*(Not Covered|\$[0-9,]+)\s+"
        r"([A-HJ-NPR-Z0-9]{17})\s+"
        r"Collision:\s*(Not Covered|\$[0-9,]+)"
        r"(?:\s+Uninsured Motorist Property Damage:\s*(\$[0-9,]+)(?:\s+\$[0-9,]+ each accident)?)?"
        r"(?:\s+Rental Reimbursement:\s*(\$[0-9,]+(?:\s+per\s+day)?\s*/\s*\$[0-9,]+\s*max))?"
        r"(?:\s+\$[0-9,]+ each accident)?"
        r"(?=\s*(?:\.\s*)?\d+\s+[0-9]{4}|\s*$)",
        re.IGNORECASE | re.DOTALL,
    )

    glass_buyback_values = extract_vehicle_level_row_values(
        text,
        "Glass Deductible Buyback",
        "Policy Level Coverage Items",
    )
    glass_reductions = re.findall(
        r"Vehicle\s*(\d+)\s*-\s*Deductible reduced to\s*(\$[0-9,]+)\s*for glass loss",
        text,
        re.IGNORECASE,
    )
    glass_by_vehicle = {int(number): amount for number, amount in glass_reductions}

    vehicles: list[dict[str, str]] = []
    for match in pattern.finditer(block):
        number = int(clean_value(match.group(1)))
        buyback = ""
        if number - 1 < len(glass_buyback_values):
            buyback = clean_value(glass_buyback_values[number - 1])
        vehicles.append(
            {
                "number": str(number),
                "description": clean_value(match.group(2)),
                "comp_deductible": clean_value(match.group(3)),
                "vin": clean_value(match.group(4)),
                "collision_deductible": clean_value(match.group(5)),
                "umpd_deductible": clean_value(match.group(6) or ""),
                "rental_reimbursement": clean_value(match.group(7) or "").replace(" / ", ", "),
                "glass_buyback": buyback,
                "glass_reduction": clean_value(glass_by_vehicle.get(number, "")),
            }
        )
    return vehicles


def summarize_unique_line(values: list[str]) -> str:
    return clean_value(", ".join(unique_preserve(values)))


def summarize_single(values: list[str]) -> str:
    cleaned = [clean_value(value) for value in values if clean_value(value)]
    unique = unique_preserve(cleaned)
    return unique[0] if len(unique) == 1 else "\n".join(unique)


def extract_discount_names(text: str, names: list[str]) -> str:
    text = re.sub(r"\be\s+Policy\b", "ePolicy", text, flags=re.IGNORECASE)
    found_positions: list[tuple[int, str]] = []
    for name in names:
        match = re.search(re.escape(name), text, re.IGNORECASE)
        if match:
            found_positions.append((match.start(), name))
    found_positions.sort(key=lambda item: item[0])
    return ", ".join(unique_preserve([name for _, name in found_positions]))


def parse_household_drivers(text: str) -> str:
    block = search(
        text,
        r"Household Drivers\s*All persons.*?Name Driver Status Name Driver Status(.*?)Vehicle Information",
    )
    if not block:
        return ""
    raw_names = re.split(r"\b(?:Covered|Excluded|Not Rated)\b", block)
    names = []
    for raw_name in raw_names:
        cleaned = clean_value(raw_name)
        if cleaned and re.search(r"[A-Za-z]", cleaned):
            names.append(cleaned)
    return ", ".join(unique_preserve(names))


def render(customer: str) -> str:
    home_paths, auto_paths, condo_paths = choose_policy_pdfs(customer)
    if condo_paths:
        rendered = render_condo_notes(customer, condo_paths, existing_text="")
        if auto_paths:
            fields = build_fields(customer)
            auto_section = "\n".join(render_auto_section_lines(fields, include_marker=True)).rstrip()
            if auto_section:
                rendered = rendered.rstrip() + "\n\n" + auto_section + "\n"
        return rendered

    text_by_path = {path: read_pdf_text(path) for path in home_paths + auto_paths}
    fields = build_fields(customer)
    home_docs = [extract_home_fields(text_by_path[path], customer) for path in home_paths]
    auto_docs = [extract_auto_fields(text_by_path[path], customer) for path in auto_paths]

    primary_name = join_name(fields.get("First name", ""), fields.get("Last Name", ""))
    secondary_name = join_name(
        fields.get("Second named insured First name", ""),
        fields.get("Second named insured Last Name", ""),
    )
    mailing_address = first_nonempty(fields.get("Property Insured", ""))

    lines = [
        primary_name,
        "Date of birth:",
        "Social security number:",
        "Drivers license #:",
        "",
    ]

    if secondary_name:
        lines.extend(
            [
                secondary_name,
                "Date of birth:",
                "Social security number:",
                "Drivers license #:",
                "",
            ]
        )

    lines.append("Phone number:")
    lines.append(f"e-mail Address(es): {clean_value(fields.get('e-mail Address(es):', ''))}".rstrip())
    if mailing_address:
        lines.append(f"Mailing Address: {mailing_address}")

    if home_docs:
        combined_home = combine_home_fields(home_docs)
        home_text = "\n".join(text_by_path[path] for path in home_paths)
        summary_change = re.search(
            r"Summary of changes.*?Dwelling\s*(\$[0-9,]+)\s*(\$[0-9,]+)",
            home_text,
            re.IGNORECASE | re.DOTALL,
        )
        home_discounts = extract_discount_names(
            home_text,
            [
                "Claim Free",
                "ePolicy",
                "Central Burglar Alarm",
                "Preferred Payment Plan",
                "Homeownership",
                "Group - Educator",
                "Group - Scientist",
                "Auto/Home",
                "Non Smoker",
                "Central Fire Alarm",
                "New Roof",
            ],
        )
        lines.extend(
            [
                "",
                "Home Policy",
                f"Property Insured: {first_nonempty(combined_home.get('Property Insured', ''), mailing_address)}".rstrip(),
                f"Policy Number: {clean_value(combined_home.get('Policy Number:', combined_home.get('Home Policy Number:', '')))}".rstrip(),
                f"Effective: {clean_value(combined_home.get('Effective:', ''))}".rstrip(),
                f"Expiration: {search(home_text, r'Expiration:?\s*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4})')}".rstrip(),
                f"Year Built: {clean_value(combined_home.get('Year Built', ''))}".rstrip(),
                f"Age of Roof: {clean_value(combined_home.get('Age of Roof', ''))}".rstrip(),
                f"Square Footage: {clean_value(combined_home.get('Square Footage', ''))}".rstrip(),
                f"Style or Number of Stories: {clean_value(combined_home.get('Style or Number of Stories', ''))}".rstrip(),
                f"Dwelling Quality Grade: {clean_value(combined_home.get('Dwelling Quality Grade', ''))}".rstrip(),
                f"Foundation Type: {clean_value(combined_home.get('Foundation Type', ''))}".rstrip(),
                f"Foundation Shape: {clean_value(combined_home.get('Foundation Shape:', ''))}".rstrip(),
                f"Roof Material: {clean_value(combined_home.get('Roof Material:', ''))}".rstrip(),
                f"Garage Type: {clean_value(combined_home.get('Garage Type:', ''))}".rstrip(),
                f"Interior Wall Construction: {clean_value(combined_home.get('Interior Wall Construction', ''))}".rstrip(),
                f"Basement: {clean_value(combined_home.get('Basement:', ''))}".rstrip(),
                f"Roof Type: {clean_value(combined_home.get('Roof Type', ''))}".rstrip(),
                f"Roof Surface Material Type: {clean_value(combined_home.get('Roof Surface Material Type', ''))}".rstrip(),
                f"Construction Type: {clean_value(combined_home.get('Construction Type', ''))}".rstrip(),
                "",
                f"Deductible: {first_nonempty(clean_value(combined_home.get('Deductible', '')) + ' all perils' if clean_value(combined_home.get('Deductible', '')) else '', clean_value(combined_home.get('Deductible', '')))}".rstrip(),
                f"Declining Deductible Balance: {search(home_text, r'You have accumulated\s*(\$[0-9,]+)\s*of Declining Deductibles')}".rstrip(),
                f"Current Coverage A (Dwelling) Amount: {clean_value(summary_change.group(1) if summary_change else combined_home.get('Current Coverage A (Dwelling) Amount with Reconstruction Cost Factor:', ''))}".rstrip(),
                f"Coverage A (Dwelling) Amount offered for this renewal: {clean_value(summary_change.group(2) if summary_change else combined_home.get('Coverage A (Dwelling) Amount offered for this renewal:', ''))}".rstrip(),
                "",
                f"Coverage A - Dwelling: {clean_value(combined_home.get('Coverage A - Dwelling', ''))}".rstrip(),
                f"Extended Replacement Cost: {first_nonempty(combined_home.get('Extended Replacement Cost %', ''), 'Not Covered')}".rstrip(),
                f"Coverage B - Separate Structures: {clean_value(combined_home.get('Coverage B - Separate Structures', ''))}".rstrip(),
                f"Coverage C - Personal Property: {clean_value(combined_home.get('Coverage C - Personal Property', ''))}".rstrip(),
                f"Coverage D - Loss of Use: {clean_value(combined_home.get('Coverage D - Loss of Use', ''))}".rstrip(),
                f"Coverage E - Personal Liability: {clean_value(combined_home.get('Coverage E - Personal Liability', ''))}".rstrip(),
                f"Coverage F - Medical Payments to Others: {clean_value(combined_home.get('Coverage F - Medical Payments to Others', ''))}".rstrip(),
                f"Association Loss Assessment: {search(home_text, r'Association Loss Assessment\s*(\$[0-9,]+)')}".rstrip(),
                f"Building Ordinance or Law: {clean_value(combined_home.get('Building Ordinance or Law', ''))}".rstrip(),
                f"Sewer & Drain Damage - Extended Contents: {clean_value(combined_home.get('Sewer & Drain Damage', ''))}".rstrip(),
                f"Limited Matching Coverage for Siding and Roof Materials: {clean_value(combined_home.get('Limited Matching Coverage for Siding and Roof Materials', ''))}".rstrip(),
                f"Contents Replacement Coverage: {search_first(home_text, r'Contents Replacement Coverage.*?(Covered|Not Covered)', r'Contents Replacement Cost.*?(Covered|Not Covered)')}".rstrip(),
                f"Personal Injury: {search(home_text, r'Coverage E - Personal Liability\s*Personal Injury\s*\$[0-9,]+\s*(Covered|Not Covered)')}".rstrip(),
                f"Identity Fraud Expense Coverage: {search(home_text, r'Identity Fraud Expense Coverage\s*(Covered|Not Covered)')}".rstrip(),
                "",
                f"Roof Materials Loss Settlement: {clean_value(combined_home.get('Roof Materials', ''))}".rstrip(),
                f"Wall-to-Wall Carpet: {clean_value(combined_home.get('Wall-to-Wall Carpet', ''))}".rstrip(),
                f"Fence: {clean_value(combined_home.get('Fence', ''))}".rstrip(),
                f"Rest of Dwelling: {clean_value(combined_home.get('Rest of Dwelling', ''))}".rstrip(),
                f"Personal Property Contents: {clean_value(combined_home.get('Personal Property Contents (Pays up to the limit for Coverage C)', ''))}".rstrip(),
                "",
                f"Discounts: {home_discounts or clean_value(combined_home.get('Discounts', ''))}".rstrip(),
                "",
                f"Mortgagee: {clean_value(combined_home.get('1st Mortgagee', ''))}".rstrip(),
                f"Loan Number: {clean_value(combined_home.get('Loan Number', ''))}".rstrip(),
                "",
                f"Renewal Premium: {clean_value(combined_home.get('Policy Premium', ''))}".rstrip(),
                f"Prior Term Premium: {search(home_text, r'Your premium at the beginning of the current term was\s*(\$[0-9,]+(?:\.\d{2})?)')}".rstrip(),
            ]
        )

    if auto_docs:
        combined_auto = combine_auto_fields(auto_docs)
        auto_texts = [text_by_path[path] for path in auto_paths]
        vehicle_details: list[dict[str, str]] = []
        for text in auto_texts:
            vehicle_details.extend(parse_auto_vehicles_detailed(text))
        auto_discount_text = extract_discount_names(
            "\n".join(auto_texts),
            [
                "Homeownership",
                "Multiple Car",
                "New Business",
                "Five Year Accident Free",
                "Group - Scientist",
                "ePolicy",
                "EFT",
                "Auto/Home",
                "3 Year Clean-Renewal",
                "Safe Driver",
            ],
        )

        lines.extend(
            [
                "",
                "Auto Policy",
                f"Policy Number: {clean_value(combined_auto.get('Auto Policy Number', ''))}".rstrip(),
                f"Effective: {clean_value(combined_auto.get('Auto Effective', ''))}".rstrip(),
                f"Expiration: {clean_value(combined_auto.get('Auto Expiration', ''))}".rstrip(),
                f"Policy Premium: {clean_value(combined_auto.get('Auto policy premium', ''))}".rstrip(),
                f"Named Insured(s): {', '.join([name for name in [join_name(fields.get('First name', ''), fields.get('Last Name', '')), join_name(fields.get('Second named insured First name', ''), fields.get('Second named insured Last Name', ''))] if name])}".rstrip(),
                "",
                f"Household Drivers: {summarize_single([parse_household_drivers(text) for text in auto_texts])}".rstrip(),
                "",
            ]
        )

        for index, vehicle in enumerate(vehicle_details, start=1):
            lines.append(f"Vehicle {index}: {clean_value(vehicle.get('description', ''))}".rstrip())
            lines.append(f"VIN: {clean_value(vehicle.get('vin', ''))}".rstrip())
            lines.append(f"Comprehensive Deductible: {clean_value(vehicle.get('comp_deductible', ''))}".rstrip())
            lines.append(f"Collision Deductible: {clean_value(vehicle.get('collision_deductible', ''))}".rstrip())
            if clean_value(vehicle.get("umpd_deductible", "")):
                lines.append(f"Uninsured Motorist Property Damage Deductible: {clean_value(vehicle.get('umpd_deductible', ''))}")
            if clean_value(vehicle.get("rental_reimbursement", "")):
                lines.append(f"Rental Reimbursement: {clean_value(vehicle.get('rental_reimbursement', ''))}")
            if clean_value(vehicle.get("glass_buyback", "")) and clean_value(vehicle.get("glass_buyback", "")) != "Not Covered":
                lines.append("Glass Deductible Buyback: Covered")
            if clean_value(vehicle.get("glass_reduction", "")):
                lines.append(f"Glass loss deductible on Vehicle {index} reduced to: {clean_value(vehicle.get('glass_reduction', ''))}")
            lines.append("")

        lines.extend(
            [
                "Auto Liability / Core Coverages",
                f"Bodily Injury Liability: {summarize_single([search(text, r'Bodily Injury Liability\s+(\$[0-9,]+ each person\s+\$[0-9,]+ each accident)').replace(' $', ' / $') for text in auto_texts])}".rstrip(),
                f"Property Damage Liability: {summarize_single([search(text, r'Property Damage Liability\s+(\$[0-9,]+ each accident)') for text in auto_texts])}".rstrip(),
                f"Personal Injury Protection: {summarize_single([search(text, r'Personal Injury Protection\s+(\$[0-9,]+ each person)') for text in auto_texts])}".rstrip(),
                f"Uninsured Motorist: {summarize_single([search(text, r'Uninsured Motorist\s+(\$[0-9,]+ each person\s+\$[0-9,]+ each accident)').replace(' $', ' / $') for text in auto_texts])}".rstrip(),
                f"Uninsured Motorist Property Damage: {summarize_single([search(text, r'Uninsured Motorist Property Damage(?: \(Alternative Coverage\))?\s+(\$[0-9,]+ each accident)') for text in auto_texts])}".rstrip(),
                f"Towing and Road Service: {'Covered on selected vehicles' if clean_value(combined_auto.get('Towing and Labor Costs', '')) else ''}".rstrip(),
                "",
                f"Auto Discounts: {auto_discount_text or clean_value(combined_auto.get('Auto Discounts', ''))}".rstrip(),
            ]
        )

    result = [re.sub(r"\be Policy\b", "ePolicy", re.sub(r",\s+", ", ", line)) for line in lines if line != "Towing and Road Service:"]
    return "\n".join(collapse_blank_lines(result)).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("customer", help="Customer first and last name")
    parser.add_argument("--output", help="Optional output path for the generated notes file")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else build_output_path(args.customer)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = render(args.customer)
    write_output(output_path, rendered)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
