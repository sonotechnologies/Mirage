"""Five-point face alignment templates and warps."""
from __future__ import annotations

import cv2
import numpy as np

# InsightFace ArcFace template for a 112x112 crop (eyes, nose, mouth corners).
ARCFACE_112 = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)

# inswapper_128 crop: same template shifted 8px right (insightface norm_crop, size 128).
ARCFACE_128 = ARCFACE_112 + np.array([8.0, 0.0], dtype=np.float32)

# FFHQ alignment used by GFPGAN, normalised then scaled to 512.
FFHQ_512 = np.array(
    [[0.37691676, 0.46864664], [0.62285697, 0.46912813], [0.50123859, 0.61331904],
     [0.39308822, 0.72541100], [0.61150205, 0.72490465]], dtype=np.float32) * 512


def similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares similarity transform (Umeyama) mapping src -> dst. Returns 2x3."""
    src = np.asarray(src, np.float64)
    dst = np.asarray(dst, np.float64)
    n = src.shape[0]
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s, d = src - mu_s, dst - mu_d
    cov = d.T @ s / n
    u, sig, vt = np.linalg.svd(cov)
    e = np.eye(2)
    if np.linalg.det(cov) < 0:
        e[1, 1] = -1
    r = u @ e @ vt
    var_s = (s ** 2).sum() / n
    scale = (sig * np.diag(e)).sum() / var_s if var_s > 0 else 1.0
    t = mu_d - scale * r @ mu_s
    m = np.zeros((2, 3), np.float32)
    m[:, :2] = scale * r
    m[:, 2] = t
    return m


def estimate(kps: np.ndarray, template: np.ndarray) -> np.ndarray:
    return similarity(kps, template)


def warp(img: np.ndarray, m: np.ndarray, size: int) -> np.ndarray:
    return cv2.warpAffine(img, m, (size, size), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def invert(m: np.ndarray) -> np.ndarray:
    return cv2.invertAffineTransform(m)


def compose(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Return affine equivalent to applying b then a (a ∘ b)."""
    a3 = np.vstack([a, [0, 0, 1]])
    b3 = np.vstack([b, [0, 0, 1]])
    return (a3 @ b3)[:2].astype(np.float32)
