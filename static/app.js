"use strict";

// Palette used to auto-assign colors to new layers.
const PALETTE = [
  "#ea580c", "#0ea5e9", "#22c55e", "#a855f7", "#eab308",
  "#ec4899", "#14b8a6", "#f97316", "#6366f1", "#84cc16",
];
const MIN_SCALE = 0.25;
const MAX_SCALE = 12;
const MIN_BOX = 0.005; // smallest allowed normalized box edge

const state = {
  pdfs: [],
  pdfId: null,
  meta: null,
  pageIdx: 0,
  doc: { pdf_id: null, layers: [], annotations: [] },
  activeLayer: null,
  hiddenLayers: new Set(),
  tool: "select",
  selectedId: null,
  dirty: false,
  scale: 1,
  tx: 0,
  ty: 0,
};

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function $(id) { return document.getElementById(id); }

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === "className") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node[k] = v;
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  });
  children.forEach((c) =>
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c)
  );
  return node;
}

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return "a" + Math.random().toString(16).slice(2) + Date.now().toString(16);
}

function clamp01(v) { return Math.min(1, Math.max(0, v)); }

function currentPageNumber() {
  const page = state.meta.pages[state.pageIdx];
  return page ? page.number : null;
}

function layerByName(name) {
  return state.doc.layers.find((l) => l.name === name) || null;
}

function layerColor(name) {
  const layer = layerByName(name);
  return layer ? layer.color : "#888";
}

function pageAnnotations() {
  const pageNum = currentPageNumber();
  return state.doc.annotations.filter((a) => a.page === pageNum);
}

function markDirty() {
  state.dirty = true;
  $("dirty-flag").hidden = false;
}

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

function populatePdfSelect() {
  const sel = $("pdf-select");
  sel.replaceChildren();
  // Group options by request id so PDFs from the same request stay together.
  const groups = new Map();
  state.pdfs.forEach((p) => {
    const key = p.request_id || "ungrouped";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  });
  const multiGroup = groups.size > 1 || !groups.has("ungrouped");
  groups.forEach((items, key) => {
    const parent = multiGroup ? el("optgroup", { label: key }) : sel;
    items.forEach((p) =>
      parent.appendChild(el("option", { value: p.id, text: p.title }))
    );
    if (multiGroup) sel.appendChild(parent);
  });
  sel.onchange = (e) => loadPdf(e.target.value);
}

async function fetchPdfs() {
  state.pdfs = await (await fetch("/api/pdfs")).json();
  $("empty-state").hidden = state.pdfs.length > 0;
  populatePdfSelect();
}

async function loadPdfList() {
  await fetchPdfs();
  if (state.pdfs.length) await loadPdf(state.pdfs[0].id);
}

async function rescan() {
  const btn = $("rescan-btn");
  btn.disabled = true;
  $("hint").textContent = "Rescanning...";
  try {
    const res = await fetch("/api/rescan", { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    const result = await res.json();
    await fetchPdfs();
    // Keep the dropdown pointing at the open PDF if it still exists; only
    // auto-load when nothing was open yet (e.g. coming from the empty state).
    const stillThere = state.pdfs.some((p) => p.id === state.pdfId);
    if (stillThere) {
      $("pdf-select").value = state.pdfId;
    } else if (!state.pdfId && state.pdfs.length) {
      await loadPdf(state.pdfs[0].id);
    }
    $("hint").textContent =
      `Rescan: ${result.found} found, ${result.rendered} new, ${result.skipped} up-to-date.`;
  } catch (err) {
    $("hint").textContent = `Rescan failed: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

async function loadPdf(pdfId) {
  if (state.dirty && !confirm("Discard unsaved changes and switch PDF?")) {
    $("pdf-select").value = state.pdfId;
    return;
  }
  state.pdfId = pdfId;
  state.meta = await (await fetch(`/api/pdfs/${pdfId}/meta`)).json();
  state.doc = await (await fetch(`/api/pdfs/${pdfId}/annotations`)).json();
  if (!Array.isArray(state.doc.layers)) state.doc.layers = [];
  if (!Array.isArray(state.doc.annotations)) state.doc.annotations = [];
  state.pageIdx = 0;
  state.selectedId = null;
  state.hiddenLayers = new Set();
  state.activeLayer = state.doc.layers.length ? state.doc.layers[0].name : null;
  state.dirty = false;
  $("dirty-flag").hidden = true;
  resetZoom();
  buildPageSelect();
  renderAll();
}

// ---------------------------------------------------------------------------
// Zoom / pan
// ---------------------------------------------------------------------------

function applyTransform() {
  $("zoom-content").style.transform =
    `translate(${state.tx}px, ${state.ty}px) scale(${state.scale})`;
  $("zoom-level").textContent = `${Math.round(state.scale * 100)}%`;
}

function resetZoom() { state.scale = 1; state.tx = 0; state.ty = 0; }

function zoomAt(vx, vy, factor) {
  const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, state.scale * factor));
  if (next === state.scale) return;
  const cx = (vx - state.tx) / state.scale;
  const cy = (vy - state.ty) / state.scale;
  state.scale = next;
  state.tx = vx - cx * state.scale;
  state.ty = vy - cy * state.scale;
  applyTransform();
}

function zoomCentered(factor) {
  const vp = $("viewport");
  zoomAt(vp.clientWidth / 2, vp.clientHeight / 2, factor);
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderAll() {
  renderHeader();
  renderToolbar();
  renderCanvas();
  renderLayers();
  renderSelectionPanel();
  renderAnnList();
  applyTransform();
}

function renderHeader() {
  const pdf = state.pdfs.find((p) => p.id === state.pdfId);
  document.title = `Annotate — ${pdf ? pdf.title : ""}`;
  const total = state.doc.annotations.length;
  const reqPart = pdf && pdf.request_id ? `${pdf.request_id} · ` : "";
  $("meta").textContent =
    `${reqPart}${pdf ? pdf.title : ""} · ${state.meta.page_count} pages · ${total} annotation(s)`;
}

function renderToolbar() {
  document.querySelectorAll(".tool").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tool === state.tool);
  });
  const vp = $("viewport");
  vp.classList.toggle("tool-create", state.tool === "create");
  const hints = {
    select: "Select: click a box to select; drag a box to move it; drag empty space to pan.",
    create: "Create: drag on the page to draw a rectangle in the active layer.",
    delete: "Delete: click a box to remove it.",
    copy: "Copy: click a box to duplicate it.",
  };
  $("hint").textContent = hints[state.tool] || "";
}

function buildPageSelect() {
  const sel = $("page-select");
  sel.replaceChildren();
  state.meta.pages.forEach((page, idx) => {
    const count = state.doc.annotations.filter((a) => a.page === page.number).length;
    sel.appendChild(el("option", { value: String(idx), text: `Page ${page.number} (${count})` }));
  });
  sel.onchange = (e) => goToPage(Number(e.target.value));
}

function renderCanvas() {
  const content = $("zoom-content");
  content.replaceChildren();
  $("page-select").value = String(state.pageIdx);

  const page = state.meta.pages[state.pageIdx];
  if (!page) {
    content.appendChild(el("div", { className: "no-image", text: "No page." }));
    return;
  }

  const stage = el("div", { className: "image-stage" });
  // Reserve correct aspect ratio before the image loads so coords map cleanly.
  if (page.width && page.height) {
    stage.style.aspectRatio = `${page.width} / ${page.height}`;
  }
  const img = el("img", {
    src: `/api/pdfs/${state.pdfId}/pages/${page.number}.png`,
    alt: `Page ${page.number}`,
    draggable: false,
  });
  const overlay = el("div", { className: "overlay" });

  pageAnnotations().forEach((ann) => {
    if (state.hiddenLayers.has(ann.layer)) return;
    overlay.appendChild(buildBoxEl(ann));
  });

  stage.appendChild(img);
  stage.appendChild(overlay);
  content.appendChild(stage);
}

function buildBoxEl(ann) {
  const b = ann.bbox;
  const left = clamp01(b.left) * 100;
  const top = clamp01(b.top) * 100;
  const width = Math.max(0, clamp01(b.right) - clamp01(b.left)) * 100;
  const height = Math.max(0, clamp01(b.bottom) - clamp01(b.top)) * 100;
  const color = layerColor(ann.layer);
  const selected = ann.id === state.selectedId;
  const box = el("div", {
    className: `bbox selectable${selected ? " selected" : ""}`,
    "data-id": ann.id,
    style:
      `left:${left}%;top:${top}%;width:${width}%;height:${height}%;` +
      `border-color:${color};background:${color}22;`,
  }, [
    el("span", { className: "bbox-label", style: `background:${color}`, text: ann.layer || "—" }),
  ]);
  return box;
}

function renderLayers() {
  // Active-layer dropdown.
  const active = $("active-layer-select");
  active.replaceChildren();
  if (!state.doc.layers.length) {
    active.appendChild(el("option", { value: "", text: "— add a layer —" }));
    active.disabled = true;
  } else {
    active.disabled = false;
    state.doc.layers.forEach((l) =>
      active.appendChild(el("option", { value: l.name, text: l.name }))
    );
    if (state.activeLayer) active.value = state.activeLayer;
  }
  active.onchange = (e) => { state.activeLayer = e.target.value; };

  // Layer list with counts + visibility toggles.
  const list = $("layer-list");
  list.replaceChildren();
  if (!state.doc.layers.length) {
    list.appendChild(el("li", { className: "sel-info", text: "No layers yet." }));
  }
  state.doc.layers.forEach((layer) => {
    const total = state.doc.annotations.filter((a) => a.layer === layer.name).length;
    const onPage = pageAnnotations().filter((a) => a.layer === layer.name).length;
    const hidden = state.hiddenLayers.has(layer.name);
    const row = el("li", { className: `layer-row${hidden ? " hidden-layer" : ""}` }, [
      el("span", { className: "swatch", style: `background:${layer.color}` }),
      el("span", { className: "layer-name", title: layer.name, text: layer.name }),
      el("span", { className: "layer-count", text: `${onPage}/${total}` }),
      el("button", {
        className: "vis-toggle",
        title: hidden ? "Show layer" : "Hide layer",
        text: hidden ? "Show" : "Hide",
        onclick: () => toggleLayerVisibility(layer.name),
      }),
    ]);
    list.appendChild(row);
  });
}

function renderSelectionPanel() {
  const panel = $("selection-panel");
  const ann = state.doc.annotations.find((a) => a.id === state.selectedId);
  if (!ann) { panel.hidden = true; return; }
  panel.hidden = false;
  $("sel-info").textContent = `Page ${ann.page} · ${ann.type || "rect"}`;

  const sel = $("reassign-select");
  sel.replaceChildren();
  state.doc.layers.forEach((l) =>
    sel.appendChild(el("option", { value: l.name, text: l.name }))
  );
  sel.value = ann.layer;
  sel.onchange = (e) => {
    ann.layer = e.target.value;
    markDirty();
    renderAll();
  };
  $("sel-copy").onclick = () => copyAnnotation(ann.id);
  $("sel-delete").onclick = () => deleteAnnotation(ann.id);
}

function renderAnnList() {
  const list = $("ann-list");
  list.replaceChildren();
  const anns = pageAnnotations();
  if (!anns.length) {
    list.appendChild(el("li", { className: "sel-info", text: "Nothing on this page." }));
    return;
  }
  anns.forEach((ann, i) => {
    const row = el("li", {
      className: `ann-row${ann.id === state.selectedId ? " selected" : ""}`,
      onclick: () => selectAnnotation(ann.id),
    }, [
      el("span", { className: "swatch", style: `background:${layerColor(ann.layer)}` }),
      el("span", { className: "ann-layer", text: `#${i + 1} · ${ann.layer || "—"}` }),
    ]);
    list.appendChild(row);
  });
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

function selectAnnotation(id) {
  state.selectedId = id;
  renderAll();
}

function deleteAnnotation(id) {
  const before = state.doc.annotations.length;
  state.doc.annotations = state.doc.annotations.filter((a) => a.id !== id);
  if (state.doc.annotations.length !== before) {
    if (state.selectedId === id) state.selectedId = null;
    markDirty();
    buildPageSelect();
    renderAll();
  }
}

function copyAnnotation(id) {
  const src = state.doc.annotations.find((a) => a.id === id);
  if (!src) return;
  const off = 0.02;
  const w = src.bbox.right - src.bbox.left;
  const h = src.bbox.bottom - src.bbox.top;
  let left = clamp01(src.bbox.left + off);
  let top = clamp01(src.bbox.top + off);
  if (left + w > 1) left = Math.max(0, 1 - w);
  if (top + h > 1) top = Math.max(0, 1 - h);
  const copy = {
    id: uuid(),
    page: src.page,
    type: src.type || "rect",
    layer: src.layer,
    bbox: { left, top, right: left + w, bottom: top + h },
  };
  state.doc.annotations.push(copy);
  state.selectedId = copy.id;
  markDirty();
  buildPageSelect();
  renderAll();
}

function createAnnotation(bbox) {
  if (!state.activeLayer) {
    $("hint").textContent = "Add a layer first, then draw.";
    return;
  }
  const ann = {
    id: uuid(),
    page: currentPageNumber(),
    type: "rect",
    layer: state.activeLayer,
    bbox,
  };
  state.doc.annotations.push(ann);
  state.selectedId = ann.id;
  markDirty();
  buildPageSelect();
  renderAll();
}

function addLayer() {
  const input = $("layer-name-input");
  const name = input.value.trim();
  if (!name) return;
  if (layerByName(name)) {
    state.activeLayer = name;
    input.value = "";
    renderLayers();
    return;
  }
  const color = PALETTE[state.doc.layers.length % PALETTE.length];
  state.doc.layers.push({ name, color });
  state.activeLayer = name;
  input.value = "";
  markDirty();
  renderAll();
}

function toggleLayerVisibility(name) {
  if (state.hiddenLayers.has(name)) state.hiddenLayers.delete(name);
  else state.hiddenLayers.add(name);
  renderAll();
}

function goToPage(idx) {
  if (idx < 0 || idx >= state.meta.pages.length) return;
  state.pageIdx = idx;
  state.selectedId = null;
  renderAll();
}

async function save() {
  const btn = $("save-btn");
  btn.disabled = true;
  try {
    const res = await fetch(`/api/pdfs/${state.pdfId}/annotations`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pdf_id: state.pdfId,
        layers: state.doc.layers,
        annotations: state.doc.annotations,
      }),
    });
    if (!res.ok) throw new Error(await res.text());
    state.dirty = false;
    $("dirty-flag").hidden = true;
    $("hint").textContent = "Saved.";
  } catch (err) {
    alert(`Save failed: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Pointer interaction (create / move / pan)
// ---------------------------------------------------------------------------

function stageRect() {
  const stage = $("zoom-content").querySelector(".image-stage");
  return stage ? stage.getBoundingClientRect() : null;
}

function toNorm(clientX, clientY) {
  const rect = stageRect();
  if (!rect) return null;
  return {
    x: (clientX - rect.left) / rect.width,
    y: (clientY - rect.top) / rect.height,
  };
}

function initPointer() {
  const vp = $("viewport");
  let mode = null; // "pan" | "draft" | "move"
  let lastX = 0, lastY = 0;
  let draftEl = null, draftStart = null;
  let moveAnn = null, moveStartNorm = null, moveOrigBox = null, moved = false;

  vp.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const boxEl = e.target.closest(".bbox");

    // Tool actions that fire on a single click of a box.
    if (boxEl && (state.tool === "delete" || state.tool === "copy")) {
      const id = boxEl.dataset.id;
      if (state.tool === "delete") deleteAnnotation(id);
      else copyAnnotation(id);
      return;
    }

    if (state.tool === "create") {
      const n = toNorm(e.clientX, e.clientY);
      if (!n) return;
      mode = "draft";
      draftStart = { x: clamp01(n.x), y: clamp01(n.y) };
      draftEl = el("div", { className: "draft-box" });
      $("zoom-content").querySelector(".image-stage").appendChild(draftEl);
      vp.setPointerCapture(e.pointerId);
      return;
    }

    if (boxEl && state.tool === "select") {
      const id = boxEl.dataset.id;
      selectAnnotation(id);
      moveAnn = state.doc.annotations.find((a) => a.id === id);
      moveOrigBox = { ...moveAnn.bbox };
      moveStartNorm = toNorm(e.clientX, e.clientY);
      moved = false;
      mode = "move";
      vp.setPointerCapture(e.pointerId);
      return;
    }

    // Empty space (or select tool on background) -> pan; also clear selection.
    if (state.tool === "select" && !boxEl && state.selectedId) {
      selectAnnotation(null);
    }
    mode = "pan";
    lastX = e.clientX; lastY = e.clientY;
    vp.classList.add("panning");
    vp.setPointerCapture(e.pointerId);
  });

  vp.addEventListener("pointermove", (e) => {
    if (mode === "pan") {
      state.tx += e.clientX - lastX;
      state.ty += e.clientY - lastY;
      lastX = e.clientX; lastY = e.clientY;
      applyTransform();
    } else if (mode === "draft" && draftEl) {
      const n = toNorm(e.clientX, e.clientY);
      if (!n) return;
      const x = clamp01(n.x), y = clamp01(n.y);
      const left = Math.min(draftStart.x, x);
      const top = Math.min(draftStart.y, y);
      const w = Math.abs(x - draftStart.x);
      const h = Math.abs(y - draftStart.y);
      draftEl.style.left = `${left * 100}%`;
      draftEl.style.top = `${top * 100}%`;
      draftEl.style.width = `${w * 100}%`;
      draftEl.style.height = `${h * 100}%`;
    } else if (mode === "move" && moveAnn) {
      const n = toNorm(e.clientX, e.clientY);
      if (!n || !moveStartNorm) return;
      let dx = n.x - moveStartNorm.x;
      let dy = n.y - moveStartNorm.y;
      if (Math.abs(dx) > 0.001 || Math.abs(dy) > 0.001) moved = true;
      const w = moveOrigBox.right - moveOrigBox.left;
      const h = moveOrigBox.bottom - moveOrigBox.top;
      let left = clamp01(moveOrigBox.left + dx);
      let top = clamp01(moveOrigBox.top + dy);
      if (left + w > 1) left = 1 - w;
      if (top + h > 1) top = 1 - h;
      left = Math.max(0, left); top = Math.max(0, top);
      moveAnn.bbox = { left, top, right: left + w, bottom: top + h };
      const boxEl = $("zoom-content").querySelector(`.bbox[data-id="${moveAnn.id}"]`);
      if (boxEl) {
        boxEl.style.left = `${left * 100}%`;
        boxEl.style.top = `${top * 100}%`;
      }
    }
  });

  const finish = (e) => {
    if (mode === "draft" && draftEl) {
      const n = toNorm(e.clientX, e.clientY);
      draftEl.remove();
      if (n) {
        const x = clamp01(n.x), y = clamp01(n.y);
        const left = Math.min(draftStart.x, x);
        const top = Math.min(draftStart.y, y);
        const right = Math.max(draftStart.x, x);
        const bottom = Math.max(draftStart.y, y);
        if (right - left >= MIN_BOX && bottom - top >= MIN_BOX) {
          createAnnotation({ left, top, right, bottom });
        }
      }
      draftEl = null; draftStart = null;
    } else if (mode === "move" && moveAnn) {
      if (moved) { markDirty(); }
      moveAnn = null; moveStartNorm = null; moveOrigBox = null;
    }
    if (mode === "pan") vp.classList.remove("panning");
    mode = null;
    try { vp.releasePointerCapture(e.pointerId); } catch (_) {}
  };
  vp.addEventListener("pointerup", finish);
  vp.addEventListener("pointercancel", finish);

  vp.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = vp.getBoundingClientRect();
    const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
    zoomAt(e.clientX - rect.left, e.clientY - rect.top, factor);
  }, { passive: false });
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function setTool(tool) {
  state.tool = tool;
  renderToolbar();
}

function initControls() {
  document.querySelectorAll(".tool").forEach((btn) => {
    btn.onclick = () => setTool(btn.dataset.tool);
  });
  $("prev-page").onclick = () => goToPage(state.pageIdx - 1);
  $("next-page").onclick = () => goToPage(state.pageIdx + 1);
  $("zoom-in").onclick = () => zoomCentered(1.25);
  $("zoom-out").onclick = () => zoomCentered(1 / 1.25);
  $("zoom-reset").onclick = () => { resetZoom(); applyTransform(); };
  $("rescan-btn").onclick = rescan;
  $("add-layer-btn").onclick = addLayer;
  $("layer-name-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") addLayer();
  });
  $("save-btn").onclick = save;

  document.addEventListener("keydown", (e) => {
    const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
    if (typing) return;
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
      e.preventDefault(); save(); return;
    }
    switch (e.key.toLowerCase()) {
      case "v": setTool("select"); break;
      case "r": setTool("create"); break;
      case "d": if (state.selectedId) deleteAnnotation(state.selectedId); else setTool("delete"); break;
      case "delete": case "backspace":
        if (state.selectedId) { e.preventDefault(); deleteAnnotation(state.selectedId); }
        break;
      case "c": if (state.selectedId) copyAnnotation(state.selectedId); else setTool("copy"); break;
      case "escape": selectAnnotation(null); break;
    }
  });

  window.addEventListener("beforeunload", (e) => {
    if (state.dirty) { e.preventDefault(); e.returnValue = ""; }
  });
}

async function main() {
  initControls();
  initPointer();
  setTool("select");
  await loadPdfList();
}

main();
