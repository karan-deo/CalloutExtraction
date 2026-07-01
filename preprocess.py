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

Only Architectural (A*) and Structural (S*) sheets are rendered: sheet names
are resolved before rasterisation and non-A/S pages are skipped, so the output
already matches the UI's required-sheet filter (see ``discipline.py``). Rendered
page ``number`` values stay tied to the real PDF pages, so image URLs and stored
annotations keep referring to the original pages.

For each PDF this writes::

    CalloutExtraction/data/<request_id>__<pdf_stem>/
        meta.json            # request_id, source path/stat, page sizes (A/S only)
        pages/page_1.png ...  # only the rendered A*/S* pages

Processing is idempotent: a PDF is skipped on subsequent runs unless its file
changed (mtime/size) or the render DPI differs. An existing ``annotations.json``
is never touched. PDFs rendered before the A/S filter existed are pruned in
place on the next run (``_reconcile_meta``).

Usage::

    python CalloutExtraction/preprocess.py            # auto: process pdfs/
    python CalloutExtraction/preprocess.py --force     # re-render everything
    python CalloutExtraction/preprocess.py a.pdf b.pdf # explicit files
    python CalloutExtraction/preprocess.py --dir DIR   # a specific folder
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import re
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import fitz  # PyMuPDF

import discipline
from logging_config import setup_logging

logger = logging.getLogger("preprocess")

# Some source PDFs (notably incremental-save CAD exports) contain content
# streams that reference XObject resources absent from the page resource dict.
# MuPDF logs a non-fatal "cannot find XObject resource" error per reference,
# flooding stderr during rasterisation even though the page still renders
# correctly. Genuinely fatal problems still raise and are caught per-PDF, so we
# only silence the unactionable error display. This runs in every process,
# including spawned render workers, because the module is re-imported there.
fitz.TOOLS.mupdf_display_errors(False)

_ROOT = Path(__file__).resolve().parent
_DATA_DIR = _ROOT / "output_data"
_INPUT_DIR = _ROOT / "input_data"

ROOT_REQUEST = "_root"

# Upper bound on auto-selected worker processes. Each high-DPI pixmap can hold
# ~10-25 MB, so the default fan-out is capped to avoid memory pressure on
# many-core machines. An explicit ``--workers`` value overrides this ceiling.
_MAX_AUTO_WORKERS = max(1, (int(os.cpu_count()) - 1) or 1)

# Per-process cache holding the most recently opened document, so a worker that
# renders several consecutive pages of the same PDF avoids re-opening it. Lives
# in each worker process and is never shared across processes.
_WORKER_DOC_CACHE: dict[str, fitz.Document] = {}


def _slugify(name: str) -> str:
    """Turn a name into a filesystem and URL safe token."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    return slug or "x"


def _normalize(name: str) -> str:
    """Normalise a file/sheet name for matching.

    Drops a trailing ``.pdf`` and a trailing ``_<timestamp>`` (the upload epoch
    suffix that appears inconsistently across requests), and lowercases. This
    lets a dropped PDF stem match either a blueprint ``name`` or the basename of
    its ``file_url``.
    """
    stem = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)
    stem = re.sub(r"_\d{6,}$", "", stem)
    return stem.strip().lower()


def _request_dir(pdf_path: Path, request_id: str) -> Path | None:
    """Locate the ``requests/<id>/`` folder holding the metadata JSON files."""
    candidate = pdf_path.parent.parent  # requests/<id>/pdfs/<file>.pdf -> requests/<id>
    if (candidate / "blueprint_files.json").is_file():
        return candidate
    fallback = _INPUT_DIR / "requests" / request_id
    if (fallback / "blueprint_files.json").is_file():
        return fallback
    return None


def _sheet_name_map(pdf_path: Path, request_id: str) -> dict[int, str | None]:
    """Map 1-based page number -> sheet name for ``pdf_path``.

    Best-effort: matches the PDF to a blueprint file (via ``blueprint_files.json``)
    then reads ``worksheets_metadata.json`` for that file's sheets. The sheet
    name is the worksheet ``name`` when present, otherwise the blueprint file's
    ``name``. Returns ``{}`` when the metadata is missing or anything fails.
    """
    try:
        req_dir = _request_dir(pdf_path, request_id)
        if req_dir is None:
            return {}
        blueprints = json.loads(
            (req_dir / "blueprint_files.json").read_text(encoding="utf-8")
        )
        files_info = blueprints.get("files_info", [])
        if not files_info:
            return {}

        target_norm = _normalize(pdf_path.stem)
        matched = None
        if len(files_info) == 1:
            matched = files_info[0]
        else:
            for info in files_info:
                name_norm = _normalize(info.get("name", "") or "")
                url_norm = _normalize(Path(info.get("file_url", "") or "").name)
                if target_norm and target_norm in (name_norm, url_norm):
                    matched = info
                    break
        if matched is None:
            return {}

        blueprint_id = matched.get("id")
        blueprint_name = matched.get("name") or None

        worksheets_path = req_dir / "worksheets_metadata.json"
        if not worksheets_path.is_file():
            return {}
        worksheets = json.loads(worksheets_path.read_text(encoding="utf-8"))

        mapping: dict[int, str | None] = {}
        for sheet in worksheets:
            if sheet.get("blueprint_file_id") != blueprint_id:
                continue
            page_no = sheet.get("page_no")
            if not isinstance(page_no, int):
                continue
            name = sheet.get("name") or blueprint_name
            mapping[page_no] = name
        return mapping
    except (json.JSONDecodeError, OSError, KeyError, TypeError) as exc:
        logger.debug("Sheet-name resolution failed for %s: %s", pdf_path.name, exc)
        return {}


def _apply_sheet_names(meta: dict, mapping: dict[int, str | None]) -> None:
    """Attach ``sheet_name`` to each page and mark the meta as resolved."""
    for page in meta.get("pages", []):
        page["sheet_name"] = mapping.get(page.get("number"))
    meta["sheet_names_resolved"] = True


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
    """Deterministic nested data id ``"<request>/<pdf>"``, stable per PDF."""
    return f"{_slugify(request_id)}/{_slugify(pdf_stem)}"


def _migrate_flat_dirs() -> None:
    """Migrate legacy flat ``data/<request>__<pdf>/`` dirs to ``data/<request>/<pdf>/``.

    Idempotent and best-effort: a flat dir is recognised by a ``__`` in its name
    plus a ``meta.json`` directly inside (request ids/pdf slugs never contain
    ``__``). Annotations and rendered pages move with the folder; meta's
    ``pdf_id`` is rewritten to the new nested id. One failure does not abort the
    rest.
    """
    if not _DATA_DIR.is_dir():
        return
    for entry in sorted(_DATA_DIR.iterdir()):
        if not (entry.is_dir() and "__" in entry.name):
            continue
        if not (entry / "meta.json").is_file():
            continue
        request_slug, pdf_slug = entry.name.split("__", 1)
        target = _DATA_DIR / request_slug / pdf_slug
        if target.exists():
            logger.warning("Migration target exists, skipping %s", entry.name)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry), str(target))
            meta_path = target / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["pdf_id"] = f"{request_slug}/{pdf_slug}"
            meta_path.write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.info("Migrated %s -> %s/%s", entry.name, request_slug, pdf_slug)
        except (OSError, json.JSONDecodeError) as exc:  # noqa: BLE001
            logger.error("Migration failed for %s: %s", entry.name, exc)


def _is_up_to_date(pdf_path: Path, pdf_id: str, dpi: int) -> bool:
    meta_path = _DATA_DIR / pdf_id / "meta.json"
    if not meta_path.is_file():
        logger.debug("Not up-to-date [%s]: no meta.json", pdf_id)
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("Not up-to-date [%s]: unreadable meta.json (%s)", pdf_id, exc)
        return False
    stat = pdf_path.stat()
    if meta.get("dpi") != dpi:
        logger.debug(
            "Not up-to-date [%s]: dpi changed (%s -> %s)", pdf_id, meta.get("dpi"), dpi
        )
        return False
    if meta.get("source_size") != stat.st_size:
        logger.debug("Not up-to-date [%s]: source size changed", pdf_id)
        return False
    if meta.get("source_mtime") != stat.st_mtime:
        logger.debug("Not up-to-date [%s]: source mtime changed", pdf_id)
        return False
    pages_dir = _DATA_DIR / pdf_id / "pages"
    for page in meta.get("pages", []):
        if not (pages_dir / page.get("image", "")).is_file():
            logger.debug(
                "Not up-to-date [%s]: missing page image %s",
                pdf_id,
                page.get("image"),
            )
            return False
    return True


def _render_page_task(task: tuple[str, int, int, str]) -> tuple[str, dict]:
    """Render a single PDF page to PNG. Runs in a worker process.

    Opens (and caches per process) its own ``fitz.Document`` because PyMuPDF
    objects are not safe to share across processes. Returns the page metadata
    the parent needs to assemble ``meta.json``.
    """
    pdf_path, page_index, dpi, out_path = task
    doc = _WORKER_DOC_CACHE.get(pdf_path)
    if doc is None:
        for cached in _WORKER_DOC_CACHE.values():
            cached.close()
        _WORKER_DOC_CACHE.clear()
        doc = fitz.open(pdf_path)
        _WORKER_DOC_CACHE[pdf_path] = doc
    zoom = dpi / 72.0
    pixmap = doc[page_index].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    pixmap.save(out_path)
    return pdf_path, {
        "number": page_index + 1,
        "image": Path(out_path).name,
        "width": pixmap.width,
        "height": pixmap.height,
    }


def _build_meta(
    pdf_path: Path,
    pdf_id: str,
    request_id: str,
    dpi: int,
    pages: list[dict],
    name_map: dict[int, str | None],
) -> dict:
    """Assemble and write ``meta.json`` from already-rendered page metadata.

    ``pages`` only holds the rendered A*/S* pages, so the meta is already
    discipline-filtered; ``pages_pruned`` marks it so ``_reconcile_meta`` never
    re-prunes it.
    """
    out_dir = _DATA_DIR / pdf_id
    stat = pdf_path.stat()
    meta = {
        "pdf_id": pdf_id,
        "request_id": request_id,
        "pdf_path": str(pdf_path),
        "title": pdf_path.name,
        "dpi": dpi,
        "source_size": stat.st_size,
        "source_mtime": stat.st_mtime,
        "page_count": len(pages),
        "pages": pages,
        "pages_pruned": True,
    }
    _apply_sheet_names(meta, name_map)
    (out_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return meta


def _reconcile_meta(pdf_path: Path, pdf_id: str, request_id: str) -> None:
    """Bring an already-rendered ``meta.json`` in line with render-time filtering.

    Two one-time, idempotent fix-ups for PDFs rendered before the A*/S* filter
    existed (each guarded by a flag, so subsequent startups do nothing):

    1. Backfill ``sheet_name`` when unresolved (``sheet_names_resolved``).
    2. Prune non-A*/S* pages: delete their PNGs and drop them from ``meta``
       (``pages_pruned``).

    Cheap: no PNG re-render, only unlinks stale files and rewrites the JSON.
    """
    pdf_data_dir = _DATA_DIR / pdf_id
    meta_path = pdf_data_dir / "meta.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    changed = False
    if not meta.get("sheet_names_resolved"):
        _apply_sheet_names(meta, _sheet_name_map(pdf_path, request_id))
        changed = True

    if not meta.get("pages_pruned"):
        pages = meta.get("pages", [])
        kept = [p for p in pages if discipline.is_required(p.get("sheet_name"))]
        # Guard against destructive deletion when sheet names never resolved:
        # if nothing is kept AND no page has a name, resolution likely failed
        # (metadata missing), so leave the pages and retry on a later run.
        names_resolved = any(p.get("sheet_name") for p in pages)
        if kept or names_resolved:
            dropped = [
                p for p in pages if not discipline.is_required(p.get("sheet_name"))
            ]
            pages_dir = pdf_data_dir / "pages"
            for page in dropped:
                image = page.get("image")
                if image:
                    (pages_dir / image).unlink(missing_ok=True)
            meta["pages"] = kept
            meta["page_count"] = len(kept)
            meta["pages_pruned"] = True
            changed = True
            if dropped:
                logger.info(
                    "Pruned %d non-A/S page(s) from [%s] %s",
                    len(dropped),
                    request_id,
                    pdf_path.name,
                )

    if changed:
        meta_path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def _resolve_workers(workers: int | None) -> int:
    """Pick a worker count: an explicit value wins; else cap cpu_count at the ceiling."""
    if workers is not None:
        return max(1, workers)
    return min(os.cpu_count() or 1, _MAX_AUTO_WORKERS)


def _run_tasks(
    task_meta: list[tuple[tuple[str, int, int, str], str, int]],
    stubs: dict[str, dict],
    workers: int,
) -> None:
    """Execute render tasks, inline for trivial loads or across a spawn pool.

    Results are placed into ``stubs[pdf_id]["pages"][page_index]``. A worker
    failure marks only its PDF as failed (logged once) and never aborts the
    batch.
    """

    total = len(task_meta)
    # Emit an INFO progress line in ~5% steps (at least every 25 pages) so a long
    # batch shows steady movement without flooding the log with one line per page.
    step = max(1, min(total, max(25, total // 20)))
    started = time.monotonic()
    completed = 0

    def _record(pdf_id: str, page_index: int, page: dict) -> None:
        stubs[pdf_id]["pages"][page_index] = page

    def _fail(pdf_id: str, exc: Exception) -> None:
        stub = stubs[pdf_id]
        if not stub["failed"]:
            stub["failed"] = True
            logger.error(
                "Render failed [%s] %s: %s",
                stub["request_id"],
                stub["pdf_path"].name,
                exc,
            )

    def _tick(pdf_id: str, page_index: int) -> None:
        nonlocal completed
        completed += 1
        logger.debug("Rendered page %d/%d [%s] page %d", completed, total, pdf_id, page_index + 1)
        if completed == total or completed % step == 0:
            elapsed = time.monotonic() - started
            rate = completed / elapsed if elapsed else 0.0
            logger.info(
                "Progress: %d/%d pages (%.0f%%, %.1f pages/s)",
                completed,
                total,
                100.0 * completed / total,
                rate,
            )

    # Inline path avoids spawn overhead for the single-worker / single-file case.
    if workers == 1 or total == 1:
        logger.debug("Running %d render task(s) inline", total)
        for task, pdf_id, page_index in task_meta:
            try:
                _, page = _render_page_task(task)
                _record(pdf_id, page_index, page)
            except Exception as exc:  # noqa: BLE001 - isolate failure to this PDF
                _fail(pdf_id, exc)
            _tick(pdf_id, page_index)
        return

    # Explicit spawn context keeps behaviour identical on Windows/macOS/Linux
    # (Windows is spawn-only) and avoids fork-related instability in PyMuPDF.
    pool_workers = min(workers, total)
    logger.debug(
        "Running %d render task(s) across %d worker process(es)",
        total,
        pool_workers,
    )
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=pool_workers, mp_context=ctx) as pool:
        futures = {
            pool.submit(_render_page_task, task): (pdf_id, page_index)
            for task, pdf_id, page_index in task_meta
        }
        for fut in as_completed(futures):
            pdf_id, page_index = futures[fut]
            try:
                _, page = fut.result()
                _record(pdf_id, page_index, page)
            except Exception as exc:  # noqa: BLE001 - isolate failure to this PDF
                _fail(pdf_id, exc)
            _tick(pdf_id, page_index)


def render_targets(
    targets: list[tuple[Path, str]],
    dpi: int = 150,
    force: bool = False,
    workers: int | None = None,
) -> dict:
    """Render ``targets``, fanning page rasterisation out across processes.

    Skip-detection, ``meta.json`` assembly, and sheet-name resolution all run
    in this (parent) process; workers only rasterise one page each. A failed
    PDF is left unrendered (no ``meta.json``) so a later run retries it.
    """
    started = time.monotonic()
    rendered = skipped = failed = 0
    stubs: dict[str, dict] = {}
    task_meta: list[tuple[tuple[str, int, int, str], str, int]] = []

    for pdf_path, request_id in targets:
        pdf_id = data_id(request_id, pdf_path.stem)
        if not force and _is_up_to_date(pdf_path, pdf_id, dpi):
            _reconcile_meta(pdf_path, pdf_id, request_id)
            logger.debug("Skipping up-to-date [%s] %s", request_id, pdf_path.name)
            skipped += 1
            continue
        try:
            with fitz.open(str(pdf_path)) as doc:
                page_count = doc.page_count
        except Exception as exc:  # noqa: BLE001 - skip this PDF, keep the batch going
            logger.error("Failed to open [%s] %s: %s", request_id, pdf_path.name, exc)
            failed += 1
            continue
        # Resolve sheet names up front so only Architectural/Structural (A*/S*)
        # pages get rasterised; the rest are never rendered (saves time + disk).
        name_map = _sheet_name_map(pdf_path, request_id)
        kept = [i for i in range(page_count) if discipline.is_required(name_map.get(i + 1))]
        # Create the pages dir up front so workers never mkdir concurrently.
        pages_dir = _DATA_DIR / pdf_id / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        stubs[pdf_id] = {
            "request_id": request_id,
            "pdf_path": pdf_path,
            "page_count": len(kept),
            "pages": [None] * len(kept),
            "name_map": name_map,
            "failed": False,
        }
        logger.debug(
            "Queued [%s] %s (%d of %d page(s) are A/S) -> %s",
            request_id,
            pdf_path.name,
            len(kept),
            page_count,
            pdf_id,
        )
        # ``index`` is the true 0-based PDF page index (so image names and
        # page["number"] keep referring to real pages); ``slot`` is the position
        # in the filtered ``pages`` list where the result is recorded.
        for slot, index in enumerate(kept):
            out_path = str(pages_dir / f"page_{index + 1}.png")
            task_meta.append(((str(pdf_path), index, dpi, out_path), pdf_id, slot))

    if task_meta:
        resolved_workers = _resolve_workers(workers)
        logger.info(
            "Rendering %d page(s) across %d PDF(s) with %d worker(s) at %d DPI",
            len(task_meta),
            len(stubs),
            resolved_workers,
            dpi,
        )
        _run_tasks(task_meta, stubs, resolved_workers)

    for pdf_id, stub in stubs.items():
        if stub["failed"] or any(page is None for page in stub["pages"]):
            failed += 1
            continue
        _build_meta(
            stub["pdf_path"],
            pdf_id,
            stub["request_id"],
            dpi,
            stub["pages"],
            stub["name_map"],
        )
        rendered += 1
        logger.info(
            "Rendered [%s] %s (%d page(s)) -> %s",
            stub["request_id"],
            stub["pdf_path"].name,
            stub["page_count"],
            pdf_id,
        )

    logger.debug(
        "render_targets finished in %.2fs (%d rendered, %d skipped, %d failed)",
        time.monotonic() - started,
        rendered,
        skipped,
        failed,
    )
    return {
        "found": len(targets),
        "rendered": rendered,
        "skipped": skipped,
        "failed": failed,
    }


def discover_input_pdfs(input_dir: Path) -> list[tuple[Path, str]]:
    """Return ``(pdf_path, request_id)`` for every PDF under ``input_dir``."""
    if not input_dir.is_dir():
        return []
    # Each request duplicates its PDFs under both ``pdf_files/`` and ``pdfs/``;
    # render only the ``pdfs/`` copy to avoid processing every PDF twice.
    pdfs = sorted(p for p in input_dir.rglob("*.pdf") if "pdf_files" not in p.parts)
    targets = [(p, derive_request_id(p, input_dir)) for p in pdfs]
    for pdf_path, request_id in targets:
        logger.debug("Discovered [%s] %s", request_id, pdf_path)
    return targets


def auto_preprocess(
    input_dir: Path = _INPUT_DIR,
    dpi: int = 150,
    force: bool = False,
    workers: int | None = None,
) -> dict:
    """Process all PDFs under ``input_dir``. Safe to call on every startup."""
    started = time.monotonic()
    logger.info("Starting preprocess scan of %s (dpi=%d, force=%s)", input_dir, dpi, force)
    input_dir.mkdir(parents=True, exist_ok=True)
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _migrate_flat_dirs()
    found = discover_input_pdfs(input_dir)
    if not found:
        logger.warning("No PDFs found in %s. Drop PDFs there and restart.", input_dir)
        return {"found": 0, "rendered": 0, "skipped": 0, "failed": 0}
    logger.info("Found %d PDF(s) under %s", len(found), input_dir)
    result = render_targets(found, dpi=dpi, force=force, workers=workers)
    logger.info(
        "Preprocess complete in %.2fs: %d found, %d rendered, %d up-to-date, %d failed.",
        time.monotonic() - started,
        result["found"],
        result["rendered"],
        result["skipped"],
        result["failed"],
    )
    return result


def _explicit_pdfs(args: argparse.Namespace) -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    if args.dir:
        root = Path(args.dir).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"directory not found: {root}")
        pdfs = sorted(p for p in root.rglob("*.pdf") if "pdf_files" not in p.parts)
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
    # Required on Windows/frozen builds so re-imported worker processes do not
    # re-enter main(); a no-op for normal interpreter runs.
    multiprocessing.freeze_support()
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
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Worker processes for rendering (default: min(cpu_count, 8))",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose (INFO) logging; overrides LOG_LEVEL",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug-level logging; overrides LOG_LEVEL and --verbose",
    )
    args = parser.parse_args()

    level = "DEBUG" if args.debug else "INFO" if args.verbose else None
    setup_logging(level)

    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    # No explicit targets -> auto-process the standard input folder.
    if not args.pdf_paths and not args.dir:
        auto_preprocess(
            _INPUT_DIR, dpi=args.dpi, force=args.force, workers=args.workers
        )
        logger.info("Start the server with: python %s/app.py", _ROOT.name)
        return 0

    try:
        targets = _explicit_pdfs(args)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1

    result = render_targets(
        targets, dpi=args.dpi, force=args.force, workers=args.workers
    )
    logger.info(
        "%d PDF(s): %d rendered, %d up-to-date, %d failed.",
        len(targets),
        result["rendered"],
        result["skipped"],
        result["failed"],
    )
    logger.info("Start the server with: python %s/app.py", _ROOT.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
