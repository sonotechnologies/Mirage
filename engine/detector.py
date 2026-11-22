"""SCRFD face detector (InsightFace buffalo_s det_500m) with 5-point landmarks."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

STRIDES = (8, 16, 32)
NUM_ANCHORS = 2


@dataclass
class Face:
    bbox: np.ndarray   # x1, y1, x2, y2 (float32)
    kps: np.ndarray    # 5x2 (float32)
    score: float

    @property
    def area(self) -> float:
        w, h = self.bbox[2] - self.bbox[0], self.bbox[3] - self.bbox[1]
        return float(max(0.0, w) * max(0.0, h))

    def to_dict(self) -> dict:
        return {"box": [round(float(v), 1) for v in self.bbox], "score": round(self.score, 3),
                "kps": [[round(float(x), 1), round(float(y), 1)] for x, y in self.kps]}


def nms(boxes: np.ndarray, scores: np.ndarray, thresh: float = 0.4) -> list[int]:
    x1, y1, x2, y2 = boxes.T
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1 + 1) * np.maximum(0.0, yy2 - yy1 + 1)
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[1:][ovr <= thresh]
    return keep


class Detector:
    def __init__(self, session, input_size: int = 320, threshold: float = 0.5):
        self.session = session
        self.input_name = session.get_inputs()[0].name
        self.input_size = input_size
        self.threshold = threshold
        self._centers: dict[tuple, np.ndarray] = {}

    def _anchor_centers(self, h: int, w: int, stride: int) -> np.ndarray:
        key = (h, w, stride)
        c = self._centers.get(key)
        if c is None:
            ys, xs = np.mgrid[:h, :w]
            c = np.stack([xs, ys], axis=-1).astype(np.float32).reshape(-1, 2) * stride
            c = np.repeat(c, NUM_ANCHORS, axis=0)
            self._centers[key] = c
        return c

    def detect(self, img: np.ndarray, input_size: int | None = None, max_faces: int = 0) -> list[Face]:
        size = input_size or self.input_size
        ih, iw = img.shape[:2]
        scale = size / max(ih, iw)
        nh, nw = int(round(ih * scale)), int(round(iw * scale))
        canvas = np.zeros((size, size, 3), np.uint8)
        canvas[:nh, :nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 128.0, (size, size), (127.5, 127.5, 127.5), swapRB=True)
        outs = self.session.run(None, {self.input_name: blob})

        all_scores, all_boxes, all_kps = [], [], []
        n = len(STRIDES)
        for i, stride in enumerate(STRIDES):
            scores = outs[i].reshape(-1)
            boxes = outs[i + n].reshape(-1, 4) * stride
            kps = outs[i + 2 * n].reshape(-1, 10) * stride
            fh, fw = size // stride, size // stride
            centers = self._anchor_centers(fh, fw, stride)
            idx = np.where(scores >= self.threshold)[0]
            if idx.size == 0:
                continue
            c = centers[idx]
            b = boxes[idx]
            all_boxes.append(np.stack([c[:, 0] - b[:, 0], c[:, 1] - b[:, 1],
                                       c[:, 0] + b[:, 2], c[:, 1] + b[:, 3]], axis=-1))
            k = kps[idx].reshape(-1, 5, 2) + c[:, None, :]
            all_kps.append(k)
            all_scores.append(scores[idx])
        if not all_scores:
            return []
        scores = np.concatenate(all_scores)
        boxes = np.concatenate(all_boxes) / scale
        kps = np.concatenate(all_kps) / scale
        keep = nms(boxes, scores)
        faces = [Face(boxes[i].astype(np.float32), kps[i].astype(np.float32), float(scores[i])) for i in keep]
        faces.sort(key=lambda f: f.area, reverse=True)
        return faces[:max_faces] if max_faces else faces
