"""Landmark tracking between detections (pyramidal Lucas-Kanade) + One Euro smoothing."""
from __future__ import annotations

import math

import cv2
import numpy as np


class OneEuro:
    """One Euro filter over a fixed-shape array (Casiez et al. 2012)."""

    def __init__(self, min_cutoff: float = 1.2, beta: float = 0.05, d_cutoff: float = 1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x_prev: np.ndarray | None = None
        self.dx_prev: np.ndarray | None = None
        self.t_prev: float | None = None

    @staticmethod
    def _alpha(cutoff: np.ndarray | float, dt: float):
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self) -> None:
        self.x_prev = self.dx_prev = self.t_prev = None

    def __call__(self, x: np.ndarray, t: float) -> np.ndarray:
        x = np.asarray(x, np.float32)
        if self.x_prev is None or self.t_prev is None:
            self.x_prev, self.dx_prev, self.t_prev = x.copy(), np.zeros_like(x), t
            return x
        dt = max(1e-3, t - self.t_prev)
        dx = (x - self.x_prev) / dt
        a_d = self._alpha(self.d_cutoff, dt)
        dx_hat = a_d * dx + (1 - a_d) * self.dx_prev
        cutoff = self.min_cutoff + self.beta * np.abs(dx_hat)
        a = self._alpha(cutoff, dt)
        x_hat = a * x + (1 - a) * self.x_prev
        self.x_prev, self.dx_prev, self.t_prev = x_hat.astype(np.float32), dx_hat, t
        return self.x_prev


class Tracker:
    """Holds the current face's landmarks and moves them frame-to-frame with optical flow."""

    LK = dict(winSize=(21, 21), maxLevel=3,
              criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03))

    def __init__(self, max_fb_error: float = 1.5):
        self.max_fb_error = max_fb_error
        self.prev_gray: np.ndarray | None = None
        self.kps: np.ndarray | None = None
        self.filter = OneEuro()
        self.since_detect = 0

    def reset(self) -> None:
        self.prev_gray = None
        self.kps = None
        self.filter.reset()
        self.since_detect = 0

    def update_detection(self, gray: np.ndarray, kps: np.ndarray, t: float) -> np.ndarray:
        # Large jump (new person / re-acquire): restart smoothing.
        if self.kps is not None:
            span = np.linalg.norm(kps[0] - kps[1]) + 1e-3
            if np.abs(kps - self.kps).max() > span:
                self.filter.reset()
        self.prev_gray = gray
        self.kps = kps.astype(np.float32)
        self.since_detect = 0
        return self.filter(self.kps, t)

    def track(self, gray: np.ndarray, t: float) -> np.ndarray | None:
        """Return smoothed landmarks, or None if tracking confidence is too low."""
        if self.prev_gray is None or self.kps is None or self.prev_gray.shape != gray.shape:
            return None
        p0 = self.kps.reshape(-1, 1, 2)
        p1, st, _ = cv2.calcOpticalFlowPyrLK(self.prev_gray, gray, p0, None, **self.LK)
        if p1 is None:
            return None
        p0r, st2, _ = cv2.calcOpticalFlowPyrLK(gray, self.prev_gray, p1, None, **self.LK)
        if p0r is None:
            return None
        fb = np.linalg.norm(p0 - p0r, axis=-1).reshape(-1)
        good = (st.reshape(-1) == 1) & (st2.reshape(-1) == 1) & (fb < self.max_fb_error)
        if good.sum() < 4:
            return None
        new = p1.reshape(-1, 2)
        if not good.all():
            # Move the lost point(s) with the median motion of the good ones.
            shift = np.median(new[good] - self.kps[good], axis=0)
            new[~good] = self.kps[~good] + shift
        self.prev_gray = gray
        self.kps = new.astype(np.float32)
        self.since_detect += 1
        return self.filter(self.kps, t)
