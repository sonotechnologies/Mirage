"""Colour/lighting transfer and face-only sharpening."""
from __future__ import annotations

import cv2
import numpy as np


def transfer_lab(src_bgr: np.ndarray, ref_bgr: np.ndarray, strength: float,
                 weight: np.ndarray | None = None) -> np.ndarray:
    """Match src's LAB mean/std to ref's. strength 0..1. weight: optional mask for stats."""
    if strength <= 0:
        return src_bgr
    s = cv2.cvtColor(src_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    r = cv2.cvtColor(ref_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    if weight is not None:
        sel = weight > 0.5
        if sel.sum() < 16:
            sel = None
    else:
        sel = None
    sv = s[sel] if sel is not None else s.reshape(-1, 3)
    rv = r[sel] if sel is not None else r.reshape(-1, 3)
    s_mean, s_std = sv.mean(0), sv.std(0) + 1e-5
    r_mean, r_std = rv.mean(0), rv.std(0) + 1e-5
    out = (s - s_mean) * (r_std / s_std) + r_mean
    out = s + (out - s) * float(strength)
    return cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def unsharp(img: np.ndarray, amount: float) -> np.ndarray:
    """amount 0..1 -> unsharp mask strength 0..1.5."""
    if amount <= 0:
        return img
    sigma = max(0.8, img.shape[0] / 128.0)
    blur = cv2.GaussianBlur(img, (0, 0), sigma)
    return cv2.addWeighted(img, 1 + 1.5 * amount, blur, -1.5 * amount, 0)
