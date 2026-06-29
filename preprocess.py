#!/usr/bin/env python3
"""Render PDF pages to PNG images for the annotation UI.

Self-contained: depends only on PyMuPDF (``fitz``).

Drop PDFs into ``CalloutExtraction/pdfs/`` (the input folder). The expected
layout mirrors the project's data directory::

    CalloutExtraction/pdfs/
        requests/<request_id>/pdfs/<file>.pdf

The ``request_id`` is the folder that contains the ``pdfs/`` subfolder. PDFs
placed directly in a subfolder (no ``pdfs/`` level) use that subfolder name as
the request id, and PDFs sitting at the top level are grouped under ``_root``.

For each PDF this writes::

    CalloutExtraction/data/<request_id>__<pdf_stem>/
        meta.json            # request_id, source path/stat, page sizes
        pages/page_1.png ...

Processing is idempotent: a PDF is skipped on subsequent runs unless its file
changed (mtime/size) or the render DPI differs. An existing ``annotations.json``
is never touched.

Usage::

    python CalloutExtraction/preprocess.py            # auto: process pdfs/
    python CalloutExtraction/preprocess.py --force     # re-render everything
    python CalloutExtraction/preprocess.py a.pdf b.pdf # explicit files
    python CalloutExtraction/preprocess.py --dir DIR   # a specific folder
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

_ROOT = Path(__file__).resolve().parent
_DATA_DIR = _ROOT / "data"
_INPUT_DIR = _ROOT / "pdfs"

ROOT_REQUEST = "_root"


def _slugify(name: str) -> str:
    """Turn a name into a filesystem- and URL-safe token."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    return slug or "x"


def derive_request_id(pdf_path: Path, input_dir: Path) -> str:
    """Infer the request id for ``pdf_path`` under ``input_dir``.

    Rule: the request id is the directory that directly contains a ``pdfs/``
    folder (matching ``requests/<id>/pdfs/<file>.pdf``). Failing that, the
    PDF's top-level subfolder under ``input_dir`` is used, and PDFs at the top
    level fall back to ``_root``.
    """
    try:
        rel_parts = pdf_path.resolve().relative_to(input_dir.resolve()).parts
    except ValueError:
        # Outside the input dir (explicit file / --dir): use parent-of-"pdfs"
        # when present, else the immediate parent folder name.
        parts = pdf_path.resolve().parts
        if "pdfs" in parts:
            idx = len(parts) - 1 - parts[::-1].index("pdfs")
            if idx > 0:
                return parts[idx - 1]
        return pdf_path.resolve().parent.name or ROOT_REQUEST

    dirs = rel_parts[:-1]  # drop the filename
    if "pdfs" in dirs:
        idx = dirs.index("pdfs")
        if idx > 0:
            return dirs[idx - 1]
    if dirs:
        return dirs[0]
    return ROOT_REQUEST


def data_id(request_id: str, pdf_stem: str) -> str:
    """Deterministic data-dir id, stable across runs for the same PDF."""
    return f"{_slugify(request_id)}__{_slugify(pdf_stem)}"


def _is_up_to_date(pdf_path: Path, pdf_id: str, dpi: int) -> bool:
    meta_path = _DATA_DIR / pdf_id / "meta.json"
    if not meta_path.is_file():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    stat = pdf_path.stat()
    if meta.get("dpi") != dpi:
        return False
    if meta.get("source_size") != stat.st_size:
        return False
    if meta.get("source_mtime") != stat.st_mtime:
        return False
    pages_dir = _DATA_DIR / pdf_id / "pages"
    for page in meta.get("pages", []):
        if not (pages_dir / page.get("image", "")).is_file():
            return False
    return True


def render_pdf(pdf_path: Path, pdf_id: str, request_id: str, dpi: int) -> dict:
    """Render every page of ``pdf_path`` and write meta.json. Returns meta."""
    out_dir = _DATA_DIR / pdf_id
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    pages: list[dict] = []
    with fitz.open(str(pdf_path)) as doc:
        page_count = doc.page_count
        for index in range(page_count):
            page_num = index + 1
            pixmap = doc[index].get_pixmap(matrix=matrix)
            image_name = f"page_{page_num}.png"
            pixmap.save(str(pages_dir / image_name))
            pages.append(
                {
                    "number": page_num,
                    "image": image_name,
                    "width": pixmap.width,
                    "height": pixmap.height,
                }
            )

    stat = pdf_path.stat()
    meta = {
        "pdf_id": pdf_id,
        "request_id": request_id,
        "pdf_path": str(pdf_path),
        "title": pdf_path.name,
        "dpi": dpi,
        "source_size": stat.st_size,
        "source_mtime": stat.st_mtime,
        "page_count": page_count,
        "pages": pages,
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return meta


def process_pdf(
    pdf_path: Path,
    request_id: str,
    dpi: int = 150,
    force: bool = False,
) -> dict:
    """Render ``pdf_path`` if needed. Returns ``{status, pdf_id, ...}``."""
    pdf_id = data_id(request_id, pdf_path.stem)
    if not force and _is_up_to_date(pdf_path, pdf_id, dpi):
        return {"status": "skipped", "pdf_id": pdf_id, "request_id": request_id}
    meta = render_pdf(pdf_path, pdf_id, request_id, dpi)
    return {
        "status": "rendered",
        "pdf_id": pdf_id,
        "request_id": request_id,
        "page_count": meta["page_count"],
    }


def discover_input_pdfs(input_dir: Path) -> list[tuple[Path, str]]:
    """Return ``(pdf_path, request_id)`` for every PDF under ``input_dir``."""
    if not input_dir.is_dir():
        return []
    pdfs = sorted(input_dir.rglob("*.pdf"))
    return [(p, derive_request_id(p, input_dir)) for p in pdfs]


def auto_preprocess(
    input_dir: Path = _INPUT_DIR,
    dpi: int = 150,
    force: bool = False,
    log=print,
) -> dict:
    """Process all PDFs under ``input_dir``. Safe to call on every startup."""
    input_dir.mkdir(parents=True, exist_ok=True)
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    found = discover_input_pdfs(input_dir)
    rendered = skipped = 0
    if not found:
        log(f"No PDFs found in {input_dir}. Drop PDFs there and restart.")
        return {"found": 0, "rendered": 0, "skipped": 0}
    for pdf_path, request_id in found:
        result = process_pdf(pdf_path, request_id, dpi=dpi, force=force)
        if result["status"] == "rendered":
            rendered += 1
            log(
                f"  rendered [{request_id}] {pdf_path.name} "
                f"({result['page_count']} page(s)) -> {result['pdf_id']}"
            )
        else:
            skipped += 1
    log(
        f"Preprocess: {len(found)} PDF(s) found, "
        f"{rendered} rendered, {skipped} up-to-date."
    )
    return {"found": len(found), "rendered": rendered, "skipped": skipped}


def _explicit_pdfs(args: argparse.Namespace) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    if args.dir:
        root = Path(args.dir).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"directory not found: {root}")
        pdfs = sorted(root.rglob("*.pdf"))
        if not pdfs:
            raise FileNotFoundError(f"no *.pdf files found under {root}")
        return [(p, derive_request_id(p, root)) for p in pdfs]
    for raw in args.pdf_paths:
        p = Path(raw).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"PDF not found: {p}")
        out.append((p, derive_request_id(p, p.parent)))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render PDF pages to PNGs for the annotation UI.",
    )
    parser.add_argument(
        "pdf_paths",
        nargs="*",
        default=[],
        help="Explicit PDF files (default: auto-process the pdfs/ folder)",
    )
    parser.add_argument(
        "--dir",
        metavar="DIRECTORY",
        help="Render every *.pdf found under this directory (recursive)",
    )
    parser.add_argument("--dpi", type=int, default=150, help="Render DPI (default 150)")
    parser.add_argument(
        "--force", action="store_true", help="Re-render even if already up-to-date"
    )
    args = parser.parse_args()

    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    # No explicit targets -> auto-process the standard input folder.
    if not args.pdf_paths and not args.dir:
        auto_preprocess(_INPUT_DIR, dpi=args.dpi, force=args.force)
        print(f"\nStart the server with:\n    python {_ROOT.name}/app.py")
        return 0

    try:
        targets = _explicit_pdfs(args)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for i, (pdf_path, request_id) in enumerate(targets, 1):
        result = process_pdf(pdf_path, request_id, dpi=args.dpi, force=args.force)
        print(
            f"[{i}/{len(targets)}] [{request_id}] {pdf_path.name} -> "
            f"{result['status']} ({result['pdf_id']})"
        )

    print(f"\nStart the server with:\n    python {_ROOT.name}/app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
