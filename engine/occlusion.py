"""XSeg occlusion mask: 1 where the face is visible, 0 where a hand/object covers it."""
from __future__ import annotations

import cv2
import numpy as np


class Occluder:
    SIZE = 256

    def __init__(self, session):
        self.session = session
        inp = session.get_inputs()[0]
        self.input_name = inp.name
        # xseg is NHWC; read the spatial size from the model when it is fixed.
        shp = inp.shape
        if len(shp) == 4 and isinstance(shp[1], int):
            self.SIZE = shp[1]

    def mask(self, crop_bgr: np.ndarray, lowres: bool = False) -> np.ndarray:
        """Return a float32 mask the size of crop_bgr, values 0..1."""
        h, w = crop_bgr.shape[:2]
        x = cv2.resize(crop_bgr, (self.SIZE, self.SIZE), interpolation=cv2.INTER_AREA if lowres else cv2.INTER_LINEAR)
        x = x[None].astype(np.float32) / 255.0
        m = self.session.run(None, {self.input_name: x})[0][0, :, :, 0]
        m = np.clip(m, 0, 1).astype(np.float32)
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
        # Soften and harden the transition the same way FaceFusion does.
        sigma = max(1.0, w / 128 * 2.5)
        m = (cv2.GaussianBlur(m, (0, 0), sigma).clip(0.5, 1) - 0.5) * 2
        return m
