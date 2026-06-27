# Visual QA Checklist

Use this checklist on real PDF documents before considering a build ready for practical use.

## Run

```bash
./.venv/bin/python run.py
```

Open:

```text
http://127.0.0.1:8000/
```

## Required Real-Document Checks

1. Upload 3-5 real PDF documents in one batch.
2. Run `Анализировать все`.
3. For every document, open `Preview`.
4. Confirm document rows show clear statuses: `ready`, `needs_review`, or `failed`.
5. Confirm warnings are visible for uncertain documents.
6. Confirm each candidate shows `anchor`, `reason`, `confidence`, and warnings.
7. Confirm signature overlay is near the correct line.
8. Confirm stamp overlay is about 35-45 mm and partly overlaps the signature.
9. Confirm stamp does not fully cover `Венедиктов Р.В.` or important printed text.
10. Confirm `Венедиктов Р.В.` is added when the signer name is missing.
11. Confirm `Венедиктов Р.В.` is not duplicated when already present.
12. Toggle signature off for one placement and verify it disappears from preview/export.
13. Toggle stamp off for one placement and verify it disappears from preview/export.
14. Drag signature and stamp, resize them, then click `Подтвердить и сохранить`.
15. Export one PDF and open it in Preview/Adobe Reader.
16. Export all documents and verify ZIP contains only expected output PDFs.
17. Verify original uploaded PDFs are unchanged.
18. Test a scanned PDF and verify OCR warnings or `needs_review` when OCR is weak.
19. Test a document where `Венедиктов Р.В.` appears only in body text; it must not be silently signed.
20. Test a document with `Генеральный директор` only; it should propose a placement or require review.

## Pass Criteria

- No unconfirmed document exports when manual confirmation is enabled.
- Exported PDF visually matches preview placement.
- Signature and stamp preserve transparent backgrounds.
- Uncertain cases are visible as `needs_review`, not silently exported.
- Batch export continues when one document is invalid or skipped.
