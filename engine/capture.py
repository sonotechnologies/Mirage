"""Webcam enumeration/capture (DirectShow on Windows) and a video-file source for headless testing."""
from __future__ import annotations

import logging
import os
import subprocess
import time

import cv2
import numpy as np

log = logging.getLogger("mirage.capture")

COMMON_RES = [(1920, 1080), (1280, 720), (854, 480), (640, 480)]


class CameraInUse(RuntimeError):
    pass


def _backend() -> int:
    return cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY


def _windows_camera_names() -> list[str]:
    """DirectShow video device names in enumeration order (matches OpenCV DSHOW indices)."""
    try:
        ps = ("Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPClass -in @('Camera','Image') } "
              "| Select-Object -ExpandProperty Name")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return [n.strip() for n in out.splitlines() if n.strip()]
    except Exception:
        return []


def list_cameras(max_index: int = 6, probe_resolutions: bool = True) -> list[dict]:
    """Probe camera indices. Excludes our own virtual camera so we never capture ourselves."""
    names = _windows_camera_names() if os.name == "nt" else []
    cams = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, _backend())
        if not cap.isOpened():
            cap.release()
            continue
        name = names[i] if i < len(names) else f"Camera {i + 1}"
        if "mirage" in name.lower():
            cap.release()
            continue
        res = []
        if probe_resolutions:
            for w, h in COMMON_RES:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                gw, gh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                if (gw, gh) == (w, h) and f"{w}x{h}" not in res:
                    res.append(f"{w}x{h}")
        cap.release()
        cams.append({"id": i, "name": name, "resolutions": res or ["640x480"]})
    return cams


def parse_res(res: str | None, default: tuple[int, int]) -> tuple[int, int]:
    try:
        w, h = (int(v) for v in str(res).lower().split("x"))
        return w, h
    except Exception:
        return default


class CameraSource:
    def __init__(self, index: int, width: int, height: int, fps: int = 30):
        self.index, self.width, self.height, self.fps = index, width, height, fps
        self.cap: cv2.VideoCapture | None = None
        self.label = f"camera:{index}"

    def open(self) -> None:
        cap = cv2.VideoCapture(self.index, _backend())
        if not cap.isOpened():
            cap.release()
            raise CameraInUse(f"Camera {self.index} could not be opened (in use by another app or unplugged).")
        if os.name == "nt":
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))  # MJPG = high FPS at 720p+
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            raise CameraInUse(f"Camera {self.index} opened but returned no frames (likely in use by another app).")
        self.cap = cap
        self.width, self.height = frame.shape[1], frame.shape[0]

    def read(self) -> np.ndarray | None:
        if self.cap is None:
            return None
        ok, frame = self.cap.read()
        return frame if ok else None

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class VideoSource:
    """Plays a video file in a loop at its native frame rate (for --video testing)."""

    def __init__(self, path: str, width: int | None = None, height: int | None = None, loop: bool = True,
                 realtime: bool = True):
        self.path, self.loop, self.realtime = path, loop, realtime
        self.target = (width, height) if width and height else None
        self.cap: cv2.VideoCapture | None = None
        self.fps = 30.0
        self.width = self.height = 0
        self._next = 0.0
        self.label = f"video:{os.path.basename(path)}"
        self.finished = False

    def open(self) -> None:
        cap = cv2.VideoCapture(self.path)
        if not cap.isOpened():
            raise FileNotFoundError(self.path)
        self.cap = cap
        self.fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if self.target:
            self.width, self.height = self.target
        self._next = time.perf_counter()

    def read(self) -> np.ndarray | None:
        if self.cap is None:
            return None
        if self.realtime:
            delay = self._next - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            self._next = max(self._next + 1.0 / self.fps, time.perf_counter() - 0.5)
        ok, frame = self.cap.read()
        if not ok:
            if not self.loop:
                self.finished = True
                return None
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()
            if not ok:
                return None
        if self.target and (frame.shape[1], frame.shape[0]) != self.target:
            frame = cv2.resize(frame, self.target, interpolation=cv2.INTER_AREA)
        return frame

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None
