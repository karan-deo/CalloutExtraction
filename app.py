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
import logging
import os
import time
from pathlib import Path

from flask import Flask, abort, g, jsonify, request, send_file, send_from_directory

import preprocess
from logging_config import setup_logging

logger = logging.getLogger("app")

_ROOT = Path(__file__).resolve().parent
_DATA_DIR = _ROOT / "data"
_STATIC_DIR = _ROOT / "static"
_INPUT_DIR = _ROOT / "pdfs"

app = Flask(__name__, static_folder=None)


@app.before_request
def _log_request_start() -> None:
    g._start_time = time.monotonic()
    logger.debug("--> %s %s", request.method, request.path)


@app.after_request
def _log_request_end(response):
    start = getattr(g, "_start_time", None)
    duration_ms = (time.monotonic() - start) * 1000 if start is not None else -1
    logger.info(
        "%s %s -> %s (%.1f ms)",
        request.method,
        request.path,
        response.status_code,
        duration_ms,
    )
    return response


def _pdf_dir(pdf_id: str) -> Path:
    """Resolve a (nested) PDF data dir, rejecting anything that escapes ``data/``."""
    data_root = _DATA_DIR.resolve()
    target = (data_root / pdf_id).resolve()
    if not target.is_dir() or data_root not in target.parents:
        logger.warning("Rejected unknown/escaping pdf id: %s", pdf_id)
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
    logger.info("Rescan requested")
    try:
        result = preprocess.auto_preprocess(_INPUT_DIR)
    except Exception as exc:  # noqa: BLE001 - surface any render failure to the UI
        logger.exception("Rescan failed")
        abort(500, description=f"rescan failed: {exc}")
    logger.info("Rescan finished: %s", result)
    return jsonify(result)


@app.route("/api/pdfs")
def list_pdfs():
    if not _DATA_DIR.is_dir():
        return jsonify([])
    pdfs = []
    for req_dir in sorted(_DATA_DIR.iterdir()):
        if not req_dir.is_dir():
            continue
        for pdf_dir in sorted(req_dir.iterdir()):
            meta_path = pdf_dir / "meta.json"
            if not (pdf_dir.is_dir() and meta_path.is_file()):
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            # meta.json is already filtered to A/S pages at render time.
            page_count = meta.get("page_count", len(meta.get("pages", [])))
            if page_count == 0:
                continue  # no required (A/S) sheets -> hide the PDF entirely
            pdfs.append(
                {
                    "id": meta.get("pdf_id", f"{req_dir.name}/{pdf_dir.name}"),
                    "title": meta.get("title", pdf_dir.name),
                    "request_id": meta.get("request_id", req_dir.name),
                    "page_count": page_count,
                }
            )
    pdfs.sort(key=lambda p: (p["request_id"], p["title"]))
    logger.debug("Listing %d PDF(s)", len(pdfs))
    return jsonify(pdfs)


@app.route("/api/pdfs/<path:pdf_id>/meta")
def get_meta(pdf_id: str):
    meta_path = _pdf_dir(pdf_id) / "meta.json"
    if not meta_path.is_file():
        logger.warning("meta.json missing for %s", pdf_id)
        abort(404, description="meta.json missing")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return jsonify(meta)


@app.route("/api/pdfs/<path:pdf_id>/pages/<int:page_num>.png")
def get_page_image(pdf_id: str, page_num: int):
    image_path = _pdf_dir(pdf_id) / "pages" / f"page_{page_num}.png"
    if not image_path.is_file():
        logger.warning("Page image not found: %s page %d", pdf_id, page_num)
        abort(404, description="page image not found")
    return send_file(image_path, mimetype="image/png")


@app.route("/api/pdfs/<path:pdf_id>/annotations", methods=["GET"])
def get_annotations(pdf_id: str):
    ann_path = _pdf_dir(pdf_id) / "annotations.json"
    if not ann_path.is_file():
        return jsonify(_empty_annotations(pdf_id))
    return jsonify(json.loads(ann_path.read_text(encoding="utf-8")))


@app.route("/api/pdfs/<path:pdf_id>/annotations", methods=["PUT"])
def put_annotations(pdf_id: str):
    pdf_dir = _pdf_dir(pdf_id)
    payload = request.get_json(silent=True)
    error = _validate_annotations(payload)
    if error:
        logger.warning("Rejected annotations for %s: %s", pdf_id, error)
        abort(400, description=error)

    payload["pdf_id"] = pdf_id
    (pdf_dir / "annotations.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    count = len(payload["annotations"])
    logger.info("Saved %d annotation(s) for %s", count, pdf_id)
    return jsonify({"status": "ok", "annotation_count": count})


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
    setup_logging()
    # Auto-preprocess any PDFs dropped into pdfs/ before serving.
    logger.info("Scanning %s for PDFs...", _INPUT_DIR)
    preprocess.auto_preprocess(_INPUT_DIR)
    # Port 5000 is taken by AirPlay Receiver on macOS; override with PORT env.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    logger.info("Serving on http://%s:%s", host, port)
    app.run(host=host, port=port, debug=True, use_reloader=False)
