#!/usr/bin/env python3
"""Flask server for the PDF annotation UI.

Self-contained: serves the static frontend, the page images, and read/write
endpoints for each PDF's ``annotations.json``. On startup it auto-preprocesses
any PDFs dropped into ``CalloutExtraction/pdfs/`` (skipping unchanged ones).

Run::

    pip install -r CalloutExtraction/requirements.txt
    # drop PDFs into CalloutExtraction/pdfs/ (e.g. requests/<id>/pdfs/*.pdf)
    python CalloutExtraction/app.py        # open http://127.0.0.1:5000
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

import preprocess

_ROOT = Path(__file__).resolve().parent
_DATA_DIR = _ROOT / "data"
_STATIC_DIR = _ROOT / "static"
_INPUT_DIR = _ROOT / "pdfs"

app = Flask(__name__, static_folder=None)


def _pdf_dir(pdf_id: str) -> Path:
    """Resolve a PDF data dir, rejecting anything that escapes ``data/``."""
    target = (_DATA_DIR / pdf_id).resolve()
    if target.parent != _DATA_DIR.resolve() or not target.is_dir():
        abort(404, description=f"unknown pdf id: {pdf_id}")
    return target


def _empty_annotations(pdf_id: str) -> dict:
    return {"pdf_id": pdf_id, "layers": [], "annotations": []}


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(_STATIC_DIR, "index.html")


@app.route("/static/<path:filename>")
def static_files(filename: str):
    return send_from_directory(_STATIC_DIR, filename)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route("/api/rescan", methods=["POST"])
def rescan():
    try:
        result = preprocess.auto_preprocess(_INPUT_DIR)
    except Exception as exc:  # noqa: BLE001 - surface any render failure to the UI
        abort(500, description=f"rescan failed: {exc}")
    return jsonify(result)


@app.route("/api/pdfs")
def list_pdfs():
    if not _DATA_DIR.is_dir():
        return jsonify([])
    pdfs = []
    for entry in sorted(_DATA_DIR.iterdir()):
        meta_path = entry / "meta.json"
        if not (entry.is_dir() and meta_path.is_file()):
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        pdfs.append(
            {
                "id": meta.get("pdf_id", entry.name),
                "title": meta.get("title", entry.name),
                "request_id": meta.get("request_id", ""),
                "page_count": meta.get("page_count", 0),
            }
        )
    pdfs.sort(key=lambda p: (p["request_id"], p["title"]))
    return jsonify(pdfs)


@app.route("/api/pdfs/<pdf_id>/meta")
def get_meta(pdf_id: str):
    meta_path = _pdf_dir(pdf_id) / "meta.json"
    if not meta_path.is_file():
        abort(404, description="meta.json missing")
    return jsonify(json.loads(meta_path.read_text(encoding="utf-8")))


@app.route("/api/pdfs/<pdf_id>/pages/<int:page_num>.png")
def get_page_image(pdf_id: str, page_num: int):
    image_path = _pdf_dir(pdf_id) / "pages" / f"page_{page_num}.png"
    if not image_path.is_file():
        abort(404, description="page image not found")
    return send_file(image_path, mimetype="image/png")


@app.route("/api/pdfs/<pdf_id>/annotations", methods=["GET"])
def get_annotations(pdf_id: str):
    ann_path = _pdf_dir(pdf_id) / "annotations.json"
    if not ann_path.is_file():
        return jsonify(_empty_annotations(pdf_id))
    return jsonify(json.loads(ann_path.read_text(encoding="utf-8")))


@app.route("/api/pdfs/<pdf_id>/annotations", methods=["PUT"])
def put_annotations(pdf_id: str):
    pdf_dir = _pdf_dir(pdf_id)
    payload = request.get_json(silent=True)
    error = _validate_annotations(payload)
    if error:
        abort(400, description=error)

    payload["pdf_id"] = pdf_id
    (pdf_dir / "annotations.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return jsonify({"status": "ok", "annotation_count": len(payload["annotations"])})


def _validate_annotations(payload) -> str | None:
    """Return an error string if ``payload`` is not a valid annotations doc."""
    if not isinstance(payload, dict):
        return "body must be a JSON object"
    layers = payload.get("layers")
    annotations = payload.get("annotations")
    if not isinstance(layers, list):
        return "'layers' must be a list"
    if not isinstance(annotations, list):
        return "'annotations' must be a list"
    for layer in layers:
        if not isinstance(layer, dict) or not isinstance(layer.get("name"), str):
            return "each layer needs a string 'name'"
    for ann in annotations:
        if not isinstance(ann, dict):
            return "each annotation must be an object"
        if not isinstance(ann.get("page"), int):
            return "each annotation needs an integer 'page'"
        bbox = ann.get("bbox")
        if not isinstance(bbox, dict):
            return "each annotation needs a 'bbox' object"
        for key in ("left", "top", "right", "bottom"):
            if not isinstance(bbox.get(key), (int, float)):
                return f"bbox.{key} must be a number"
    return None


if __name__ == "__main__":
    # Auto-preprocess any PDFs dropped into pdfs/ before serving. Reloader is
    # disabled so this runs exactly once.
    print(f"Scanning {_INPUT_DIR} for PDFs...")
    preprocess.auto_preprocess(_INPUT_DIR)
    # Port 5000 is taken by AirPlay Receiver on macOS; override with PORT env.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    print(f"Serving on http://{host}:{port}")
    app.run(host=host, port=port, debug=True, use_reloader=False)
