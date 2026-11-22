"""Consent-only face library: import, embed (once), store, thumbnails, rename/reorder/delete.

Layout in %APPDATA%/Mirage/faces/:
    index.json                 ordered list of {id, name, created}
    <id>/source.jpg            the imported photo (re-encoded, max 1600px)
    <id>/embedding.npy         normalised ArcFace embedding (512 float32)
    <id>/thumb.jpg             192px square thumbnail
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

import cv2
import numpy as np

import paths

MAX_FACES = 12
MAX_SIDE = 1600
THUMB = 192
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}


class ImportError_(ValueError):
    """Import rejected; `code` is stable for the UI."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def decode_image(src: str | bytes) -> np.ndarray:
    """Accept a file path, a data: URL, or raw base64. Returns BGR uint8."""
    data: bytes | None = None
    if isinstance(src, bytes):
        data = src
    elif isinstance(src, str):
        s = src.strip()
        if s.startswith("data:"):
            header, _, b64 = s.partition(",")
            if not re.match(r"data:image/(jpeg|jpg|png|webp)", header, re.I):
                raise ImportError_("bad_format", "Use a JPG, PNG or WEBP image.")
            data = base64.b64decode(b64)
        elif len(s) < 1024 and Path(s).suffix.lower() in ALLOWED_EXT and Path(s).exists():
            data = Path(s).read_bytes()
        elif len(s) < 1024 and Path(s).exists():
            raise ImportError_("bad_format", "Use a JPG, PNG or WEBP image.")
        else:
            try:
                data = base64.b64decode(s, validate=True)
            except Exception as e:
                raise ImportError_("bad_image", "That file could not be read.") from e
    if not data:
        raise ImportError_("bad_image", "That file could not be read.")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ImportError_("bad_image", "That file could not be read as an image.")
    h, w = img.shape[:2]
    if max(h, w) > MAX_SIDE:
        s = MAX_SIDE / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return img


def significant_faces(faces: list, ratio: float = 0.2) -> list:
    """Faces at least `ratio` of the largest face's area (ignores tiny background faces)."""
    if not faces:
        return []
    big = max(f.area for f in faces)
    return [f for f in faces if f.area >= big * ratio]


def detect_for_import(detector, img: np.ndarray) -> list:
    """Detect at high resolution; retry on a padded copy for tight, face-filling crops."""
    faces = significant_faces(detector.detect(img, input_size=640))
    if faces:
        return faces
    h, w = img.shape[:2]
    pad = int(max(h, w) * 0.4)
    padded = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    faces = significant_faces(detector.detect(padded, input_size=640))
    for f in faces:
        f.bbox = f.bbox - pad
        f.kps = f.kps - pad
    return faces


def thumbnail(img: np.ndarray, bbox: np.ndarray) -> np.ndarray:
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    side = max(x2 - x1, y2 - y1) * 1.6
    h, w = img.shape[:2]
    m = np.float32([[THUMB / side, 0, THUMB / 2 - cx * THUMB / side],
                    [0, THUMB / side, THUMB / 2 - (cy - side * 0.05) * THUMB / side]])
    return cv2.warpAffine(img, m, (THUMB, THUMB), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE)


def to_data_url(path: Path) -> str:
    try:
        return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return ""


class FaceLibrary:
    def __init__(self, detector, recognizer, swapper=None, root: Path | None = None):
        self.detector = detector
        self.recognizer = recognizer
        self.swapper = swapper
        self.root = root or paths.faces_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._latents: dict[str, np.ndarray] = {}
        self.items: list[dict] = self._load_index()
        self._prune()

    # -- persistence ----------------------------------------------------
    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    def _load_index(self) -> list[dict]:
        try:
            data = json.loads(self.index_path.read_text("utf-8"))
            return [d for d in data if isinstance(d, dict) and "id" in d]
        except (OSError, ValueError):
            return []

    def _save_index(self) -> None:
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, indent=2), "utf-8")
        tmp.replace(self.index_path)

    def _prune(self) -> None:
        """Drop index entries whose embedding is missing (e.g. a half-finished import)."""
        keep = [d for d in self.items if (self.root / d["id"] / "embedding.npy").exists()]
        if len(keep) != len(self.items):
            self.items = keep
            self._save_index()

    # -- queries --------------------------------------------------------
    def list(self, with_thumbs: bool = True) -> list[dict]:
        with self._lock:
            out = []
            for i, d in enumerate(self.items):
                item = {"id": d["id"], "name": d["name"], "hotkey": i + 1 if i < 9 else None}
                if with_thumbs:
                    item["thumb"] = to_data_url(self.root / d["id"] / "thumb.jpg")
                out.append(item)
            return out

    def get(self, face_id: str) -> dict | None:
        return next((d for d in self.items if d["id"] == face_id), None)

    def index_of(self, face_id: str) -> int:
        return next((i for i, d in enumerate(self.items) if d["id"] == face_id), -1)

    def embedding(self, face_id: str) -> np.ndarray:
        return np.load(self.root / face_id / "embedding.npy")

    def latent(self, face_id: str) -> np.ndarray | None:
        """Swapper latent for a face, cached in memory so switching is instant."""
        if face_id is None or self.swapper is None:
            return None
        lat = self._latents.get(face_id)
        if lat is None and self.get(face_id):
            lat = self._latents[face_id] = self.swapper.latent(self.embedding(face_id))
        return lat

    def warm(self) -> None:
        for d in self.items:
            self.latent(d["id"])

    # -- analysis / import ----------------------------------------------
    def analyze(self, src) -> dict:
        """Detect faces for the import dialog's preview (no side effects)."""
        img = decode_image(src)
        faces = detect_for_import(self.detector, img)
        h, w = img.shape[:2]
        return {"width": w, "height": h, "faces": [f.to_dict() for f in faces]}

    def import_face(self, src, name: str, consent: bool) -> dict:
        if not consent:
            raise ImportError_("consent_required", "Confirm this is you, or that this person agreed.")
        name = (name or "").strip()[:40] or "Face"
        with self._lock:
            if len(self.items) >= MAX_FACES:
                raise ImportError_("library_full", f"Your library is full ({MAX_FACES} faces). Delete one first.")
        img = decode_image(src)
        faces = detect_for_import(self.detector, img)
        if not faces:
            raise ImportError_("no_face", "No face found. Try a clear, front-facing photo.")
        if len(faces) > 1:
            raise ImportError_("multiple_faces", "Multiple faces found. Use a photo with one person.")
        face = faces[0]
        emb = self.recognizer.embed(img, face.kps)

        face_id = uuid.uuid4().hex[:12]
        d = self.root / face_id
        d.mkdir(parents=True)
        try:
            cv2.imwrite(str(d / "source.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
            cv2.imwrite(str(d / "thumb.jpg"), thumbnail(img, face.bbox), [cv2.IMWRITE_JPEG_QUALITY, 88])
            np.save(d / "embedding.npy", emb.astype(np.float32))
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise
        with self._lock:
            self.items.append({"id": face_id, "name": name, "created": int(time.time())})
            self._save_index()
        self.latent(face_id)
        return {"id": face_id, "name": name, "hotkey": len(self.items) if len(self.items) <= 9 else None,
                "thumb": to_data_url(d / "thumb.jpg"), "box": face.to_dict()["box"], "score": round(face.score, 3)}

    # -- management -----------------------------------------------------
    def rename(self, face_id: str, name: str) -> dict:
        name = (name or "").strip()[:40]
        if not name:
            raise ImportError_("bad_name", "Name can't be empty.")
        with self._lock:
            d = self.get(face_id)
            if not d:
                raise ImportError_("not_found", "That face no longer exists.")
            d["name"] = name
            self._save_index()
            return {"id": face_id, "name": name}

    def delete(self, face_id: str) -> None:
        with self._lock:
            if not self.get(face_id):
                raise ImportError_("not_found", "That face no longer exists.")
            self.items = [d for d in self.items if d["id"] != face_id]
            self._save_index()
            self._latents.pop(face_id, None)
        shutil.rmtree(self.root / face_id, ignore_errors=True)

    def reorder(self, ids: list[str]) -> None:
        with self._lock:
            by_id = {d["id"]: d for d in self.items}
            ordered = [by_id[i] for i in ids if i in by_id]
            ordered += [d for d in self.items if d["id"] not in set(ids)]
            self.items = ordered
            self._save_index()
