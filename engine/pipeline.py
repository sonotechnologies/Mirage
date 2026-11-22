"""Thread orchestration: capture -> process -> output (virtual camera + UI preview).

Every hand-off is a size-1 "latest" slot, so a slow stage drops stale frames
instead of building lag. The output thread runs at a steady rate and repeats
the last processed frame when processing is slower than capture.
"""
from __future__ import annotations

import base64
import logging
import threading
import time
from collections import deque
from typing import Callable

import cv2
import numpy as np

from .capture import CameraInUse
from .processor import Processor
from .providers import Tier, provider_label
from .vcam import VCamMissing

log = logging.getLogger("mirage.pipeline")

Emit = Callable[[str, dict], None]


class Latest:
    """A one-slot queue: put() overwrites, get() waits for something newer than last seen."""

    def __init__(self):
        self._cv = threading.Condition()
        self._item = None
        self._seq = 0

    def put(self, item) -> None:
        with self._cv:
            self._item = item
            self._seq += 1
            self._cv.notify_all()

    def get(self, after: int, timeout: float = 0.5):
        with self._cv:
            if self._seq <= after:
                self._cv.wait(timeout)
            if self._seq <= after:
                return after, None
            return self._seq, self._item

    def peek(self):
        with self._cv:
            return self._seq, self._item


class Rate:
    """Events per second over a sliding window."""

    def __init__(self, window: float = 2.0):
        self.window = window
        self.ts: deque[float] = deque()

    def tick(self, t: float | None = None) -> None:
        t = time.perf_counter() if t is None else t
        self.ts.append(t)
        while self.ts and t - self.ts[0] > self.window:
            self.ts.popleft()

    def value(self) -> float:
        if len(self.ts) < 2:
            return 0.0
        span = self.ts[-1] - self.ts[0]
        return (len(self.ts) - 1) / span if span > 0 else 0.0


class Pipeline:
    def __init__(self, models, tier: Tier, emit: Emit, source_factory: Callable[[], object],
                 vcam_factory: Callable[[int, int, float], object], out_fps: float = 30.0,
                 preview_fps: float = 15.0, preview_width: int = 640):
        self.models = models
        self.processor = Processor(models, tier)
        self.emit = emit
        self.source_factory = source_factory
        self.vcam_factory = vcam_factory
        self.out_fps = out_fps
        self.preview_fps = min(15.0, preview_fps)
        self.preview_width = preview_width
        self.mirror_preview = True
        self.view = "Mirage"            # Mirage | Original | Split  (preview only)
        self.preview_enabled = True

        self._raw = Latest()
        self._out = Latest()
        self._proc_lock = threading.Lock()
        self._src_lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.source = None
        self.vcam = None
        self.live = False
        self.size = (0, 0)

        self.cap_rate, self.proc_rate, self.out_rate = Rate(), Rate(), Rate()
        self.stage_ms: dict[str, float] = {}
        self.proc_ms = 0.0
        self.latency_ms = 0.0
        self._face_state: bool | None = None
        self._miss = 0
        self.frames_processed = 0

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        """Open the source and start all three threads (preview runs; virtual cam stays off)."""
        self._open_source()
        self._stop.clear()
        for name, fn in (("capture", self._capture_loop), ("process", self._process_loop),
                         ("output", self._output_loop)):
            t = threading.Thread(target=fn, name=f"mirage-{name}", daemon=True)
            t.start()
            self._threads.append(t)

    def shutdown(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2)
        self._threads.clear()
        self.processor.close()
        self.stop_live()
        with self._src_lock:
            if self.source:
                self.source.close()
                self.source = None

    def _open_source(self) -> None:
        with self._src_lock:
            if self.source:
                self.source.close()
                self.source = None
            src = self.source_factory()
            try:
                src.open()
            except CameraInUse as e:
                self.emit("error", {"code": "camera_in_use", "message": str(e)})
                return
            except Exception as e:  # noqa: BLE001
                self.emit("error", {"code": "camera_failed", "message": str(e)})
                return
            self.source = src
            self.size = (src.width, src.height)
            self.emit("source", {"label": src.label, "width": src.width, "height": src.height})

    def restart_source(self, source_factory: Callable[[], object] | None = None) -> bool:
        if source_factory:
            self.source_factory = source_factory
        was_live = self.live
        self._open_source()
        if was_live and self.vcam and self.size != (self.vcam.width, self.vcam.height):
            self.stop_live()
            self.go_live()
        return self.source is not None

    def go_live(self) -> dict:
        if self.live:
            return {"ok": True, "device": self.vcam.device}
        w, h = self.size if self.size[0] else (1280, 720)
        cam = self.vcam_factory(w, h, self.out_fps)
        try:
            cam.open()
        except VCamMissing as e:
            self.emit("error", {"code": "vcam_missing", "message": str(e)})
            return {"ok": False, "error": "vcam_missing", "message": str(e)}
        self.vcam = cam
        self.live = True
        return {"ok": True, "device": cam.device}

    def stop_live(self) -> None:
        self.live = False
        cam, self.vcam = self.vcam, None
        if cam:
            cam.close()

    # -- configuration --------------------------------------------------
    def set_param(self, key: str, value) -> None:
        with self._proc_lock:
            self.processor.params.update(key, value)

    def set_latent(self, latent: np.ndarray | None) -> None:
        # Pointer swap only: applies on the very next processed frame.
        self.processor.set_latent(latent)
        self._face_state = None

    def set_tier(self, tier: Tier) -> None:
        with self._proc_lock:
            self.processor.set_tier(tier)

    # -- threads --------------------------------------------------------
    def _capture_loop(self) -> None:
        fails = 0
        while not self._stop.is_set():
            with self._src_lock:
                src = self.source
                frame = src.read() if src else None
            if frame is None:
                if src is not None and getattr(src, "finished", False):
                    time.sleep(0.05)
                    continue
                fails += 1
                if src is not None and fails == 60:
                    self.emit("error", {"code": "camera_lost", "message": "The camera stopped sending frames."})
                time.sleep(0.03 if src else 0.2)
                continue
            fails = 0
            t = time.perf_counter()
            self.cap_rate.tick(t)
            self._raw.put((frame, t))

    def _process_loop(self) -> None:
        seq = 0
        while not self._stop.is_set():
            seq, item = self._raw.get(seq, timeout=0.25)
            if item is None:
                continue
            frame, t_cap = item
            t0 = time.perf_counter()
            with self._proc_lock:
                try:
                    res = self.processor.process(frame, t_cap)
                except Exception as e:  # never freeze: pass the raw frame through
                    log.exception("process failed")
                    self.emit("error", {"code": "process_failed", "message": str(e)})
                    from .processor import FrameResult
                    res = FrameResult(frame, face=False, swapped=False)
                active = self.processor.params.swap and self.processor.latent is not None
            t1 = time.perf_counter()
            self.proc_ms = 0.8 * self.proc_ms + 0.2 * (t1 - t0) * 1000 if self.proc_ms else (t1 - t0) * 1000
            for k, v in res.timings.items():
                self.stage_ms[k] = 0.8 * self.stage_ms.get(k, v) + 0.2 * v
            self.latency_ms = (t1 - t_cap) * 1000
            self.proc_rate.tick(t1)
            self.frames_processed += 1
            self._out.put((frame, res.frame))
            self._track_face(res.face if active else None)

    def _track_face(self, present: bool | None) -> None:
        if present is None:            # bypassed: no face status
            if self._face_state is not None:
                self._face_state = None
                self.emit("face_found", {"bypass": True})
            return
        if present:
            self._miss = 0
            if self._face_state is not True:
                self._face_state = True
                self.emit("face_found", {})
        else:
            self._miss += 1
            if self._miss >= 6 and self._face_state is not False:
                self._face_state = False
                self.emit("face_lost", {})

    def _output_loop(self) -> None:
        period = 1.0 / self.out_fps
        next_t = time.perf_counter()
        next_preview = next_stats = next_t
        last = None
        while not self._stop.is_set():
            _, item = self._out.peek()
            if item is not None:
                last = item
            now = time.perf_counter()
            if last is not None:
                cam = self.vcam
                if self.live and cam is not None:
                    try:
                        cam.send(last[1])
                        self.out_rate.tick(now)
                    except Exception as e:  # noqa: BLE001
                        log.exception("vcam send failed")
                        self.stop_live()
                        self.emit("error", {"code": "vcam_failed", "message": str(e)})
                if self.preview_enabled and now >= next_preview:
                    next_preview = now + 1.0 / self.preview_fps
                    self._emit_preview(*last)
            if now >= next_stats:
                next_stats = now + 0.5
                self.emit("stats", self.stats())
            next_t += period
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.perf_counter()

    def _encode(self, frame: np.ndarray) -> str:
        h, w = frame.shape[:2]
        if w > self.preview_width:
            frame = cv2.resize(frame, (self.preview_width, int(h * self.preview_width / w)), interpolation=cv2.INTER_AREA)
        if self.mirror_preview:
            frame = cv2.flip(frame, 1)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
        return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""

    def _emit_preview(self, original: np.ndarray, processed: np.ndarray) -> None:
        data = {"frame": self._encode(original if self.view == "Original" else processed)}
        if self.view == "Split":
            data["original"] = self._encode(original)
        self.emit("preview", data)

    def stats(self) -> dict:
        return {
            "fps": round(self.proc_rate.value(), 1),
            "capture_fps": round(self.cap_rate.value(), 1),
            "output_fps": round(self.out_rate.value(), 1) if self.live else 0.0,
            "ms": round(self.proc_ms, 1),
            "latency_ms": round(self.latency_ms, 1),
            "stages": {k: round(v, 1) for k, v in self.stage_ms.items()},
            "live": self.live,
            "face": self._face_state,
            "provider": provider_label(self.models.providers),
            "tier": self.processor.tier.name,
            "swap_fps": self.processor.swap_fps,
            "size": list(self.size),
        }
