"""GFPGAN v1.4 face restoration (512x512, FFHQ alignment)."""
from __future__ import annotations

import numpy as np


class Enhancer:
    SIZE = 512

    def __init__(self, session):
        self.session = session
        self.input_name = session.get_inputs()[0].name

    def enhance(self, crop_bgr: np.ndarray) -> np.ndarray:
        x = crop_bgr[:, :, ::-1].astype(np.float32) / 255.0
        x = (x - 0.5) / 0.5
        x = np.ascontiguousarray(x.transpose(2, 0, 1)[None])
        y = self.session.run(None, {self.input_name: x})[0][0]
        y = (np.clip(y, -1, 1) + 1) / 2
        return (y.transpose(1, 2, 0) * 255.0).round().astype(np.uint8)[:, :, ::-1].copy()
