# Demo

This folder provides a safe demo flow for `PDFReader` without any real customer PDFs.

## What It Includes

- `inputs/JordanParkerHome.txt`
- `inputs/JordanParkerAuto.txt`
- `run_demo.py`
- `examples/CustomerNotesExample.txt`
- `examples/notes-example.png`

The input files are fake text fixtures that mimic extracted policy content closely enough to demonstrate the notes-generation workflow.

All example inputs and outputs in this folder are sanitized for public review and do not contain live customer data.

## Run The Demo

From the repo root:

```bash
python demo/run_demo.py
```

That writes:

```text
demo/output/JordanParkerNotes.txt
```

This is meant to show the shape of the generated notes and make the project easier for outside reviewers to evaluate without needing real PDFs or internal templates.
