"""Fast tests with no models or photos."""
from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from engine import align
from engine.pipeline import Latest, Rate
from engine.processor import Params, change_mask, ellipse_mask, paste_back
from engine.providers import TIERS, pick_tier
from engine.tracker import OneEuro


def test_similarity_recovers_known_transform():
    ang, s, t = np.deg2rad(12), 1.7, np.array([30.0, -12.0])
    r = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]])
    src = align.ARCFACE_112.astype(np.float64)
    dst = (s * src @ r.T) + t
    m = align.similarity(src, dst)
    np.testing.assert_allclose(m[:, :2], s * r, atol=1e-4)
    np.testing.assert_allclose(m[:, 2], t, atol=1e-3)


def test_compose_and_invert_roundtrip():
    m = align.similarity(align.ARCFACE_112, align.ARCFACE_128 * 2)
    ident = align.compose(m, align.invert(m))
    np.testing.assert_allclose(ident, [[1, 0, 0], [0, 1, 0]], atol=1e-5)


def test_paste_back_full_mask_copies_face_and_leaves_rest():
    frame = np.full((200, 300, 3), 10, np.uint8)
    face = np.full((128, 128, 3), 200, np.uint8)
    m = np.float32([[1, 0, -50], [0, 1, -40]])     # frame -> crop: crop starts at (50,40)
    out = paste_back(frame, face, np.ones((128, 128), np.float32), m)
    assert out[100, 100].tolist() == [200, 200, 200]
    assert out[10, 10].tolist() == [10, 10, 10]
    assert out[190, 290].tolist() == [10, 10, 10]


def test_ellipse_mask_softer_with_more_blend():
    hard, soft = ellipse_mask(128, 0), ellipse_mask(128, 100)
    assert hard[70, 64] > 0.95 and soft[70, 64] > 0.5
    # a softer feather spreads further from the edge
    assert (soft > 0.05).sum() > (hard > 0.05).sum()
    assert hard[0, 0] == 0


def test_change_mask_ignores_jitter_but_flags_a_hand():
    rng = np.random.default_rng(1)
    base = (rng.random((128, 128, 3)) * 60 + 120).astype(np.uint8)
    base = cv2.GaussianBlur(base, (0, 0), 3)
    jitter = np.roll(base, 1, axis=1)
    assert change_mask(base, jitter).min() > 0.9
    hand = base.copy()
    hand[20:128, 0:60] = (40, 60, 200)
    m = change_mask(base, hand)
    assert m[90, 20] < 0.2         # covered by the hand -> live pixels win
    assert m[60, 115] > 0.9        # untouched cheek -> swapped face stays


def test_params_clamp_and_reject_unknown():
    p = Params()
    p.update("blend", 150)
    p.update("sharpness", -3)
    p.update("swap", 0)
    assert (p.blend, p.sharpness, p.swap) == (100, 0, False)
    with pytest.raises(KeyError):
        p.update("nope", 1)


def test_one_euro_smooths_noise_and_follows_motion():
    f = OneEuro(min_cutoff=1.0, beta=0.0)
    rng = np.random.default_rng(0)
    outs = [f(np.array([100.0 + rng.normal(0, 2)]), t / 30) for t in range(60)]
    assert np.std(outs[10:]) < 1.0
    g = OneEuro()
    for t in range(30):
        y = g(np.array([float(t * 10)]), t / 30)
    assert abs(float(y[0]) - 290) < 30


def test_latest_slot_drops_stale_items():
    q = Latest()
    for i in range(5):
        q.put(i)
    seq, item = q.get(0, timeout=0.01)
    assert item == 4 and seq == 5
    assert q.get(seq, timeout=0.01)[1] is None


def test_rate_counts_events_per_second():
    r = Rate(window=2)
    for i in range(31):
        r.tick(i / 30)
    assert 29 < r.value() < 31


def test_pick_tier():
    fast_gpu = {"detect": 2, "swap": 8, "occlude": 3, "enhance": 12}
    assert pick_tier(fast_gpu, gpu=True) == "Ultra"
    igpu = {"detect": 4, "swap": 22, "occlude": 6, "enhance": 60}
    assert pick_tier(igpu, gpu=True) == "Balanced"
    cpu = {"detect": 8, "swap": 250, "occlude": 60, "enhance": 900}
    assert pick_tier(cpu, gpu=False) == "Performance"
    assert pick_tier(fast_gpu, gpu=False) != "Ultra"   # Ultra is GPU-only


def test_tiers_match_brief():
    assert (TIERS["Ultra"].height, TIERS["Balanced"].height, TIERS["Performance"].height) == (1080, 720, 480)
    assert [TIERS[t].detect_every for t in ("Ultra", "Balanced", "Performance")] == [1, 2, 5]
    assert not TIERS["Performance"].enhancer_allowed


def test_settings_persist_and_merge_new_defaults(isolated_home):
    from config import DEFAULTS, Settings
    s = Settings()
    s.set_param("blend", 12)
    s.set("tier", "Balanced")
    raw = json.loads(s.path.read_text())
    raw["params"].pop("occlusion")           # an older settings file missing a key
    s.path.write_text(json.dumps(raw))
    s2 = Settings()
    assert s2.get("params")["blend"] == 12
    assert s2.get("tier") == "Balanced"
    assert s2.get("params")["occlusion"] == DEFAULTS["params"]["occlusion"]


def test_settings_survive_corrupt_file(isolated_home):
    from config import Settings
    p = isolated_home / "settings.json"
    isolated_home.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json")
    assert Settings(p).get("mirror") is True


@pytest.mark.parametrize("combo,expected", [
    ("Ctrl+Alt+1", "ctrl+alt+1"), ("alt+ctrl+→", "ctrl+alt+right"), ("Shift+Win+F5", "shift+windows+f5")])
def test_hotkey_normalise(combo, expected):
    from hotkeys import normalise
    assert normalise(combo) == expected


@pytest.mark.parametrize("bad", ["1", "ctrl+alt", "ctrl+a+b"])
def test_hotkey_normalise_rejects(bad):
    from hotkeys import normalise
    with pytest.raises(ValueError):
        normalise(bad)


def test_bridge_wraps_errors():
    from bridge.api import Api
    from faces.library import ImportError_

    class Core:
        def import_face(self, *a):
            raise ImportError_("no_face", "No face found.")

        def start(self):
            raise RuntimeError("Mirage is still starting")

        def stop(self):
            return {"ok": True}

        def get_state(self):
            return {"status": "ready"}

    api = Api(Core())
    assert api.import_face("x", "n", True) == {"ok": False, "error": "no_face", "message": "No face found."}
    assert api.start()["error"] == "not_ready"
    assert api.get_state() == {"ok": True, "status": "ready"}
    # pywebview must not see the core through attributes
    assert all(k.startswith("_") for k in vars(api))


def test_decode_image_rejects_garbage():
    from faces.library import ImportError_, decode_image
    with pytest.raises(ImportError_):
        decode_image("data:text/plain;base64,aGVsbG8=")
    with pytest.raises(ImportError_):
        decode_image("bm90IGFuIGltYWdl")
