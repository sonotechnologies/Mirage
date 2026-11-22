/* Mirage UI controller (plain JS, no build step).
 *
 * Talks to Python through window.pywebview.api (bridge/api.py) and receives
 * pushed events via window.mirage.on(event, data). Without pywebview (a plain
 * browser) it falls back to the mock bridge in mock.js.
 */
(function () {
  "use strict";

  // ------------------------------------------------------------------ helpers
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === "class") el.className = v;
      else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === "html") el.innerHTML = v;
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  const svg = (html, w = 16) => { const s = document.createElement("span"); s.style.display = "contents"; s.innerHTML = html.replace("<svg", `<svg width="${w}" height="${w}"`); return s.firstChild; };
  const ICON = {
    play: '<svg viewBox="0 0 12 12" fill="currentColor"><path d="M2 1l9 5-9 5z"/></svg>',
    stop: '<svg viewBox="0 0 11 11" fill="currentColor"><rect width="11" height="11" rx="1.5"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M12 16V4M7 9l5-5 5 5M4 20h16"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  };
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const prettyCombo = (c) => !c ? "—" : c.split("+").map((p) => ({ ctrl: "Ctrl", alt: "Alt", shift: "Shift", windows: "Win", right: "→", left: "←", up: "↑", down: "↓" }[p] || (p.length === 1 ? p.toUpperCase() : p[0].toUpperCase() + p.slice(1)))).join("+");
  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ------------------------------------------------------------------ state
  const S = {
    api: null, mock: false,
    status: "loading", live: false, faces: [], active: null,
    settings: { params: { swap: true, blend: 60, sharpness: 30, colour: 70, enhancer: false, occlusion: true }, hotkeys: {}, mirror: true, camera: 0, resolution: null, provider: "auto" },
    tiers: {}, tier: null, cameras: [], provider: null, benchmark: null, providersAvailable: [],
    view: "Mirage", split: 50,
    fps: null, ms: null, lowStreak: 0, lowPerf: false, noFace: false, gotFrame: false,
    error: null, collapsedManual: null, menu: null, asyncSwap: false, hotkeysAvailable: true,
    firstRun: false, splash: null,
  };

  // ------------------------------------------------------------------ bridge
  window.mirage = {
    handlers: {},
    on(event, data) { const fn = EVENTS[event]; if (fn) { try { fn(data || {}); } catch (e) { console.error(e); } } },
  };
  const call = async (name, ...args) => {
    try {
      const fn = S.api && S.api[name];
      if (!fn) return { ok: false, error: "no_method" };
      const r = await fn(...args);
      return r || { ok: true };
    } catch (e) { console.error(name, e); return { ok: false, error: "internal", message: String(e) }; }
  };

  // ------------------------------------------------------------------ events from Python
  const EVENTS = {
    ready(st) { applyState(st); renderAll(); if (S.splash && S.splash.mode !== "result") closeSplash(); },
    preview(d) {
      if (d.frame) { $("#preview").src = d.frame; if (!S.gotFrame) { S.gotFrame = true; $("#preview-placeholder").hidden = true; } }
      if (d.original) $("#preview-orig").src = d.original;
    },
    stats(d) {
      S.fps = d.fps; S.ms = d.ms; S.asyncSwap = d.swap_fps != null;
      S.lowStreak = d.fps < 15 && d.fps > 0 ? S.lowStreak + 1 : 0;
      const wasLow = S.lowPerf; S.lowPerf = S.lowStreak >= 3 && S.tier !== "Performance";
      if (typeof d.live === "boolean" && d.live !== S.live) { S.live = d.live; renderLive(); }
      renderFps(d);
      if (wasLow !== S.lowPerf) {
        renderControls();
        const hint = S.lowPerf && $("#group-performance .perfhint");
        if (hint) hint.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
      }
    },
    face_lost() { S.noFace = true; renderPreviewFlags(); },
    face_found() { S.noFace = false; renderPreviewFlags(); },
    faces(d) { S.faces = d.faces || []; renderFaces(); renderEmpty(); },
    face_selected(d) { setActive(d.id, true); },
    param(d) { S.settings.params[d.key] = d.value; renderControls(); renderPreviewFlags(); },
    live(d) { S.live = !!d.live; renderLive(); },
    tier(d) { S.tier = d.tier; S.settings.tier = d.tier; if (S.tiers[d.tier] && !S.tiers[d.tier].enhancer_allowed) S.settings.params.enhancer = false; S.lowStreak = 0; S.lowPerf = false; renderControls(); },
    source() {},
    error(d) { handleError(d); },
    benchmark_progress(d) { onBenchmark(d); },
  };

  function applyState(st) {
    if (!st) return;
    S.status = st.status || S.status;
    S.settings = Object.assign(S.settings, st.settings || {});
    S.faces = st.faces || [];
    S.active = (st.settings && st.settings.active_face) || null;
    S.tiers = st.tiers || S.tiers;
    S.tier = st.tier || (st.settings && st.settings.tier) || null;
    S.cameras = st.cameras && st.cameras.length ? st.cameras : S.cameras;
    S.provider = st.provider || S.provider;
    S.benchmark = st.benchmark || S.benchmark;
    S.providersAvailable = st.providers_available || [];
    S.live = !!st.live;
    S.asyncSwap = !!st.async_swap;
    S.hotkeysAvailable = st.hotkeys_available !== false;
    S.version = st.version;
    S.vcam = st.vcam;
    if (st.status === "error" && st.error) handleError({ code: "models_failed", message: st.error });
  }

  // ------------------------------------------------------------------ renderers
  function renderAll() {
    renderStatus(); renderHw(); renderControls(); renderLive(); renderFaces(); renderEmpty(); renderPreviewFlags(); renderBanner(); renderHotkeyHint(); renderCollapsed();
  }

  function renderStatus() {
    const pill = $("#status"), txt = $("#status-text");
    pill.className = "pill pywebview-drag-region " + (S.error && S.error.blocking ? "pill--error" : S.live ? "pill--live" : "pill--offline");
    txt.textContent = S.error && S.error.blocking ? (S.error.short || "Camera error") : S.live ? "Live in Mirage Camera" : S.status === "ready" ? "Offline" : "Starting…";
  }

  function renderHw() {
    const p = S.provider || {};
    $("#hw-k").textContent = p.gpu ? "GPU" : "CPU";
    $("#hw-v").textContent = p.gpu ? (p.device || "DirectML") : (p.kind ? "No GPU acceleration" : "…");
    $("#hw").title = p.gpu ? `DirectML · ${p.device}` : "Running on the CPU";
  }

  function renderLive() {
    const ready = S.status === "ready";
    const b = $("#live-btn"), r = $("#rail-live");
    b.replaceChildren(svg(S.live ? ICON.stop : ICON.play, S.live ? 11 : 12), S.live ? "STOP" : "START");
    b.classList.toggle("stop", S.live); b.dataset.action = S.live ? "stop" : "start"; b.disabled = !ready;
    r.replaceChildren(svg(S.live ? ICON.stop : ICON.play, 12));
    r.classList.toggle("stop", S.live); r.dataset.action = S.live ? "stop" : "start"; r.setAttribute("aria-label", S.live ? "Stop" : "Start"); r.disabled = !ready;
    renderStatus(); renderPreviewFlags();
  }

  function renderFps(d) {
    const el = $("#fps");
    if (S.fps == null) return;
    $("#fps-v").textContent = "FPS " + Math.round(S.fps);
    $("#ms-v").textContent = Math.round(S.ms) + "ms";
    el.classList.toggle("low", S.fps < 15 && S.fps > 0);
    if (d && d.stages) {
      const parts = Object.entries(d.stages).filter(([k]) => k !== "swap_reuse").map(([k, v]) => `${k} ${v}ms`);
      el.title = parts.join(" · ") + (d.swap_fps ? ` · swap ${d.swap_fps}/s (CPU, tracked between swaps)` : "");
    }
    $("#rail-perf").classList.toggle("warn", S.lowPerf);
  }

  function renderPreviewFlags() {
    const box = $("#preview-box");
    const swapping = S.settings.params.swap && S.active;
    const noface = S.noFace && swapping && S.status === "ready";
    box.classList.toggle("noface", !!noface);
    box.classList.toggle("live", S.live && !noface);
    $("#noface").hidden = !noface;
    const split = S.view === "Split";
    $("#split").hidden = !split; $("#split-range").hidden = !split;
    $("#preview-orig").style.clipPath = `inset(0 ${100 - S.split}% 0 0)`;
    $("#split-divider").style.left = S.split + "%";
    $$("#viewtabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.value === S.view)));
  }

  function renderBanner() {
    const e = S.error, b = $("#banner");
    b.hidden = !e;
    if (!e) return;
    $("#banner-title").textContent = e.title;
    $("#banner-body").textContent = e.body;
    $("#banner-fix").hidden = !e.fix;
    $("#banner-fix").textContent = e.fixLabel || "Fix";
  }

  function renderHotkeyHint() { $("#bypass-key").textContent = prettyCombo(S.settings.hotkeys.bypass || "ctrl+alt+0"); }

  function renderCollapsed() {
    const auto = window.innerWidth < 1180;
    const collapsed = S.collapsedManual ?? auto;
    $("#app").classList.toggle("collapsed", collapsed);
  }

  // ---- right panel ------------------------------------------------------
  function toggleRow(key, label, opts = {}) {
    const on = !!S.settings.params[key];
    const btn = h("button", { class: "switch", role: "switch", "aria-checked": String(on), "aria-label": label, "data-action": "set_param", "data-param": key, disabled: opts.disabled },
      h("span", { class: "knob" }));
    btn.addEventListener("click", () => setParam(key, !S.settings.params[key]));
    return h("div", { class: "row" + (opts.disabled ? " disabled" : "") },
      h("label", { class: "row-inline", style: { cursor: opts.disabled ? "not-allowed" : "pointer" } }, h("span", {}, label), btn),
      opts.hint ? h("div", { class: "hint" }, opts.hint) : null);
  }
  function sliderRow(key, label) {
    const v = S.settings.params[key];
    const val = h("span", { class: "val" }, String(v));
    const fill = h("div", { class: "fill", style: { width: v + "%" } });
    const thumb = h("div", { class: "thumb", style: { left: v + "%" } });
    const input = h("input", { type: "range", min: "0", max: "100", value: String(v), "aria-label": label, "data-action": "set_param", "data-param": key });
    let raf = 0;
    input.addEventListener("input", () => {
      const n = +input.value; S.settings.params[key] = n;
      val.textContent = n; fill.style.width = n + "%"; thumb.style.left = n + "%";
      cancelAnimationFrame(raf); raf = requestAnimationFrame(() => call("set_param", key, n));
    });
    input.addEventListener("change", () => call("set_param", key, +input.value));
    return h("div", { class: "row" },
      h("div", { class: "slider-head" }, h("span", {}, label), val),
      h("div", { class: "slider" }, h("div", { class: "track" }), fill, thumb, input));
  }
  function selectRow(key, label, options, value, onChange, opts = {}) {
    const sel = h("select", { class: "select", "aria-label": label, "data-action": "set_param", "data-param": key, disabled: opts.disabled },
      options.map(([v, text, dis]) => h("option", { value: String(v), selected: String(v) === String(value), disabled: dis }, text)));
    sel.addEventListener("change", () => onChange(sel.value));
    return h("div", { class: "row" }, h("div", { class: "row-inline" }, h("span", {}, label), sel));
  }

  function renderControls() {
    const host = $("#controls");
    const scroll = host.scrollTop;
    const tier = S.tiers[S.tier] || {};
    const enhancerOk = tier.enhancer_allowed !== false;
    const camOpts = (S.cameras.length ? S.cameras : [{ id: S.settings.camera ?? 0, name: "Camera " + ((S.settings.camera ?? 0) + 1) }]).map((c) => [c.id, c.name]);
    const tierOpts = ["Ultra", "Balanced", "Performance"].map((t) => [t, t, t === "Ultra" && S.provider && !S.provider.gpu]);
    const groups = [
      ["swap", "SWAP", [toggleRow("swap", "Face swap"), sliderRow("blend", "Blend")]],
      ["look", "LOOK", [sliderRow("sharpness", "Sharpness"), sliderRow("colour", "Colour match"),
        toggleRow("enhancer", "Enhancer", { disabled: !enhancerOk, hint: !enhancerOk ? "Off on the Performance tier" : null }),
        toggleRow("occlusion", "Occlusion")]],
      ["input", "INPUT", [selectRow("camera", "Camera", camOpts, S.settings.camera, (v) => setCamera(+v, null))]],
      ["performance", "PERFORMANCE", [selectRow("tier", "Mode", tierOpts, S.tier || "Balanced", (v) => setTier(v))]],
    ];
    host.replaceChildren(...groups.map(([id, title, rows]) => {
      const sec = h("section", { class: "group", id: "group-" + id, "aria-label": title }, h("div", { class: "label" }, title), ...rows);
      if (id === "performance" && S.lowPerf) {
        sec.append(h("div", { class: "perfhint" },
          h("div", {}, "Running slow. Switch to Performance mode?"),
          h("button", { class: "btn-warn", "data-action": "set_tier", "data-param": "mode", "data-value": "Performance", onclick: () => setTier("Performance") }, "Switch")));
      }
      return sec;
    }));
    host.scrollTop = scroll;
  }

  // ---- face strip -------------------------------------------------------
  function renderEmpty() {
    const empty = S.faces.length === 0;
    $("#dropzone").hidden = !empty;
    $("#faces").hidden = empty;
  }

  function faceCard(f, i) {
    const active = f.id === S.active;
    const kids = [];
    if (f.thumb) kids.push(h("img", { src: f.thumb, alt: "", draggable: "false" }));
    else kids.push(h("span", { class: "initial" }, (f.name || "?")[0]));
    if (f.hotkey) kids.push(h("span", { class: "badge" }, String(f.hotkey)));
    kids.push(h("span", { class: "name" }, f.name));
    const bg = f.thumb ? null : { background: `linear-gradient(160deg,hsl(${f.hue ?? 200} 16% 24%),hsl(${f.hue ?? 200} 12% 12%))` };
    const btn = h("button", {
      class: "face", role: "option", "data-action": "select_face", "data-param": f.id, "data-index": String(i),
      "aria-pressed": String(active), "aria-selected": String(active), "aria-label": `Face ${i + 1}, ${f.name}`,
      draggable: "true", style: bg, title: f.hotkey ? `${f.name} · ${prettyCombo(S.settings.hotkeys["face_" + f.hotkey])}` : f.name,
    }, kids);
    btn.addEventListener("click", () => selectFace(f.id));
    btn.addEventListener("contextmenu", (e) => { e.preventDefault(); openMenu(f, e.clientX, e.clientY); });
    btn.addEventListener("keydown", (e) => {
      if (e.key === "ContextMenu" || (e.shiftKey && e.key === "F10")) { e.preventDefault(); const r = btn.getBoundingClientRect(); openMenu(f, r.left + 8, r.top + 8); }
      if (e.key === "Delete") { e.preventDefault(); confirmDelete(f); }
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); const cards = $$("#faces .face"); const j = clamp(i + (e.key === "ArrowRight" ? 1 : -1), 0, cards.length - 1); cards[j] && cards[j].focus(); }
    });
    // drag to reorder
    btn.addEventListener("dragstart", (e) => { e.dataTransfer.setData("text/x-mirage-face", f.id); e.dataTransfer.effectAllowed = "move"; btn.classList.add("dragging"); });
    btn.addEventListener("dragend", () => { btn.classList.remove("dragging"); $$(".face.drop-before").forEach((x) => x.classList.remove("drop-before")); });
    btn.addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes("text/x-mirage-face")) { e.preventDefault(); $$(".face.drop-before").forEach((x) => x.classList.remove("drop-before")); btn.classList.add("drop-before"); } });
    btn.addEventListener("drop", (e) => {
      const id = e.dataTransfer.getData("text/x-mirage-face");
      if (!id) return;
      e.preventDefault(); e.stopPropagation();
      const ids = S.faces.map((x) => x.id).filter((x) => x !== id);
      ids.splice(ids.indexOf(f.id), 0, id);
      S.faces.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
      renderFaces();
      call("reorder_faces", ids);
    });
    return btn;
  }

  function renderFaces() {
    const host = $("#faces");
    const full = S.faces.length >= 12;
    const add = h("button", { class: "add-card", "data-action": "import_face", "aria-label": full ? "Library full (12 faces)" : "Add face", title: full ? "Library full (12 faces)" : "Add face", disabled: full, onclick: () => openImport() }, svg(ICON.plus, 22));
    host.replaceChildren(add, ...S.faces.map(faceCard));
  }

  // ------------------------------------------------------------------ actions
  async function selectFace(id, fromKey) {
    if (id === S.active) return;
    setActive(id, true);
    const r = await call("select_face", id);
    if (!r.ok) toastError(r.message || "Couldn't switch face");
  }

  function setActive(id, animate) {
    const changed = id !== S.active;
    S.active = id;
    $$("#faces .face").forEach((b) => { const on = b.dataset.param === id; b.setAttribute("aria-pressed", String(on)); b.setAttribute("aria-selected", String(on)); });
    renderPreviewFlags();
    if (changed && animate && id) {
      const i = S.faces.findIndex((f) => f.id === id);
      shimmer();
      if (i >= 0) toast("Face " + (i + 1), S.faces[i].name);
    }
  }

  function shimmer() {
    const s = $("#shimmer"); if (reducedMotion()) return;
    s.classList.remove("run"); void s.offsetWidth; s.classList.add("run");
  }

  let toastTimer = 0;
  function toast(tag, text, opts = {}) {
    const host = $("#toast-host");
    clearTimeout(toastTimer);
    const el = h("div", { class: "toast" + (opts.long ? " long" : "") + (opts.error ? " err" : ""), role: "status" }, h("span", { class: "mono" }, tag), "· " + text);
    host.replaceChildren(el);
    toastTimer = setTimeout(() => el.remove(), opts.long ? 3000 : 1200);
  }
  const toastError = (msg) => toast("Error", msg, { long: true, error: true });

  async function setParam(key, value) {
    const prev = S.settings.params[key];
    S.settings.params[key] = value;
    renderControls(); renderPreviewFlags();
    const r = await call("set_param", key, value);
    if (!r.ok) { S.settings.params[key] = prev; renderControls(); toastError(r.message || "Couldn't change that setting"); }
    else if (r.value !== undefined && r.value !== value) { S.settings.params[key] = r.value; renderControls(); }
  }

  async function setTier(tier) {
    const r = await call("set_tier", tier);
    if (!r.ok) { toastError(r.message || "Couldn't change mode"); renderControls(); return; }
    EVENTS.tier({ tier });
  }

  async function setCamera(id, resolution) {
    S.settings.camera = id; S.settings.resolution = resolution;
    S.gotFrame = false; $("#preview-placeholder").hidden = false;
    const r = await call("set_camera", id, resolution);
    if (!r.ok) toastError(r.message || "Couldn't open that camera");
    renderControls();
  }

  async function toggleLive() {
    if (S.status !== "ready") return;
    if (S.live) { await call("stop"); S.live = false; renderLive(); return; }
    const r = await call("start");
    if (r.ok) {
      S.live = true; renderLive();
      if (S.error && S.error.code.startsWith("vcam")) clearError();
      if (r.show_helper) openHelper();
    } else if (r.error !== "vcam_missing") toastError(r.message || "Couldn't go live");
  }

  // ------------------------------------------------------------------ errors
  const ERRORS = {
    camera_in_use: { title: "Camera in use.", body: "Another app has the webcam open. Close it, then retry.", fix: true, fixLabel: "Retry", blocking: true, short: "Camera error" },
    camera_lost: { title: "Camera disconnected.", body: "Plug it back in, then retry.", fix: true, fixLabel: "Retry", blocking: true, short: "Camera error" },
    camera_failed: { title: "Camera unavailable.", body: "", fix: true, fixLabel: "Retry", blocking: true, short: "Camera error" },
    vcam_missing: { title: "Virtual camera driver missing.", body: "Install the Mirage Camera driver to go live.", fix: true, fixLabel: "Fix", blocking: true, short: "Driver missing" },
    vcam_failed: { title: "Virtual camera stopped.", body: "Press Start to try again.", fix: false, blocking: false },
    models_missing: { title: "Face models missing.", body: "Reinstall Mirage to restore them.", fix: true, fixLabel: "Retry", blocking: true, short: "Models missing" },
    models_failed: { title: "Couldn't load the face models.", body: "", fix: true, fixLabel: "Retry", blocking: true, short: "Engine error" },
  };
  function handleError(d) {
    const tpl = ERRORS[d.code];
    if (!tpl) { toastError(d.message || d.code); return; }
    S.error = Object.assign({ code: d.code }, tpl, { body: tpl.body || d.message || "" });
    if (d.code === "vcam_missing" || d.code === "vcam_failed") { S.live = false; renderLive(); }
    if (d.code.startsWith("camera")) { S.gotFrame = false; $("#preview-placeholder").textContent = "NO CAMERA"; $("#preview-placeholder").hidden = false; }
    renderBanner(); renderStatus();
  }
  function clearError() { S.error = null; $("#preview-placeholder").textContent = "STARTING CAMERA…"; renderBanner(); renderStatus(); }
  async function fixError() {
    const e = S.error; if (!e) return;
    const btn = $("#banner-fix"); btn.disabled = true; btn.textContent = "…";
    const r = await call("fix_error", e.code);
    btn.disabled = false;
    if (r.ok) { clearError(); if (e.code.startsWith("camera")) $("#preview-placeholder").hidden = S.gotFrame; }
    else { btn.textContent = e.fixLabel || "Fix"; toastError(r.message || "That didn't work"); }
  }

  // ------------------------------------------------------------------ overlays
  const overlay = $("#overlay-host");
  let lastFocus = null;
  function openOverlay(node, { onClose } = {}) {
    lastFocus = document.activeElement;
    const scrim = h("div", { class: "scrim" }, node);
    scrim.addEventListener("mousedown", (e) => { if (e.target === scrim) closeOverlay(); });
    scrim._onClose = onClose;
    overlay.replaceChildren(scrim);
    const first = node.querySelector("[autofocus]") || node.querySelector("button, input, select");
    first && first.focus();
  }
  function closeOverlay() {
    const scrim = overlay.firstChild; if (!scrim) return;
    scrim._onClose && scrim._onClose();
    overlay.replaceChildren();
    lastFocus && lastFocus.focus && lastFocus.focus();
  }
  function trapFocus(e) {
    const scrim = overlay.firstChild; if (!scrim || e.key !== "Tab") return;
    const f = $$("button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])", scrim).filter((x) => x.offsetParent);
    if (!f.length) return;
    const i = f.indexOf(document.activeElement);
    if (e.shiftKey && (i <= 0)) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
  }
  const modalHead = (title, tabsFollow) => h("div", { class: "modal-head" + (tabsFollow ? " tabs-follow" : "") },
    h("div", { class: "modal-title", id: "dlg-title" }, title),
    h("button", { class: "iconbtn", "data-action": "close_modal", "aria-label": "Close", onclick: closeOverlay }, svg(ICON.close, 14)));

  // ---- context menu -----------------------------------------------------
  function openMenu(f, x, y) {
    closeMenu();
    const menu = h("div", { class: "menu", role: "menu", style: { left: Math.min(x, innerWidth - 170) + "px", top: Math.max(8, Math.min(y - 72, innerHeight - 80)) + "px" } },
      h("button", { role: "menuitem", "data-action": "rename_face", "data-param": f.id, onclick: () => { closeMenu(); openRename(f); } }, "Rename"),
      h("button", { role: "menuitem", class: "danger", "data-action": "delete_face", "data-param": f.id, onclick: () => { closeMenu(); confirmDelete(f); } }, "Delete"));
    const scrim = h("div", { class: "menu-scrim", onclick: closeMenu, oncontextmenu: (e) => { e.preventDefault(); closeMenu(); } });
    S.menu = [scrim, menu];
    document.body.append(scrim, menu);
    menu.querySelector("button").focus();
    menu.addEventListener("keydown", (e) => {
      const items = $$("button", menu); const i = items.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
      if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
      if (e.key === "Escape") { e.preventDefault(); closeMenu(); }
    });
  }
  function closeMenu() { if (S.menu) { S.menu.forEach((n) => n.remove()); S.menu = null; } }

  function openRename(f) {
    const input = h("input", { class: "input", value: f.name, maxlength: "40", "data-param": "name", autofocus: true });
    const err = h("div", { class: "err-box", hidden: true });
    const save = async () => {
      const r = await call("rename_face", f.id, input.value);
      if (!r.ok) { err.textContent = r.message || "Couldn't rename"; err.hidden = false; return; }
      const x = S.faces.find((z) => z.id === f.id); if (x) x.name = r.name || input.value.trim();
      renderFaces(); closeOverlay();
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
    openOverlay(h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "dlg-title", style: { width: "400px" } },
      modalHead("Rename face"),
      h("div", { class: "modal-body" }, h("label", { class: "field" }, h("span", { class: "label" }, "Name"), input), err),
      h("div", { class: "modal-foot" },
        h("button", { class: "btn", "data-action": "close_modal", onclick: closeOverlay }, "Cancel"),
        h("button", { class: "btn-primary", "data-action": "rename_face", "data-param": f.id, onclick: save }, "Save"))));
    input.select();
  }

  function confirmDelete(f) {
    const del = async () => {
      const r = await call("delete_face", f.id);
      if (!r.ok) { toastError(r.message || "Couldn't delete"); return; }
      S.faces = S.faces.filter((x) => x.id !== f.id);
      renderFaces(); renderEmpty(); closeOverlay();
    };
    openOverlay(h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "dlg-title", style: { width: "400px" } },
      modalHead(`Delete ${f.name}?`),
      h("div", { class: "modal-body" }, h("div", { style: { color: "var(--text-dim)" } }, "The face is removed from your library.")),
      h("div", { class: "modal-foot" },
        h("button", { class: "btn", "data-action": "close_modal", onclick: closeOverlay, autofocus: true }, "Cancel"),
        h("button", { class: "btn-danger", "data-action": "delete_face", "data-param": f.id, onclick: del }, "Delete"))));
  }

  // ---- import face ------------------------------------------------------
  const ACCEPT = ["image/jpeg", "image/png", "image/webp"];
  function readFile(file) {
    return new Promise((res, rej) => {
      if (!ACCEPT.includes(file.type)) { rej(new Error("Use a JPG, PNG or WEBP image.")); return; }
      if (file.size > 25 * 1024 * 1024) { rej(new Error("That image is too large (max 25 MB).")); return; }
      const r = new FileReader(); r.onload = () => res(r.result); r.onerror = () => rej(new Error("That file could not be read.")); r.readAsDataURL(file);
    });
  }

  function openImport(file) {
    if (S.faces.length >= 12) { toastError("Your library is full (12 faces). Delete one first."); return; }
    const M = { data: null, name: "", consent: false, analysis: null, err: null, busy: false, imgW: 0, imgH: 0 };
    const photo = h("div", { class: "photo" });
    const errBox = h("div", { class: "err-box", hidden: true });
    const nameInput = h("input", { class: "input", "data-param": "name", maxlength: "40", placeholder: "Name" });
    const consent = h("input", { type: "checkbox", "data-param": "consent" });
    const addBtn = h("button", { class: "btn-primary", "data-action": "import_face", disabled: true }, "Add face");

    const valid = () => M.consent && M.analysis && M.analysis.faces.length === 1 && nameInput.value.trim() && !M.busy;
    const sync = () => {
      addBtn.disabled = !valid();
      errBox.hidden = !M.err; errBox.textContent = M.err || "";
    };
    const pick = () => { const fi = $("#file-input"); fi.value = ""; fi.onchange = () => fi.files[0] && load(fi.files[0]); fi.click(); };

    function drawPhoto() {
      if (!M.data) {
        photo.className = "photo drop"; photo.tabIndex = 0; photo.setAttribute("role", "button"); photo.setAttribute("aria-label", "Choose a face photo");
        photo.replaceChildren(svg(ICON.upload, 22), h("div", {}, "Drop a face photo, or click to choose"), h("div", { class: "mono", style: { fontSize: "11px" } }, "JPG · PNG · WEBP"));
        return;
      }
      photo.className = "photo"; photo.removeAttribute("tabindex"); photo.removeAttribute("role"); photo.removeAttribute("aria-label");
      const img = h("img", { src: M.data, alt: "Uploaded photo" });
      const kids = [img, h("span", { class: "ph-label" }, "uploaded photo"),
        h("button", { class: "btn-small ph-replace", "data-action": "replace_photo", onclick: (e) => { e.stopPropagation(); pick(); } }, "Replace")];
      if (M.busy) kids.push(h("div", { class: "busy" }, "Looking for a face…"));
      photo.replaceChildren(...kids);
      if (M.analysis) {
        const place = () => {
          const W = photo.clientWidth, H = photo.clientHeight, iw = M.analysis.width, ih = M.analysis.height;
          const s = Math.min(W / iw, H / ih), ox = (W - iw * s) / 2, oy = (H - ih * s) / 2;
          const bad = M.analysis.faces.length !== 1;
          M.analysis.faces.forEach((f) => {
            const [x1, y1, x2, y2] = f.box;
            photo.append(h("div", { class: "facebox" + (bad ? " bad" : ""), style: { left: ox + x1 * s + "px", top: oy + y1 * s + "px", width: (x2 - x1) * s + "px", height: (y2 - y1) * s + "px" } },
              h("span", {}, "face " + f.score.toFixed(2))));
          });
        };
        img.complete ? requestAnimationFrame(place) : img.addEventListener("load", place);
      }
    }

    async function load(f) {
      if (S.mock && window.__mirageMockFileName) window.__mirageMockFileName(f.name);
      M.err = null; M.analysis = null;
      try { M.data = await readFile(f); } catch (e) { M.err = e.message; sync(); return; }
      if (!nameInput.value.trim()) nameInput.value = f.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").slice(0, 40);
      M.busy = true; drawPhoto(); sync();
      const r = await call("analyze_face", M.data);
      M.busy = false;
      if (!r.ok) { M.err = r.message || "That file could not be read."; M.data = null; }
      else {
        M.analysis = r;
        if (r.faces.length === 0) M.err = "No face found. Try a clear, front-facing photo.";
        else if (r.faces.length > 1) M.err = "Multiple faces found. Use a photo with one person.";
      }
      drawPhoto(); sync();
    }

    photo.addEventListener("click", () => { if (!M.data) pick(); });
    photo.addEventListener("keydown", (e) => { if (!M.data && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); pick(); } });
    photo.addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); photo.classList.add("dragover"); } });
    photo.addEventListener("dragleave", () => photo.classList.remove("dragover"));
    photo.addEventListener("drop", (e) => { e.preventDefault(); e.stopPropagation(); photo.classList.remove("dragover"); const f = e.dataTransfer.files[0]; f && load(f); });
    nameInput.addEventListener("input", sync);
    consent.addEventListener("change", () => { M.consent = consent.checked; sync(); });
    addBtn.addEventListener("click", async () => {
      if (!valid()) return;
      M.busy = true; addBtn.textContent = "Adding…"; sync();
      const r = await call("import_face", M.data, nameInput.value.trim(), M.consent);
      M.busy = false; addBtn.textContent = "Add face";
      if (!r.ok) { M.err = r.message || "Couldn't add that face."; sync(); return; }
      if (r.face && !S.faces.some((x) => x.id === r.face.id)) S.faces.push(r.face);
      renderFaces(); renderEmpty(); closeOverlay();
      toast("Added", r.face ? r.face.name : nameInput.value.trim());
    });

    openOverlay(h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-labelledby": "dlg-title", style: { width: "520px" } },
      modalHead("Add a face"),
      h("div", { class: "modal-body" }, photo, errBox,
        h("label", { class: "field" }, h("span", { class: "label" }, "Name"), nameInput),
        h("label", { class: "check" }, consent, h("span", {}, "This is me, or this person agreed to it"))),
      h("div", { class: "modal-foot" },
        h("button", { class: "btn", "data-action": "close_modal", onclick: closeOverlay }, "Cancel"), addBtn)));
    drawPhoto(); sync();
    const fi = $("#file-input"); fi.value = ""; fi.onchange = () => fi.files[0] && load(fi.files[0]);
    if (file) load(file); else photo.focus();
  }

  // ---- WhatsApp helper ---------------------------------------------------
  function openHelper() {
    const done = () => { call("dismiss_helper"); S.settings.helper_seen = true; closeOverlay(); };
    openOverlay(h("div", { class: "modal helper", role: "dialog", "aria-modal": "true", "aria-label": "Use Mirage in WhatsApp" },
      h("div", { style: { display: "flex", flexDirection: "column", gap: "6px" } },
        h("div", { class: "modal-title" }, "Mirage Camera is live"),
        h("div", { style: { color: "var(--text-dim)" } }, "Pick it as your camera in WhatsApp Desktop.")),
      h("ol", {},
        h("li", {}, h("span", { class: "step" }, "1"), "Open WhatsApp Desktop"),
        h("li", {}, h("span", { class: "step" }, "2"), "Go to Settings → Video"),
        h("li", {}, h("span", { class: "step" }, "3"), "Choose “Mirage Camera”")),
      h("div", { style: { display: "flex", justifyContent: "flex-end" } },
        h("button", { class: "btn-primary", "data-action": "dismiss_helper", onclick: done, autofocus: true }, "Got it"))),
      { onClose: () => call("dismiss_helper") });
  }

  // ---- settings ---------------------------------------------------------
  const HOTKEY_ROWS = [...Array.from({ length: 9 }, (_, i) => ["face_" + (i + 1), "Face " + (i + 1)]),
    ["bypass", "Real camera (bypass)"], ["next_face", "Next face"], ["prev_face", "Previous face"], ["toggle_live", "Start / Stop"]];

  function openSettings(tab = "General") {
    let current = tab, listening = null, benchBusy = false, benchP = 0;
    const body = h("div", { class: "modal-body" });
    const tabbar = h("div", { class: "tabbar", role: "tablist" });
    const srow = (label, control, sub) => h("div", { class: "srow" }, h("div", {}, h("div", {}, label), sub ? h("div", { class: "sub" }, sub) : null), control);
    const switchCtl = (on, onClick, label) => { const b = h("button", { class: "switch", role: "switch", "aria-checked": String(on), "aria-label": label }, h("span", { class: "knob" })); b.addEventListener("click", onClick); return b; };

    async function render() {
      tabbar.replaceChildren(...["General", "Performance", "Hotkeys", "About"].map((t) =>
        h("button", { role: "tab", "aria-selected": String(t === current), "data-param": "settings_tab", "data-value": t, onclick: () => { current = t; listening = null; render(); } }, t)));
      if (current === "General") {
        const cams = S.cameras.length ? S.cameras : [{ id: S.settings.camera ?? 0, name: "Camera " + ((S.settings.camera ?? 0) + 1), resolutions: [] }];
        const cam = cams.find((c) => c.id === S.settings.camera) || cams[0];
        const camSel = h("select", { class: "select wide", "aria-label": "Camera", "data-param": "camera" }, cams.map((c) => h("option", { value: c.id, selected: c.id === S.settings.camera }, c.name)));
        camSel.addEventListener("change", async () => { await setCamera(+camSel.value, null); render(); });
        const resSel = h("select", { class: "select wide mono", "aria-label": "Resolution", "data-param": "resolution" },
          h("option", { value: "", selected: !S.settings.resolution }, "Auto (from mode)"),
          (cam.resolutions || []).map((r) => h("option", { value: r, selected: r === S.settings.resolution }, r.replace("x", "×"))));
        resSel.addEventListener("change", () => setCamera(+camSel.value, resSel.value || null));
        const refresh = h("button", { class: "btn-small", onclick: async () => { refresh.textContent = "…"; const r = await call("list_cameras", true); if (r.ok) S.cameras = r.cameras; render(); } }, "Refresh");
        body.replaceChildren(
          srow("Camera", h("div", { style: { display: "flex", gap: "8px", alignItems: "center" } }, refresh, camSel)),
          srow("Resolution", resSel, "Higher resolutions cost more FPS"),
          srow("Mirror preview", switchCtl(!!S.settings.mirror, async () => { S.settings.mirror = !S.settings.mirror; await call("set_param", "mirror", S.settings.mirror); render(); }, "Mirror preview"), "Only the preview; WhatsApp gets the unmirrored image"));
      } else if (current === "Performance") {
        const tierSel = h("select", { class: "select wide", "aria-label": "Tier", "data-param": "tier" },
          ["Ultra", "Balanced", "Performance"].map((t) => h("option", { value: t, selected: t === S.tier, disabled: t === "Ultra" && S.provider && !S.provider.gpu }, `${t} · ${(S.tiers[t] || {}).expected || ""} FPS`)));
        tierSel.addEventListener("change", async () => { await setTier(tierSel.value); render(); });
        const dmlOk = S.providersAvailable.includes("DmlExecutionProvider");
        const provSel = h("select", { class: "select wide", "aria-label": "Provider", "data-param": "provider" },
          [["auto", "Auto"], ["directml", "DirectML (GPU)"], ["cpu", "CPU"]].map(([v, t]) => h("option", { value: v, selected: v === S.settings.provider, disabled: v === "directml" && !dmlOk && !S.mock }, t)));
        provSel.addEventListener("change", async () => {
          provSel.disabled = true; S.settings.provider = provSel.value; toast("Engine", "Reloading models…", { long: true });
          const r = await call("set_provider", provSel.value);
          if (r.ok && r.provider) { S.provider = r.provider; renderHw(); } else if (!r.ok) toastError(r.message || "Couldn't switch provider");
          render();
        });
        const bar = h("div", { class: "bar", style: { width: "120px" } }, h("div", { style: { width: Math.round(benchP * 100) + "%" } }));
        const rerun = h("button", { class: "btn-small", "data-action": "rerun_benchmark", disabled: benchBusy || S.status !== "ready", onclick: async () => {
          benchBusy = true; benchP = 0; render();
          settingsBenchHook = (p) => { benchP = p; const f = bar.firstChild; if (f) f.style.width = Math.round(p * 100) + "%"; };
          const r = await call("run_benchmark");
          settingsBenchHook = null; benchBusy = false;
          if (r.ok) { S.benchmark = r; EVENTS.tier({ tier: r.tier }); toast("Benchmark", `${r.tier} · ${r.expected_fps} FPS`, { long: true }); }
          else toastError(r.message || "Benchmark failed");
          render();
        } }, benchBusy ? "Running…" : "Re-run");
        const b = S.benchmark;
        const sub = b ? `Last result: ${b.tier} · swap ${b.ms.swap}ms · detect ${b.ms.detect}ms` : "Takes about 3 seconds";
        body.replaceChildren(
          srow("Tier", tierSel, "Ultra needs a GPU"),
          srow("Provider", provSel, S.provider ? (S.provider.gpu ? `In use: DirectML · ${S.provider.device}` : "In use: CPU") : ""),
          srow("Benchmark", h("div", { style: { display: "flex", gap: "10px", alignItems: "center" } }, benchBusy ? bar : null, rerun), sub),
          ...(S.asyncSwap ? [h("div", { class: "note" }, "On CPU the face swap runs in the background at its own rate, while tracking, lighting and hands update every frame. Expressions may lag slightly; a GPU removes the lag.")] : []));
      } else if (current === "Hotkeys") {
        const rows = HOTKEY_ROWS.map(([action, label]) => {
          const combo = S.settings.hotkeys[action];
          const cap = h("button", { class: "val keycap" + (listening === action ? " listening" : ""), "data-param": "hotkey_" + action, "aria-label": `${label}: ${prettyCombo(combo)}. Press to change.` },
            listening === action ? "Press keys…" : prettyCombo(combo));
          cap.addEventListener("click", () => { listening = action; render(); setTimeout(() => { const el = body.querySelector(".keycap.listening"); el && el.focus(); }, 0); });
          cap.addEventListener("keydown", async (e) => {
            if (listening !== action) return;
            if (e.key === "Tab") return;
            e.preventDefault(); e.stopPropagation();
            if (e.key === "Escape") { listening = null; render(); return; }
            let next;
            if (e.key === "Backspace" || e.key === "Delete") next = "";
            else {
              const k = keyName(e); if (!k) return;
              const mods = [e.ctrlKey && "ctrl", e.altKey && "alt", e.shiftKey && "shift", e.metaKey && "windows"].filter(Boolean);
              if (!mods.length) { toastError("Use at least one modifier, e.g. Ctrl+Alt"); return; }
              next = [...mods, k].join("+");
            }
            const r = await call("set_hotkey", action, next);
            if (!r.ok) { toastError(r.message || "Couldn't set hotkey"); return; }
            S.settings.hotkeys = r.hotkeys || Object.assign({}, S.settings.hotkeys, { [action]: next });
            listening = null; render(); renderHotkeyHint(); renderFaces();
          });
          return srow(label, cap);
        });
        body.replaceChildren(...(S.hotkeysAvailable ? [] : [h("div", { class: "note", style: { color: "var(--warn)" } }, "Global hotkeys aren't available on this system.")]),
          ...rows, h("div", { class: "note" }, "Hotkeys work while WhatsApp has focus. Click a key to change it; Backspace clears; Esc cancels."));
      } else {
        const lic = h("div", { class: "licences", hidden: true });
        const view = h("button", { class: "btn-small", onclick: async () => { const r = await call("open_licences"); lic.textContent = r.text || ""; lic.hidden = !lic.hidden; } }, "View");
        const drv = S.vcam && S.vcam.disabled ? "Disabled (--no-vcam)" : (S.error && S.error.code === "vcam_missing" ? "Not installed" : "Installed");
        body.replaceChildren(
          srow("Version", h("span", { class: "val mono" }, S.version || "—")),
          srow("Virtual camera driver", h("span", { class: "val", style: { color: drv === "Not installed" ? "var(--danger)" : "var(--text)" } }, drv), "Shows up as “Mirage Camera”"),
          srow("Licences", view, "Fonts, models and libraries"), lic,
          h("div", { class: "note" }, "Only use faces you own or have permission to use. The bundled inswapper model is licensed for non-commercial use."));
      }
    }
    openOverlay(h("div", { class: "modal settings", role: "dialog", "aria-modal": "true", "aria-labelledby": "dlg-title" }, modalHead("Settings", true), tabbar, body),
      { onClose: () => { settingsBenchHook = null; } });
    render();
  }
  let settingsBenchHook = null;
  function keyName(e) {
    const k = e.key;
    if (["Control", "Alt", "Shift", "Meta", "OS", "AltGraph"].includes(k)) return null;
    if (/^Digit\d$/.test(e.code)) return e.code.slice(5);
    if (/^Key[A-Z]$/.test(e.code)) return e.code.slice(3).toLowerCase();
    const map = { ArrowRight: "right", ArrowLeft: "left", ArrowUp: "up", ArrowDown: "down", " ": "space", PageUp: "page up", PageDown: "page down" };
    if (map[k]) return map[k];
    if (/^F\d{1,2}$/.test(k)) return k.toLowerCase();
    return k.length === 1 ? k.toLowerCase() : k.toLowerCase();
  }

  // ---- splash / first launch ----------------------------------------------
  function openSplash() {
    if (S.splash) return;
    const word = h("div", { class: "big", "aria-label": "MIRAGE" }, "MIRAGE".split("").map((c, i) => h("span", { style: { animationDelay: i * 0.12 + "s" } }, c)));
    const fill = h("div", { style: { width: "4%" } });
    const bar = h("div", { class: "bar" }, fill);
    const label = h("span", { class: "mono", id: "splash-prov" }, "");
    const progress = h("div", { class: "progress", "aria-live": "polite" }, h("div", { class: "progress-head" }, h("span", { id: "splash-msg" }, "Checking your hardware…"), label), bar);
    const el = h("div", { class: "splash", role: "dialog", "aria-label": "Setting up Mirage" }, word, progress);
    document.body.append(el);
    S.splash = { el, fill, label, progress, mode: "progress" };
  }
  function onBenchmark(d) {
    if (settingsBenchHook) { settingsBenchHook(d.progress || 0); if (d.stage === "done") settingsBenchHook(1); return; }
    if (!S.splash) openSplash();
    const sp = S.splash;
    if (d.provider) sp.label.textContent = d.provider.gpu ? "GPU · DirectML" : "CPU";
    sp.fill.style.width = Math.max(4, Math.round((d.progress || 0) * 100)) + "%";
    if (d.stage === "done" && d.result) showResult(d.result);
  }
  function showResult(r) {
    const sp = S.splash; if (!sp || sp.mode === "result") return;
    sp.mode = "result";
    const cont = h("button", { class: "btn-continue", "data-action": "continue", autofocus: true, onclick: () => closeSplash() }, "CONTINUE");
    const change = h("button", { class: "link", "data-action": "change_tier", style: { fontSize: "12px" }, onclick: () => { closeSplash(); openSettings("Performance"); } }, "Change");
    const card = h("div", { class: "result" },
      h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } }, h("div", { class: "label" }, "Result"), change),
      h("div", { class: "grid" },
        h("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } }, h("span", { class: "k" }, "Tier"), h("span", { class: "v" }, r.tier)),
        h("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } }, h("span", { class: "k" }, "Expected FPS"), h("span", { class: "v mono" }, r.expected_fps))),
      r.provider && !r.provider.gpu ? h("div", { class: "foot" }, "No compatible GPU found, so Mirage runs on the CPU. The swap updates in the background; motion stays smooth.") : null,
      cont);
    $("#splash-msg").textContent = "Hardware checked";
    sp.el.append(card);
    cont.focus();
  }
  function closeSplash() { if (S.splash) { S.splash.el.remove(); S.splash = null; } }

  // ------------------------------------------------------------------ global input
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-action]"); if (!t) return;
    switch (t.dataset.action) {
      case "open_settings": openSettings(); break;
      case "minimize": call("window_minimize"); break;
      case "maximize": call("window_toggle_maximize"); break;
      case "close": call("window_close"); break;
      case "collapse_panel": S.collapsedManual = true; renderCollapsed(); break;
      case "expand_panel": S.collapsedManual = false; renderCollapsed(); if (t.dataset.group) { const g = $("#group-" + t.dataset.group); g && g.scrollIntoView({ block: "start" }); } break;
      case "start": case "stop": toggleLive(); break;
      case "fix_error": fixError(); break;
      case "dismiss_error": clearError(); break;
      case "import_face": if (t.id === "dropzone") openImport(); break;
      default: break;
    }
  });

  $$("#viewtabs button").forEach((b) => b.addEventListener("click", () => { S.view = b.dataset.value; renderPreviewFlags(); call("set_param", "view", S.view); }));
  $("#split-range").addEventListener("input", (e) => { S.split = +e.target.value; renderPreviewFlags(); });

  document.addEventListener("keydown", (e) => {
    trapFocus(e);
    if (e.key === "Escape") { if (S.menu) { closeMenu(); return; } if (overlay.firstChild) { closeOverlay(); return; } }
    if (overlay.firstChild || S.splash) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (["input", "select", "textarea"].includes(tag) && e.target.type !== "range") return;
    if (S.mock && e.ctrlKey && e.altKey && e.code === "Digit0") { e.preventDefault(); S.api.__bypass(); return; }
    if (!e.ctrlKey && !e.altKey && !e.metaKey && /^Digit[1-9]$/.test(e.code)) {
      const i = +e.code.slice(5) - 1;
      if (S.faces[i]) { e.preventDefault(); selectFace(S.faces[i].id); }
    }
  });

  // Window-level drag & drop of a photo opens the import dialog.
  const strip = $("#strip");
  document.addEventListener("dragover", (e) => { if (e.dataTransfer && e.dataTransfer.types.includes("Files")) { e.preventDefault(); strip.classList.add("dragover"); } });
  document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) strip.classList.remove("dragover"); });
  document.addEventListener("drop", (e) => {
    strip.classList.remove("dragover");
    if (!e.dataTransfer || !e.dataTransfer.files.length) return;
    e.preventDefault();
    if (!overlay.firstChild) openImport(e.dataTransfer.files[0]);
  });

  window.addEventListener("resize", renderCollapsed);

  // ------------------------------------------------------------------ boot
  function initStatic() {
    $("#wordmark").replaceChildren(..."MIRAGE".split("").map((c, i) => h("span", { style: { animationDelay: i * 0.12 + "s" } }, c)));
    renderAll();
  }

  async function connect() {
    if (window.pywebview && window.pywebview.api) { S.api = window.pywebview.api; S.mock = false; }
    else { S.api = window.MirageMock.api; S.mock = true; }
    const st = await call("get_state");
    if (st.ok !== false) applyState(st);
    S.firstRun = !S.tier;
    if (S.firstRun && S.status !== "ready") openSplash();
    renderAll();
    if (S.status === "ready" && !S.cameras.length && !S.mock) call("list_cameras").then((r) => { if (r.ok) { S.cameras = r.cameras; renderControls(); } });
    if (S.mock) {
      if (window.MirageMock.scenario === "collapsed") { S.collapsedManual = true; renderCollapsed(); }
      window.MirageMock.boot();
    }
  }

  initStatic();
  // pywebview injects its API asynchronously; give it a moment before falling back to mock mode.
  let connected = false;
  const go = () => { if (!connected) { connected = true; connect(); } };
  window.addEventListener("pywebviewready", go);
  if (window.pywebview && window.pywebview.api) go();
  // Opened as a file (browser review) -> mock quickly; served over http (pywebview's local
  // server) -> wait for the real bridge, and only fall back to mock if it never arrives.
  else setTimeout(go, location.protocol === "file:" ? 300 : 4000);

  // Ready event may arrive before cameras are listed: fetch them once ready.
  const origReady = EVENTS.ready;
  EVENTS.ready = (st) => {
    origReady(st);
    if (!S.mock && !S.cameras.length) call("list_cameras").then((r) => { if (r.ok) { S.cameras = r.cameras; renderControls(); } });
  };
})();
