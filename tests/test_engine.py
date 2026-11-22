"""Model-backed tests: need the ONNX models and MIRAGE_TEST_ASSETS photos (see conftest)."""
from __future__ import annotations

import base64

import cv2
import numpy as np
import pytest

from engine import align
from engine.processor import Processor
from engine.providers import TIERS
from faces.library import FaceLibrary, ImportError_, detect_for_import


def _latent(models, path):
    img = cv2.imread(str(path))
    face = detect_for_import(models.detector, img)[0]
    return models.swapper.latent(models.recognizer.embed(img, face.kps))


def test_detector_finds_one_face(models, asset):
    faces = models.detector.detect(cv2.imread(str(asset("target.jpg"))), input_size=640)
    assert len(faces) >= 1 and faces[0].score > 0.5
    assert faces[0].kps.shape == (5, 2)


def test_static_swap_changes_identity(models, asset):
    tgt = cv2.imread(str(asset("target.jpg")))
    lat_a = _latent(models, asset("face_a.jpg"))
    proc = Processor(models, TIERS["Ultra"], async_cpu=False)
    proc.set_latent(lat_a)
    res = proc.process(tgt)
    assert res.swapped
    # The swapped face should be closer to identity A than the original was.
    f0 = models.detector.detect(tgt)[0]
    f1 = models.detector.detect(res.frame)[0]
    emb_a = models.recognizer.embed(cv2.imread(str(asset("face_a.jpg"))),
                                    detect_for_import(models.detector, cv2.imread(str(asset("face_a.jpg"))))[0].kps)
    before = float(models.recognizer.embed(tgt, f0.kps) @ emb_a)
    after = float(models.recognizer.embed(res.frame, f1.kps) @ emb_a)
    assert after > before + 0.15


def test_face_switch_applies_on_next_frame_even_in_async_mode(models, asset):
    tgt = cv2.imread(str(asset("target.jpg")))
    lat_a, lat_b = _latent(models, asset("face_a.jpg")), _latent(models, asset("face_b.jpg"))
    sync = Processor(models, TIERS["Balanced"], async_cpu=False)
    sync.set_latent(lat_b)
    expected = sync.process(tgt).frame
    proc = Processor(models, TIERS["Balanced"], async_cpu=True)
    try:
        proc.set_latent(lat_a)
        proc.process(tgt)
        proc.set_latent(lat_b)
        got = proc.process(tgt).frame
    finally:
        proc.close()
    assert np.abs(got.astype(int) - expected.astype(int)).mean() < 1.0


def test_no_face_passes_frame_through(models, asset):
    blank = np.full((480, 640, 3), 90, np.uint8)
    proc = Processor(models, TIERS["Balanced"], async_cpu=False)
    proc.set_latent(_latent(models, asset("face_a.jpg")))
    res = proc.process(blank)
    assert not res.face and res.frame is blank


def test_hand_over_face_stays_visible(models, asset):
    tgt = cv2.imread(str(asset("target.jpg")))
    f = models.detector.detect(tgt)[0]
    x1, y1, x2, y2 = f.bbox.astype(int)
    occluded = tgt.copy()
    hx = (x1 + x2) // 2
    occluded[y1 + (y2 - y1) // 3:y2, hx:x2] = (60, 90, 170)       # a flat "hand" over half the lower face
    proc = Processor(models, TIERS["Ultra"], async_cpu=False)
    proc.set_latent(_latent(models, asset("face_a.jpg")))
    proc.tracker.reset()
    # Seed landmarks from the unoccluded frame so detection isn't the thing under test.
    proc.process(tgt)
    out = proc.process(occluded).frame
    patch = (slice(y1 + (y2 - y1) // 2, y2 - 4), slice(hx + 4, x2 - 4))
    diff = np.abs(out[patch].astype(int) - occluded[patch].astype(int)).mean()
    assert diff < 25, f"hand region was overwritten by the swap (mean diff {diff:.1f})"


def test_library_import_rules(models, asset, tmp_path):
    lib = FaceLibrary(models.detector, models.recognizer, models.swapper, root=tmp_path / "faces")
    with pytest.raises(ImportError_) as e:
        lib.import_face(str(asset("face_a.jpg")), "A", consent=False)
    assert e.value.code == "consent_required"
    with pytest.raises(ImportError_) as e:
        lib.import_face(str(asset("group.jpg")), "G", consent=True)
    assert e.value.code == "multiple_faces"
    blank = cv2.imencode(".png", np.full((300, 300, 3), 128, np.uint8))[1].tobytes()
    with pytest.raises(ImportError_) as e:
        lib.import_face("data:image/png;base64," + base64.b64encode(blank).decode(), "N", consent=True)
    assert e.value.code == "no_face"

    a = lib.import_face(str(asset("face_a.jpg")), "Tolu", consent=True)
    b = lib.import_face(str(asset("face_b.jpg")), "Ada", consent=True)
    assert [x["name"] for x in lib.list()] == ["Tolu", "Ada"]
    lib.reorder([b["id"], a["id"]])
    lib.rename(a["id"], "Tolu B")
    # persisted across a reload
    lib2 = FaceLibrary(models.detector, models.recognizer, models.swapper, root=tmp_path / "faces")
    assert [x["name"] for x in lib2.list()] == ["Ada", "Tolu B"]
    assert lib2.latent(a["id"]).shape == (1, 512)
    lib2.delete(b["id"])
    assert [x["id"] for x in lib2.list()] == [a["id"]]
    assert not (tmp_path / "faces" / b["id"]).exists()


def test_library_max_12(models, asset, tmp_path):
    lib = FaceLibrary(models.detector, models.recognizer, models.swapper, root=tmp_path / "faces")
    for i in range(12):
        lib.import_face(str(asset("face_a.jpg")), f"F{i}", consent=True)
    with pytest.raises(ImportError_) as e:
        lib.import_face(str(asset("face_a.jpg")), "F13", consent=True)
    assert e.value.code == "library_full"


def test_headless_pipeline_on_video(models, asset, tmp_path):
    """--video / --no-vcam path: frames flow, a face is swapped, FPS is measured."""
    import time

    from core import MirageCore
    tgt = cv2.imread(str(asset("target.jpg")))
    h, w = tgt.shape[:2]
    vid = tmp_path / "clip.mp4"
    vw = cv2.VideoWriter(str(vid), cv2.VideoWriter_fourcc(*"mp4v"), 25, (w, h))
    for i in range(50):
        m = np.float32([[1, 0, 4 * np.sin(i / 5)], [0, 1, 0]])
        vw.write(cv2.warpAffine(tgt, m, (w, h), borderMode=cv2.BORDER_REPLICATE))
    vw.release()
    events = []
    core = MirageCore(emit=lambda e, d: events.append(e), video=str(vid), no_vcam=True, enable_hotkeys=False)
    core.settings.set("tier", "Performance")
    core.boot()
    try:
        assert core.status == "ready"
        r = core.import_face(str(asset("face_a.jpg")), "A", True)
        core.select_face(r["face"]["id"])
        assert core.start()["ok"]
        time.sleep(4)
        st = core.pipeline.stats()
        assert st["fps"] > 5
        assert st["face"] is True
        assert "preview" in events and "stats" in events
        assert core.pipeline.vcam.sent > 20            # NullCamera received frames
    finally:
        core.shutdown()
