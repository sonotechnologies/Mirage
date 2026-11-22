"""inswapper_128 inference plus the ArcFace recognizer that feeds it."""
from __future__ import annotations

import cv2
import numpy as np

from . import align


class Recognizer:
    """ArcFace w600k_r50: 112x112 aligned crop -> L2-normalised 512-d embedding.

    Used only at import time (never per frame), so the heavier r50 model is fine.
    It must be the r50 model: inswapper was trained on its embeddings.
    """

    def __init__(self, session):
        self.session = session
        self.input_name = session.get_inputs()[0].name

    def embed(self, img: np.ndarray, kps: np.ndarray) -> np.ndarray:
        crop = align.warp(img, align.estimate(kps, align.ARCFACE_112), 112)
        blob = cv2.dnn.blobFromImage(crop, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
        emb = self.session.run(None, {self.input_name: blob})[0][0].astype(np.float32)
        return emb / (np.linalg.norm(emb) + 1e-8)


class Swapper:
    SIZE = 128

    def __init__(self, session, emap: np.ndarray):
        self.session = session
        self.emap = emap.astype(np.float32)
        names = [i.name for i in session.get_inputs()]
        self.target_name = "target" if "target" in names else names[0]
        self.source_name = "source" if "source" in names else names[1]

    def latent(self, normed_embedding: np.ndarray) -> np.ndarray:
        """Project an ArcFace embedding into inswapper's latent (once per face)."""
        lat = normed_embedding.reshape(1, -1) @ self.emap
        return (lat / (np.linalg.norm(lat) + 1e-8)).astype(np.float32)

    def swap(self, crop_bgr: np.ndarray, latent: np.ndarray) -> np.ndarray:
        """crop_bgr: 128x128 BGR aligned with ARCFACE_128. Returns swapped 128x128 BGR."""
        blob = cv2.dnn.blobFromImage(crop_bgr, 1.0 / 255.0, (self.SIZE, self.SIZE), (0, 0, 0), swapRB=True)
        out = self.session.run(None, {self.target_name: blob, self.source_name: latent})[0]
        img = out[0].transpose(1, 2, 0)
        return np.clip(img * 255.0, 0, 255).astype(np.uint8)[:, :, ::-1].copy()
