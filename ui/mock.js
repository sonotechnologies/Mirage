/* Mock bridge: used when window.pywebview is absent (plain browser review).
 * Implements the same methods as bridge/api.py and pushes the same events
 * through window.mirage.on(event, data). Fake faces, FPS and a canvas preview.
 *
 * Review scenarios via the URL:  index.html?scenario=<name>
 *   live | empty | noface | lowperf | error-camera | error-driver | first-run | collapsed
 */
(function () {
  "use strict";
  const params = new URLSearchParams(location.search);
  const scenario = params.get("scenario") || "";

  const HUES = { f1: 160, f2: 280, f3: 30, f4: 210, f5: 340 };
  let faces = scenario === "empty" ? [] : [
    { id: "f1", name: "Tolu" }, { id: "f2", name: "Ada" }, { id: "f3", name: "Kemi" },
    { id: "f4", name: "Marcus" }, { id: "f5", name: "Inès" },
  ];
  const TIERS = {
    Ultra: { name: "Ultra", width: 1920, height: 1080, enhancer_allowed: true, expected: "30" },
    Balanced: { name: "Balanced", width: 1280, height: 720, enhancer_allowed: true, expected: "25–30" },
    Performance: { name: "Performance", width: 854, height: 480, enhancer_allowed: false, expected: "15–20" },
  };
  const HOTKEYS = { bypass: "ctrl+alt+0", next_face: "ctrl+alt+right", prev_face: "ctrl+alt+left", toggle_live: "ctrl+alt+s" };
  for (let n = 1; n <= 9; n++) HOTKEYS["face_" + n] = "ctrl+alt+" + n;

  const S = {
    live: false,
    active: faces[0] ? faces[0].id : null,
    tier: scenario === "first-run" ? null : (scenario === "lowperf" ? "Balanced" : "Balanced"),
    provider: "auto",
    camera: 0, resolution: null, mirror: true,
    params: { swap: true, blend: 60, sharpness: 30, colour: 70, enhancer: false, occlusion: true },
    view: "Mirage",
    hotkeys: HOTKEYS,
    helperSeen: false,
    status: "loading",
  };
  const cameras = [
    { id: 0, name: "Integrated Webcam", resolutions: ["1280x720", "854x480", "640x480"] },
    { id: 1, name: "Logitech C920", resolutions: ["1920x1080", "1280x720", "854x480"] },
  ];

  const emit = (e, d) => setTimeout(() => window.mirage && window.mirage.on(e, d), 0);
  const ok = (x) => Promise.resolve(Object.assign({ ok: true }, x || {}));
  const fail = (error, message) => Promise.resolve({ ok: false, error, message });
  const faceList = () => faces.map((f, i) => ({ id: f.id, name: f.name, hotkey: i < 9 ? i + 1 : null, thumb: f.thumb || "", hue: HUES[f.id] ?? (f.hue || 200) }));
  const providerInfo = () => S.provider === "cpu" ? { kind: "CPU", device: "CPU", gpu: false } : { kind: "DirectML", device: "Intel Iris Xe Graphics", gpu: true };

  // ---- fake preview frames -------------------------------------------------
  const canvas = document.createElement("canvas"); canvas.width = 640; canvas.height = 360;
  const ctx = canvas.getContext("2d");
  function frame(label, hue, real, t) {
    const g = ctx.createRadialGradient(320, 160, 20, 320, 200, 380);
    g.addColorStop(0, `hsl(${hue} 18% 26%)`); g.addColorStop(1, "#0d0e12");
    ctx.fillStyle = g; ctx.fillRect(0, 0, 640, 360);
    const dx = Math.sin(t / 900) * 10, dy = Math.cos(t / 1300) * 4;
    ctx.fillStyle = `hsla(${hue} 20% 60% / .22)`;
    ctx.beginPath(); ctx.ellipse(320 + dx, 165 + dy, 70, 88, 0, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.ellipse(320 + dx * .5, 380, 170, 120, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "rgba(232,234,240,.55)"; ctx.font = '500 13px "JetBrains Mono", monospace'; ctx.textAlign = "center";
    ctx.fillText(real ? "MOCK · REAL WEBCAM" : "MOCK · " + label.toUpperCase(), 320, 340);
    return canvas.toDataURL("image/jpeg", 0.8);
  }
  const noFace = () => scenario === "noface";
  function tick() {
    if (S.status !== "ready") return;
    const t = performance.now();
    const f = faces.find((x) => x.id === S.active);
    const swapping = S.params.swap && f && !noFace();
    const real = frame("", 220, true, t);
    const proc = swapping ? frame(f.name, HUES[f.id] ?? f.hue ?? 200, false, t) : real;
    const data = { frame: S.view === "Original" ? real : proc };
    if (S.view === "Split") data.original = real;
    emit("preview", data);
  }
  let statsT = 0;
  function stats() {
    if (S.status !== "ready") return;
    const low = scenario === "lowperf" && S.tier !== "Performance";
    const base = low ? 11 : S.tier === "Performance" ? 19 : S.tier === "Ultra" ? 30 : 28;
    const fps = Math.max(1, base + Math.round(Math.random() * 3 - 1.5));
    emit("stats", { fps, capture_fps: 30, output_fps: S.live ? 30 : 0, ms: Math.round(1000 / fps) + 2,
      latency_ms: Math.round(1000 / fps) + 6, stages: { detect: 4.1, swap: 18.2, colour: 1.1, paste: 1.4 },
      live: S.live, face: noFace() ? false : true, provider: providerInfo(), tier: S.tier, swap_fps: null });
    statsT++;
  }

  function state() {
    return {
      status: S.status, error: null, version: "1.0.0 (mock)",
      settings: { camera: S.camera, resolution: S.resolution, mirror: S.mirror, tier: S.tier, provider: S.provider,
        params: Object.assign({}, S.params), hotkeys: Object.assign({}, S.hotkeys), helper_seen: S.helperSeen, active_face: S.active },
      faces: faceList(), cameras, tiers: TIERS, tier: S.tier,
      benchmark: S.tier ? { tier: S.tier, expected_fps: TIERS[S.tier].expected, ms: { detect: 4, swap: 18, occlude: 6, enhance: 31 }, provider: providerInfo() } : null,
      provider: providerInfo(), providers_available: ["DmlExecutionProvider", "CPUExecutionProvider"],
      live: S.live, vcam: { name: "Mirage Camera", disabled: false }, hotkeys_available: true, async_swap: S.provider === "cpu",
      mock: true,
    };
  }

  async function benchmark() {
    S.status = "benchmarking";
    const stages = ["detect", "swap", "occlude", "enhance"];
    for (let i = 0; i <= 30; i++) {
      await new Promise((r) => setTimeout(r, 100));
      emit("benchmark_progress", { progress: i / 30, stage: stages[Math.min(3, Math.floor(i / 8))], provider: providerInfo() });
    }
    S.tier = S.provider === "cpu" ? "Performance" : "Balanced";
    const res = { tier: S.tier, expected_fps: TIERS[S.tier].expected, ms: { detect: 4, swap: 18, occlude: 6, enhance: 31 }, provider: providerInfo(), swap_fps: 55 };
    emit("benchmark_progress", { progress: 1, stage: "done", result: res });
    return res;
  }

  async function boot() {
    await new Promise((r) => setTimeout(r, 350));
    if (!S.tier) await benchmark();
    S.status = "ready";
    if (scenario === "live") S.live = true;
    emit("ready", state());
    emit("source", { label: "mock", width: 1280, height: 720 });
    if (scenario === "error-camera") emit("error", { code: "camera_in_use", message: "Camera 0 could not be opened (in use by another app or unplugged)." });
    if (scenario === "error-driver") emit("error", { code: "vcam_missing", message: "Virtual camera driver not available." });
    if (noFace()) emit("face_lost", {});
    setInterval(tick, 1000 / 15);
    setInterval(stats, 500);
  }

  function readName(dataUrl) { return dataUrl.length; }

  // Fake detection: the mock "finds" one face unless the file name says otherwise.
  let lastFileName = "";
  window.__mirageMockFileName = (n) => { lastFileName = (n || "").toLowerCase(); };

  const api = {
    get_state: () => ok(state()),
    list_cameras: () => ok({ cameras }),
    set_camera: (id, resolution) => { S.camera = +id; S.resolution = resolution || null; return ok({ size: [1280, 720] }); },
    start: () => {
      if (scenario === "error-driver") { emit("error", { code: "vcam_missing", message: "Virtual camera driver not available." }); return fail("vcam_missing", "Virtual camera driver not available."); }
      S.live = true; const first = !S.helperSeen; emit("live", { live: true, device: "Mirage Camera" });
      return ok({ device: "Mirage Camera", show_helper: first });
    },
    stop: () => { S.live = false; emit("live", { live: false }); return ok(); },
    analyze_face: (data) => {
      readName(data);
      const n = lastFileName;
      const faces_ = n.includes("group") || n.includes("multi") ? [{ box: [80, 60, 220, 240], score: 0.97 }, { box: [300, 70, 430, 240], score: 0.95 }]
        : n.includes("noface") || n.includes("landscape") ? [] : [{ box: [190, 80, 330, 270], score: 0.98 }];
      return new Promise((r) => setTimeout(() => r({ ok: true, width: 520, height: 360, faces: faces_ }), 250));
    },
    import_face: (data, name, consent) => {
      if (!consent) return fail("consent_required", "Confirm this is you, or that this person agreed.");
      if (faces.length >= 12) return fail("library_full", "Your library is full (12 faces). Delete one first.");
      const id = "m" + Math.random().toString(36).slice(2, 8);
      const f = { id, name: (name || "Face").slice(0, 40), thumb: data, hue: Math.floor(Math.random() * 360) };
      faces.push(f);
      if (!S.active) { S.active = id; emit("face_selected", { id, index: faces.length - 1, name: f.name }); }
      emit("faces", { faces: faceList() });
      return ok({ face: faceList().find((x) => x.id === id) });
    },
    rename_face: (id, name) => { const f = faces.find((x) => x.id === id); if (!f) return fail("not_found", "That face no longer exists."); if (!name.trim()) return fail("bad_name", "Name can't be empty."); f.name = name.trim().slice(0, 40); emit("faces", { faces: faceList() }); return ok({ id, name: f.name }); },
    delete_face: (id) => {
      const i = faces.findIndex((x) => x.id === id); if (i < 0) return fail("not_found", "That face no longer exists.");
      faces.splice(i, 1);
      if (S.active === id) { S.active = faces[0] ? faces[0].id : null; emit("face_selected", { id: S.active, index: S.active ? 0 : -1, name: S.active ? faces[0].name : null }); }
      emit("faces", { faces: faceList() }); return ok();
    },
    reorder_faces: (ids) => { faces.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id)); emit("faces", { faces: faceList() }); return ok(); },
    select_face: (id) => { const i = faces.findIndex((x) => x.id === id); S.active = id; const d = { id, index: i, name: i >= 0 ? faces[i].name : null }; emit("face_selected", d); return ok(d); },
    set_param: (key, value) => {
      if (key === "mirror") { S.mirror = !!value; return ok(); }
      if (key === "view") { S.view = value; return ok(); }
      if (key === "enhancer" && value && !TIERS[S.tier].enhancer_allowed) return fail("enhancer_disabled", "The enhancer is off on the Performance tier.");
      S.params[key] = value; return ok({ key, value });
    },
    set_tier: (tier) => { S.tier = tier; if (!TIERS[tier].enhancer_allowed) S.params.enhancer = false; emit("tier", { tier }); return ok({ tier }); },
    run_benchmark: async () => { const r = await benchmark(); S.status = "ready"; emit("tier", { tier: r.tier }); return Object.assign({ ok: true }, r); },
    set_provider: async (p) => { S.provider = p; await new Promise((r) => setTimeout(r, 400)); emit("ready", state()); return ok({ provider: providerInfo() }); },
    set_hotkey: (action, combo) => {
      const clash = Object.entries(S.hotkeys).find(([a, c]) => a !== action && c === combo && combo);
      if (clash) return fail("hotkey_in_use", `${combo} is already used by ${clash[0]}`);
      S.hotkeys[action] = combo || ""; return ok({ hotkeys: Object.assign({}, S.hotkeys) });
    },
    fix_error: (code) => new Promise((r) => setTimeout(() => r({ ok: true }), 500)),
    dismiss_helper: () => { S.helperSeen = true; return ok(); },
    open_licences: () => ok({ text: "Mock mode — licences ship in LICENSES.md." }),
    window_minimize: () => ok(), window_toggle_maximize: () => ok(), window_close: () => ok(),
    // mock-only: Ctrl+Alt+0 in the browser
    __bypass: () => { S.params.swap = !S.params.swap; emit("param", { key: "swap", value: S.params.swap }); },
  };

  window.MirageMock = { api, boot, scenario };
})();
