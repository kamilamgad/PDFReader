---
name: pdf-notes
description: Use when the user asks to "make notes for" a customer, create notes from insurance PDFs, read PDFs in Downloads, extract structured data from customer Home or Auto PDF files, or format notes to match the existing GM+Notes and LF+Notes style. Triggers include phrases like "make notes for", "notes for", "read the PDFs for", and "summarize the customer PDFs".
---

# PDF Notes

## Overview

This skill creates customer notes from PDFs stored in `%USERPROFILE%\Downloads`.
Use it when the user wants notes for a customer and the source PDFs follow the naming pattern `{FirstName}{LastName}Home.pdf`, `{FirstName}{LastName}Home2.pdf`, `{FirstName}{LastName}Auto.pdf`, `{FirstName}{LastName}Auto2.pdf`, `{FirstName}{LastName}Condo.pdf`, or `{FirstName}{LastName}Condo2.pdf`.
The current implementation fills `%USERPROFILE%\Downloads\NotesTemplate.txt` for Home/Auto notes and uses a condo-style summary renderer for Condo notes.

## Workflow

1. Interpret the user request `make notes for {customer}` as a request to find that customer's PDFs in `Downloads`, extract relevant policy and customer details, and write a notes text file using `NotesTemplate.txt`.
2. Search `%USERPROFILE%\Downloads` for matching PDFs.
   - Expected files are named with no separator between first and last name, plus `Home`, `Auto`, or `Condo`, with an optional trailing number for multiple policies.
   - Examples: `LauraFolloHome.pdf`, `LauraFolloAuto.pdf`, `GetachewMollaAuto2.pdf`, `SwatiSinghCondo2.pdf`
   - Prefer exact normalized matches first, then looser matches if needed.
3. Use `NotesTemplate.txt` in `Downloads` as the output shape for Home/Auto notes.
   - Any field wrapped in `{}` should be treated as a label-driven extraction target.
   - Shared customer fields should be filled once even when both Home and Auto PDFs exist.
   - Auto-only and Home-only customers should still generate a single notes file.
4. For Condo notes, follow the compact multi-property format shown by `SSinghNotes.txt`.
5. Use [scripts/generate_notes.py](scripts/generate_notes.py) for the current combined workflow.
   - By default it writes generated notes into `pdf-notes/generated/`.
   - If the user specifically wants the finished `.txt` copied into `Downloads`, do that as a separate shell step after generation.
6. If a field is not present in the PDF, keep the line and leave the value blank instead of guessing unless the condo renderer intentionally omits that line.

## Output Rules

- Write a new `.txt` file.
- Default script output goes to `pdf-notes/generated/`.
- If requested, copy the final `.txt` to `Downloads` after generation.
- Follow `NotesTemplate.txt` line order.
- Replace `{label}` placeholders with `label: extracted value`.
- Preserve the example note style only where it does not conflict with the template.
- When both Home and Auto documents exist, populate the Home and Auto sections separately without repeating shared customer info.
- When Condo documents exist, render the condo summary format instead of the Home/Auto template.

## Resources

### Script

Use [scripts/find_matching_pdfs.py](scripts/find_matching_pdfs.py) to locate candidate files in `Downloads` for a customer name before reading PDFs manually.
Use [scripts/generate_notes.py](scripts/generate_notes.py) to generate Home, Auto, or Condo notes.

### Reference

Use [references/note-format.md](references/note-format.md) for the generalized structure derived from `GM+Notes.txt` and `LF+Notes.txt`.

## Known Local References

These existing note examples define the target style when they exist in the current user's profile:

- `%USERPROFILE%\OneDrive\Documents\ALL Dec Pages\GM+Notes.txt`
- `%USERPROFILE%\OneDrive\Documents\ALL Dec Pages\LF+Notes.txt`

If the user later updates those examples, re-check them before changing the skill.

## Pending Customization

The current logic is based on the user's `NotesTemplate.txt`, the condo-style `SSinghNotes.txt` example, and known Farmers-style Home/Auto/Condo documents.
If the user changes the template layout or adds new carrier formats, update this skill and the generator together.
