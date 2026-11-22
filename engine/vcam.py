"""Virtual camera output ("Mirage Camera") through pyvirtualcam."""
from __future__ import annotations

import logging
import os

import cv2
import numpy as np

log = logging.getLogger("mirage.vcam")

CAMERA_NAME = "Mirage Camera"


class VCamMissing(RuntimeError):
    pass


class VirtualCamera:
    """Wraps pyvirtualcam. On Windows uses the Unity Capture backend, which the
    installer registers under the name "Mirage Camera" (no OBS needed)."""

    def __init__(self, width: int, height: int, fps: float):
        self.width, self.height, self.fps = width, height, fps
        self.cam = None

    def open(self) -> None:
        try:
            import pyvirtualcam
        except ImportError as e:
            raise VCamMissing("pyvirtualcam is not installed") from e
        kwargs = dict(width=self.width, height=self.height, fps=self.fps, fmt=pyvirtualcam.PixelFormat.RGB,
                      print_fps=False)
        attempts = []
        if os.name == "nt":
            attempts.append(dict(backend="unitycapture", device=CAMERA_NAME))
        attempts.append({})
        last = None
        for extra in attempts:
            try:
                self.cam = pyvirtualcam.Camera(**kwargs, **extra)
                log.info("virtual camera open: %s (%s)", self.cam.device, self.cam.backend)
                return
            except Exception as e:  # noqa: BLE001 — pyvirtualcam raises RuntimeError/ValueError
                last = e
        raise VCamMissing(f"Virtual camera driver not available: {last}")

    @property
    def device(self) -> str:
        return getattr(self.cam, "device", CAMERA_NAME) if self.cam else CAMERA_NAME

    def send(self, frame: np.ndarray) -> None:
        if self.cam is None:
            return
        if frame.shape[1] != self.width or frame.shape[0] != self.height:
            frame = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_LINEAR)
        self.cam.send(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))  # RGB is supported by every backend

    def close(self) -> None:
        if self.cam is not None:
            try:
                self.cam.close()
            finally:
                self.cam = None


class NullCamera:
    """Used with --no-vcam: accepts frames and drops them."""

    device = "none"

    def __init__(self, *a, **k):
        self.sent = 0

    def open(self) -> None:
        pass

    def send(self, frame) -> None:
        self.sent += 1

    def close(self) -> None:
        pass
