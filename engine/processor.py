"""Per-frame face swap: detect/track -> align -> swap -> enhance -> colour -> mask -> paste."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import align
from .color import transfer_lab, unsharp
from .providers import Tier
from .tracker import Tracker


@dataclass
class Params:
    swap: bool = True
    blend: int = 60
    sharpness: int = 30
    colour: int = 70
    enhancer: bool = False
    occlusion: bool = True

    def update(self, key: str, value) -> None:
        if key in ("swap", "enhancer", "occlusion"):
            setattr(self, key, bool(value))
        elif key in ("blend", "sharpness", "colour"):
            setattr(self, key, int(max(0, min(100, float(value)))))
        else:
            raise KeyError(key)


@dataclass
class FrameResult:
    frame: np.ndarray
    face: bool                 # a face was found (swap applied when swapping is on)
    swapped: bool
    timings: dict = field(default_factory=dict)


class Timer:
    def __init__(self):
        self.t = time.perf_counter()
        self.ms: dict[str, float] = {}

    def lap(self, name: str) -> None:
        now = time.perf_counter()
        self.ms[name] = self.ms.get(name, 0.0) + (now - self.t) * 1000
        self.t = now


def ellipse_mask(size: int, blend: int) -> np.ndarray:
    """Feathered face mask in aligned-crop space. Higher blend = softer, wider feather."""
    m = np.zeros((size, size), np.float32)
    s = size / 128.0
    # Ellipse covering the inner face for the ARCFACE_128 / FFHQ_512 crops.
    cv2.ellipse(m, (int(64 * s), int(70 * s)), (int(44 * s), int(56 * s)), 0, 0, 360, 1.0, -1)
    feather = (2 + blend / 100.0 * 14) * s
    erode = int(max(1, feather * 0.6))
    m = cv2.erode(m, np.ones((erode, erode), np.uint8))
    return cv2.GaussianBlur(m, (0, 0), feather)


class AsyncStage:
    """Runs fn(*args) on a worker thread, always on the newest submitted input.

    Used on CPU, where inswapper/xseg are far slower than the frame budget: the
    caller keeps using the last finished result (which lives in aligned-crop
    space, so it still follows the live landmarks) while the next one computes.
    """

    def __init__(self, fn, name: str):
        self.fn = fn
        self._cv = threading.Condition()
        self._pending = None
        self.result = None          # (output, tag)
        self.busy_ms = 0.0
        self._stop = False
        self._t = threading.Thread(target=self._run, name=f"mirage-{name}", daemon=True)
        self._t.start()

    def submit(self, args: tuple, tag) -> None:
        with self._cv:
            self._pending = (args, tag)
            self._cv.notify()

    def _run(self) -> None:
        while True:
            with self._cv:
                while self._pending is None and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                args, tag = self._pending
                self._pending = None
            t0 = time.perf_counter()
            try:
                out = self.fn(*args)
            except Exception:  # noqa: BLE001 — keep the last good result
                continue
            self.busy_ms = (time.perf_counter() - t0) * 1000
            self.result = (out, tag)

    def close(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify()
        self._t.join(timeout=3)


class Processor:
    """Single-face swap engine. Not thread-safe: call process() from one thread."""

    def __init__(self, models, tier: Tier, async_cpu: bool | None = None):
        self.models = models
        # Asynchronous swap/occlusion when running on CPU (see AsyncStage).
        if async_cpu is None:
            async_cpu = models.providers[0] == "CPUExecutionProvider"
        self.async_mode = async_cpu
        self._swap_stage = (AsyncStage(lambda c, l: (models.swapper.swap(c, l), c), "swap")
                            if async_cpu else None)
        self._occ_stage = (AsyncStage(lambda c, lr: models.occluder.mask(c, lowres=lr), "occlusion")
                           if async_cpu and models.occluder is not None else None)
        self._latent_gen = 0
        self.tier = tier
        self.params = Params()
        self.latent: np.ndarray | None = None
        self.tracker = Tracker()
        self.frame_idx = 0
        self._mask_cache: dict[tuple, np.ndarray] = {}
        self._occ: np.ndarray | None = None
        self._occ_age = 0
        self.face_present = False

    # -- configuration -------------------------------------------------
    def set_tier(self, tier: Tier) -> None:
        self.tier = tier
        self.tracker.reset()

    def set_latent(self, latent: np.ndarray | None) -> None:
        self.latent = latent
        self._latent_gen += 1    # invalidates any cached async swap of the previous face

    def close(self) -> None:
        for st in (self._swap_stage, self._occ_stage):
            if st:
                st.close()

    def _swap(self, crop: np.ndarray, timer: "Timer") -> tuple[np.ndarray, np.ndarray | None]:
        """Return (swapped crop, change mask or None).

        In async mode the swapped crop may be a few frames old; the change mask
        (1 = unchanged) marks where the live crop differs strongly from the crop
        that was swapped, e.g. a hand that just moved in, so the live pixels win there.
        """
        st = self._swap_stage
        if st is None:
            out = self.models.swapper.swap(crop, self.latent)
            timer.lap("swap")
            return out, None
        res = st.result
        if res is None or res[1] != self._latent_gen:
            # First frame for this face: swap synchronously so a switch applies on this frame.
            out = self.models.swapper.swap(crop, self.latent)
            st.result = ((out, crop), self._latent_gen)
            timer.lap("swap")
            change = None
        else:
            out, src_crop = res[0]
            change = change_mask(src_crop, crop)
            timer.lap("swap_reuse")
        st.submit((crop, self.latent), self._latent_gen)
        return out, change

    @property
    def swap_fps(self) -> float | None:
        st = self._swap_stage
        return round(1000.0 / st.busy_ms, 1) if st and st.busy_ms else None

    def _occlusion(self, crop: np.ndarray) -> np.ndarray:
        st = self._occ_stage
        if st is not None:
            st.submit((crop, self.tier.occlusion_lowres), 0)
            if st.result is not None:
                return st.result[0]
            return self.models.occluder.mask(crop, lowres=self.tier.occlusion_lowres)
        if self._occ is None or self._occ_age + 1 >= self.tier.occlusion_every:
            self._occ = self.models.occluder.mask(crop, lowres=self.tier.occlusion_lowres)
            self._occ_age = 0
        else:
            self._occ_age += 1
        return self._occ

    def _base_mask(self, size: int) -> np.ndarray:
        key = (size, self.params.blend)
        m = self._mask_cache.get(key)
        if m is None:
            if len(self._mask_cache) > 16:
                self._mask_cache.clear()
            m = self._mask_cache[key] = ellipse_mask(size, self.params.blend)
        return m

    # -- landmarks -----------------------------------------------------
    def _landmarks(self, frame: np.ndarray, t: float, timer: Timer) -> np.ndarray | None:
        h, w = frame.shape[:2]
        scale = 320.0 / max(h, w)
        gray = cv2.cvtColor(cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA),
                            cv2.COLOR_BGR2GRAY)
        kps = None
        due = self.tracker.kps is None or self.tracker.since_detect + 1 >= self.tier.detect_every
        if not due:
            k = self.tracker.track(gray, t)
            timer.lap("track")
            if k is not None:
                kps = k / scale
        if kps is None:
            faces = self.models.detector.detect(frame, max_faces=1)
            timer.lap("detect")
            if not faces:
                self.tracker.reset()
                return None
            kps = self.tracker.update_detection(gray, faces[0].kps * scale, t) / scale
        return kps

    # -- main ----------------------------------------------------------
    def process(self, frame: np.ndarray, t: float | None = None) -> FrameResult:
        t = time.perf_counter() if t is None else t
        timer = Timer()
        self.frame_idx += 1
        active = self.params.swap and self.latent is not None
        if not active:
            # Bypass: no inference at all; keep the tracker cold so re-enable re-detects.
            self.tracker.reset()
            return FrameResult(frame, face=False, swapped=False, timings={})

        kps = self._landmarks(frame, t, timer)
        if kps is None:
            self.face_present = False
            return FrameResult(frame, face=False, swapped=False, timings=timer.ms)
        self.face_present = True

        m128 = align.estimate(kps, align.ARCFACE_128)
        crop = align.warp(frame, m128, 128)
        timer.lap("align")
        swapped, change = self._swap(crop, timer)

        enhance = (self.params.enhancer and self.tier.enhancer_allowed and self.models.enhancer is not None)
        if enhance:
            m_w = align.estimate(kps, align.FFHQ_512)
            to_w = align.compose(m_w, align.invert(m128))     # crop128 -> ffhq512
            size = 512
            work = self.models.enhancer.enhance(align.warp(swapped, to_w, 512))
            ref = align.warp(frame, m_w, 512)
            timer.lap("enhance")
        else:
            m_w, to_w, size = m128, None, 128
            work, ref = swapped, crop

        base = self._base_mask(size)
        if self.params.colour > 0:
            work = transfer_lab(work, ref, self.params.colour / 100.0, weight=base)
        if self.params.sharpness > 0:
            work = unsharp(work, self.params.sharpness / 100.0)
        timer.lap("colour")

        mask = base
        if self.params.occlusion and self.models.occluder is not None:
            occ = self._occlusion(crop)
            if to_w is not None:
                occ = cv2.warpAffine(occ, to_w, (size, size))
            mask = base * occ
            timer.lap("mask")
        if change is not None:
            mask = mask * (change if to_w is None else cv2.warpAffine(change, to_w, (size, size), borderValue=1))
        out = paste_back(frame, work, mask, m_w)
        timer.lap("paste")
        return FrameResult(out, face=True, swapped=True, timings=timer.ms)


def change_mask(old: np.ndarray, new: np.ndarray, lo: float = 45.0, hi: float = 90.0) -> np.ndarray:
    """1 where two aligned crops agree, falling to 0 where they differ a lot.

    Compared at 24x24 and then morphologically opened, so landmark jitter and
    expression changes (small blobs) are ignored and only large regions such
    as a hand register.
    """
    n = 24
    a = cv2.resize(old, (n, n), interpolation=cv2.INTER_AREA).astype(np.int16)
    b = cv2.resize(new, (n, n), interpolation=cv2.INTER_AREA).astype(np.int16)
    d = np.abs(a - b).max(axis=2).astype(np.float32)
    changed = np.clip((d - lo) / (hi - lo), 0, 1)
    k = np.ones((3, 3), np.uint8)
    changed = cv2.dilate(cv2.erode(changed, k), k, iterations=2)   # open, then grow a little
    h, w = old.shape[:2]
    m = 1.0 - cv2.resize(changed, (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.GaussianBlur(m, (0, 0), w / 40)


def paste_back(frame: np.ndarray, face: np.ndarray, mask: np.ndarray, m: np.ndarray) -> np.ndarray:
    """Inverse-warp `face` (aligned by m) into `frame`, blending only inside its bounding box."""
    h, w = frame.shape[:2]
    size = face.shape[0]
    inv = align.invert(m)
    corners = np.array([[0, 0, 1], [size, 0, 1], [0, size, 1], [size, size, 1]], np.float32) @ inv.T
    x0, y0 = np.floor(corners.min(0)).astype(int)
    x1, y1 = np.ceil(corners.max(0)).astype(int)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return frame
    roi_inv = inv.copy()
    roi_inv[:, 2] -= (x0, y0)
    rw, rh = x1 - x0, y1 - y0
    f = cv2.warpAffine(face, roi_inv, (rw, rh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    a = cv2.warpAffine(mask, roi_inv, (rw, rh), flags=cv2.INTER_LINEAR, borderValue=0)[..., None]
    out = frame.copy()
    roi = out[y0:y1, x0:x1].astype(np.float32)
    out[y0:y1, x0:x1] = (f.astype(np.float32) * a + roi * (1 - a)).astype(np.uint8)
    return out
