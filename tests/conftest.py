"""Shared fixtures.

Model-backed tests need the ONNX models (python build/fetch_models.py) and a few
face photos you supply, because the repo does not ship photos of real people:

    MIRAGE_TEST_ASSETS=/path/to/dir   containing
        face_a.jpg   one clear face (the identity to wear)
        face_b.jpg   a different single face
        target.jpg   a photo with one face to swap onto
        group.jpg    a photo with several faces

Tests that need missing files are skipped, not failed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRAGE_HOME", str(tmp_path / "home"))
    yield tmp_path / "home"


def _models_dir() -> Path:
    return Path(os.environ.get("MIRAGE_MODELS", ROOT / "models"))


def _assets() -> Path | None:
    p = os.environ.get("MIRAGE_TEST_ASSETS")
    return Path(p) if p else None


@pytest.fixture(scope="session")
def models():
    from engine.models import REQUIRED, ModelSet
    d = _models_dir()
    if not all((d / f).exists() for f in REQUIRED):
        pytest.skip("models not downloaded (run build/fetch_models.py)")
    os.environ["MIRAGE_MODELS"] = str(d)
    return ModelSet.load("cpu", models_dir=d, with_enhancer=False)


@pytest.fixture(scope="session")
def asset():
    def get(name: str) -> Path:
        d = _assets()
        if not d or not (d / name).exists():
            pytest.skip(f"test asset {name} not provided (set MIRAGE_TEST_ASSETS)")
        return d / name
    return get
