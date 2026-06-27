# Regression Checklist

Run before changing analysis, placement, export, or UI behavior.

## Automated

```bash
./.venv/bin/python -m pytest -q
```

Expected: all tests pass.

## Real Documents

Run QA on the current real-document sample:

```bash
./.venv/bin/python scripts/qa_real_documents.py /Users/rafailvv/Documents/Иннопрог
```

Review `REAL_DOCUMENT_QA_REPORT.md`.

## Must Not Regress

- Upload accepts multiple PDFs and isolates bad files.
- `/analyze` marks uncertain cases as `needs_review`.
- Long table borders must not produce confident auto placements.
- Duplicate anchors on the same line must produce one candidate, not multiple placements.
- `Венедиктов Р.В.` in body text without a signature line must not be auto-signed.
- `Генеральный директор` near a real signature line may create a placement.
- Stamp remains about 35-45 mm.
- Stamp partially overlaps signature but does not cover signer name.
- Manual confirmation blocks export until `Подтвердить и сохранить`.
- Exported PDF opens and source PDF hash remains unchanged.
- ZIP export contains all expected signed PDFs.
- OCR failures and low confidence produce warnings and `needs_review`.
