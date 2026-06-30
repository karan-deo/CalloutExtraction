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

## Install & setup (uv)

This project is managed with [uv](https://docs.astral.sh/uv/). uv handles the
Python toolchain, the virtual environment, and dependency resolution from the
committed `uv.lock`.

> Run all `uv` commands from the `CalloutExtraction/` directory (the one holding
> `pyproject.toml` and `uv.lock`).

**1. Install uv** (Windows, via [WinGet](https://docs.astral.sh/uv/getting-started/installation/#winget)):

```powershell
winget install --id=astral-sh.uv -e
```

On macOS / Linux use the standalone installer instead
(`curl -LsSf https://astral.sh/uv/install.sh | sh`). See the
[installation docs](https://docs.astral.sh/uv/getting-started/installation/)
for other options.

**2. Install a matching Python.** `pyproject.toml` requires Python `>=3.14`; uv
can fetch it for you:

```bash
uv python install 3.14
```

**3. Sync dependencies.** This creates a `.venv/` and installs the exact,
locked versions from `uv.lock`:

```bash
uv sync
```

**4. Run the app** without manually activating the venv:

```bash
uv run app.py        # open http://127.0.0.1:5000
```

(If you prefer, activate the venv first — `.venv\Scripts\activate` on Windows,
`source .venv/bin/activate` on macOS / Linux — then the plain
`python CalloutExtraction/app.py` command below works too.)

`uv.lock` pins exact dependency versions for reproducible installs. To change
dependencies, edit `pyproject.toml` and run `uv lock`, or use `uv add <pkg>` /
`uv remove <pkg>`.

### pip (fallback)

If you'd rather not use uv:

```bash
pip install -r requirements.txt
```

## 1. Drop PDFs into the input folder

Put your PDFs under `CalloutExtraction/pdfs/`, mirroring the project's data
layout:

```
CalloutExtraction/pdfs/requests/
    <request_id>/pdfs/<file>.pdf
```

The **request id** is the folder that contains the `pdfs/` subfolder. PDFs in
any other folder are grouped under that folder's name, and PDFs at the top level
are grouped under `_root`. Nested folders are scanned recursively, so you can
drop multiple requests in at once.

## 2. Run the UI

```bash
uv run app.py                  # from CalloutExtraction/ (recommended)
python CalloutExtraction/app.py  # or, inside an activated venv
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

| Tool                 | What it does                                                              |
| -------------------- | ------------------------------------------------------------------------- |
| **Select** (V)       | Click a box to select it; drag a box to move it; drag empty space to pan. |
| **Create** (R)       | Drag on the page to draw a rectangle in the **active layer**.             |
| **Delete** (D / Del) | Click a box to delete it (or delete the selected box).                    |
| **Copy** (C)         | Click a box to duplicate it (or duplicate the selected box).              |

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
      "bbox": { "left": 0.1, "top": 0.2, "right": 0.3, "bottom": 0.4 }
    }
  ]
}
```

Bounding boxes are stored **normalized** (0..1) relative to the page image, so
they are independent of render resolution.
