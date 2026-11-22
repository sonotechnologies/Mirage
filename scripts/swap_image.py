"""Milestone 2: detection + static swap on one image file.

    python scripts/swap_image.py --source me.jpg --target photo.jpg --out swapped.jpg [--enhance] [--cpu]
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402

from engine.models import ModelSet  # noqa: E402
from engine.processor import Processor  # noqa: E402
from engine.providers import TIERS  # noqa: E402
from faces.library import decode_image, detect_for_import  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="photo of the face to wear")
    ap.add_argument("--target", required=True, help="image to put the face on")
    ap.add_argument("--out", default="swapped.jpg")
    ap.add_argument("--enhance", action="store_true")
    ap.add_argument("--no-occlusion", action="store_true")
    ap.add_argument("--cpu", action="store_true")
    a = ap.parse_args()

    t0 = time.perf_counter()
    models = ModelSet.load("cpu" if a.cpu else "auto", with_enhancer=a.enhance)
    print(f"models on {models.providers[0]} in {time.perf_counter() - t0:.1f}s")

    src = decode_image(a.source)
    faces = detect_for_import(models.detector, src)
    if len(faces) != 1:
        print(f"source must contain exactly one face (found {len(faces)})")
        return 2
    latent = models.swapper.latent(models.recognizer.embed(src, faces[0].kps))

    tgt = cv2.imread(a.target)
    proc = Processor(models, TIERS["Ultra"])
    proc.set_latent(latent)
    proc.params.enhancer = a.enhance
    proc.params.occlusion = not a.no_occlusion
    res = proc.process(tgt)
    print("face found:", res.face, "timings(ms):", {k: round(v, 1) for k, v in res.timings.items()})
    cv2.imwrite(a.out, res.frame)
    print("wrote", a.out)
    return 0 if res.swapped else 1


if __name__ == "__main__":
    raise SystemExit(main())
