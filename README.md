# CalloutExtraction — PDF Annotation UI

A small, self-contained tool for drawing bounding-box annotations on PDF pages,
grouping them into named layers, and saving everything to JSON.

It depends on `flask` and `pymupdf`.

## What it does

- Preprocess one or more PDFs into per-page PNG images.
- Open a browser UI to draw rectangles on any page.
- Group annotations under user-named **layers** (e.g. 5 boxes on page 1 and
  6 on page 3 all under one layer). Layers span pages.
- **Create / Delete / Copy** tools, plus moving boxes and reassigning a box to a
  different layer.
- Save all layers + boxes to `data/<pdf>/annotations.json`.

## Install

```bash
pip install -r requirements.txt
```

## 1. Drop PDFs into the input folder

Put your PDFs under `CalloutExtraction/pdfs/`, mirroring the project's data
layout:

```
CalloutExtraction/pdfs/
    requests/<request_id>/pdfs/<file>.pdf
```

The **request id** is the folder that contains the `pdfs/` subfolder. PDFs in
any other folder are grouped under that folder's name, and PDFs at the top level
are grouped under `_root`. Nested folders are scanned recursively, so you can
drop multiple requests in at once.

## 2. Run the UI

```bash
python CalloutExtraction/app.py
# open http://127.0.0.1:5000
```

On startup the app **auto-preprocesses** everything in `pdfs/`: each PDF is
rendered once into `data/<request_id>__<pdf_stem>/` (pages + `meta.json`).
Unchanged PDFs are skipped on later starts (it re-renders only if the file
changed or you pass a new `--dpi`). An existing `annotations.json` is never
touched.

Pick a PDF from the dropdown — PDFs are grouped by request id.

### Manual preprocessing (optional)

You don't need this for normal use, but you can preprocess explicitly:

```bash
python CalloutExtraction/preprocess.py              # process pdfs/ now
python CalloutExtraction/preprocess.py --force      # re-render everything
python CalloutExtraction/preprocess.py a.pdf b.pdf  # specific files
python CalloutExtraction/preprocess.py --dir DIR    # a specific folder
```

## Using the tools

| Tool | What it does |
| --- | --- |
| **Select** (V) | Click a box to select it; drag a box to move it; drag empty space to pan. |
| **Create** (R) | Drag on the page to draw a rectangle in the **active layer**. |
| **Delete** (D / Del) | Click a box to delete it (or delete the selected box). |
| **Copy** (C) | Click a box to duplicate it (or duplicate the selected box). |

Other:

- **Layers panel**: type a name + **Add** to create a layer (it gets a color).
  The **Active layer** dropdown decides which layer new boxes join. Use **Hide/Show**
  to toggle a layer's visibility.
- **Selected annotation panel**: change the **Layer** dropdown to move a box from
  one layer/group to another.
- Scroll to zoom, drag to pan. **Save** (or Cmd/Ctrl+S) writes to `annotations.json`.

## Output JSON

`data/<pdf>/annotations.json`:

```json
{
  "pdf_id": "drawing",
  "layers": [{ "name": "Ducts", "color": "#ea580c" }],
  "annotations": [
    {
      "id": "…",
      "page": 3,
      "type": "rect",
      "layer": "Ducts",
      "bbox": { "left": 0.10, "top": 0.20, "right": 0.30, "bottom": 0.40 }
    }
  ]
}
```

Bounding boxes are stored **normalized** (0..1) relative to the page image, so
they are independent of render resolution.



Undo Redo
Delete Layer
AutoSave
