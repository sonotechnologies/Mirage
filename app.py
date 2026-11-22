"""Mirage entry point.

    python app.py                         # normal: window + webcam + virtual camera
    python app.py --video clip.mp4        # use a video file instead of the webcam
    python app.py --no-vcam               # don't open the virtual camera
    python app.py --headless --video clip.mp4 --no-vcam --face me.jpg --consent --seconds 20
                                          # no window: run the pipeline and print FPS / timings
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
from collections import OrderedDict

import paths

log = logging.getLogger("mirage")


class WindowEmitter:
    """Delivers engine events to the page via evaluate_js on a dedicated thread.

    High-rate events (preview, stats, benchmark_progress) are coalesced so a slow
    page never backs up the engine; everything else is delivered in order.
    """

    COALESCE = {"preview", "stats", "benchmark_progress"}

    def __init__(self):
        self.window = None
        self._cv = threading.Condition()
        self._latest: OrderedDict[str, dict] = OrderedDict()
        self._queue: list[tuple[str, dict]] = []
        self._ready = False
        threading.Thread(target=self._run, name="mirage-emit", daemon=True).start()

    def __call__(self, event: str, data: dict) -> None:
        with self._cv:
            if event in self.COALESCE:
                self._latest[event] = data
            else:
                self._queue.append((event, data))
            self._cv.notify()

    def set_ready(self) -> None:
        with self._cv:
            self._ready = True
            self._cv.notify()

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._ready or (not self._queue and not self._latest):
                    self._cv.wait()
                batch = self._queue
                self._queue = []
                batch += list(self._latest.items())
                self._latest.clear()
            for event, data in batch:
                js = f"window.mirage&&window.mirage.on({json.dumps(event)},{json.dumps(data)})"
                try:
                    self.window.evaluate_js(js)
                except Exception as e:  # noqa: BLE001 — window closing
                    log.debug("emit %s failed: %s", event, e)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="mirage", description="Live face swap to a virtual camera.")
    ap.add_argument("--video", help="read frames from this video file instead of the webcam (loops)")
    ap.add_argument("--no-vcam", action="store_true", help="do not open the virtual camera")
    ap.add_argument("--headless", action="store_true", help="no window; run the pipeline and log stats")
    ap.add_argument("--seconds", type=float, default=15, help="headless run length")
    ap.add_argument("--face", help="headless: import this photo and swap it in")
    ap.add_argument("--consent", action="store_true", help="headless: confirm the face is yours or the person agreed")
    ap.add_argument("--tier", choices=["Ultra", "Balanced", "Performance"], help="override the performance tier")
    ap.add_argument("--provider", choices=["auto", "directml", "cpu"], help="override the inference provider")
    ap.add_argument("--live", action="store_true", help="headless: also go live (send to the virtual camera)")
    ap.add_argument("--record", help="headless: write the processed output to this .mp4")
    ap.add_argument("--debug", action="store_true", help="window: enable devtools; verbose logs")
    return ap.parse_args(argv)


def setup_logging(debug: bool) -> None:
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(paths.user_dir() / "mirage.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def apply_overrides(core, args) -> None:
    if args.provider:
        core.settings.set("provider", args.provider)
    if args.tier:
        core.settings.set("tier", args.tier)


def run_headless(args) -> int:
    from core import MirageCore

    events: dict[str, int] = {}
    last_stats: dict = {}

    def emit(event, data):
        events[event] = events.get(event, 0) + 1
        if event == "stats":
            last_stats.update(data)
        elif event == "benchmark_progress" and data.get("stage") == "done":
            log.info("benchmark: %s", json.dumps(data.get("result")))
        elif event not in ("preview",):
            log.info("event %s %s", event, json.dumps(data)[:300])

    core = MirageCore(emit=emit, video=args.video, no_vcam=args.no_vcam, enable_hotkeys=False)
    apply_overrides(core, args)
    core.boot()
    if core.status != "ready":
        log.error("boot failed: %s", core.load_error)
        return 2
    log.info("ready after %.1fs", time.perf_counter() - core.t_start)
    if args.face:
        existing = {d["name"]: d["id"] for d in core.library.items}
        name = os.path.splitext(os.path.basename(args.face))[0][:40]
        if name in existing:
            core.select_face(existing[name])
        else:
            r = core.import_face(args.face, name, args.consent)
            core.select_face(r["face"]["id"])
    if args.live:
        log.info("go live: %s", core.start())

    writer = None
    if args.record:
        import cv2
        p = core.pipeline
        orig_put = p._out.put

        def tee(item):
            nonlocal writer
            frame = item[1]
            if writer is None:
                h, w = frame.shape[:2]
                writer = cv2.VideoWriter(args.record, cv2.VideoWriter_fourcc(*"mp4v"), 25, (w, h))
            writer.write(frame)
            orig_put(item)
        p._out.put = tee

    t_end = time.perf_counter() + args.seconds
    try:
        while time.perf_counter() < t_end:
            time.sleep(1.0)
            s = core.pipeline.stats()
            log.info("fps %.1f (capture %.1f, out %.1f)  proc %.1fms  latency %.1fms  face=%s  stages=%s",
                     s["fps"], s["capture_fps"], s["output_fps"], s["ms"], s["latency_ms"], s["face"], s["stages"])
    except KeyboardInterrupt:
        pass
    summary = {"stats": core.pipeline.stats(), "frames_processed": core.pipeline.frames_processed,
               "events": events, "tier": core.tier.name, "async_swap": core.pipeline.processor.async_mode}
    core.shutdown()
    if writer is not None:
        writer.release()
    print(json.dumps(summary, indent=2))
    return 0


def run_window(args) -> int:
    import webview

    from bridge.api import Api
    from core import MirageCore

    emitter = WindowEmitter()
    core = MirageCore(emit=emitter, video=args.video, no_vcam=args.no_vcam)
    apply_overrides(core, args)
    holder: dict = {}
    api = Api(core, window_ref=lambda: holder.get("w"))
    window = webview.create_window(
        "Mirage", url=str(paths.ui_dir() / "index.html"), js_api=api,
        width=1280, height=800, min_size=(1024, 640), frameless=True, easy_drag=False,
        background_color="#0A0B0E")
    holder["w"] = window
    emitter.window = window

    def on_loaded():
        emitter.set_ready()

    window.events.loaded += on_loaded
    window.events.closed += lambda: core.shutdown()
    core.boot_async()   # load models while the window paints
    # http_server=True serves ui/ from 127.0.0.1 (fonts and fetches behave like a normal page).
    webview.start(gui="edgechromium" if os.name == "nt" else None, debug=args.debug, http_server=True,
                  private_mode=False, storage_path=str(paths.user_dir() / "webview"))
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(args.debug)
    if args.headless:
        return run_headless(args)
    return run_window(args)


if __name__ == "__main__":
    raise SystemExit(main())
