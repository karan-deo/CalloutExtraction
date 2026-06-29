# Drop PDFs here

Place PDFs in this folder; they are auto-preprocessed when you start the app
(`python CalloutExtraction/app.py`).

Recommended layout (mirrors the project data directory):

```
pdfs/
    requests/<request_id>/pdfs/<file>.pdf
```

- The **request id** is the folder that contains the `pdfs/` subfolder.
- PDFs in any other subfolder are grouped under that subfolder's name.
- PDFs placed directly here (no subfolder) are grouped under `_root`.

Subfolders are scanned recursively, so you can drop multiple requests at once.
Already-rendered PDFs are skipped on later starts unless the file changes.
