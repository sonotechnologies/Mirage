"""Model manifest and the loaded model set (one ONNX session per model, reused)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import paths
from . import providers as prov
from .detector import Detector
from .enhancer import Enhancer
from .occlusion import Occluder
from .swapper import Recognizer, Swapper

log = logging.getLogger("mirage.models")

_FF = "https://github.com/facefusion/facefusion-assets/releases/download"

# file name -> download source + SHA-256 of the final file. Used by build/fetch_models.py.
MANIFEST: dict[str, dict] = {
    "det_500m.onnx": {
        "url": "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip",
        "zip_member": "det_500m.onnx",
        "sha256": "5e4447f50245bbd7966bd6c0fa52938c61474a04ec7def48753668a9d8b4ea3a",
    },
    "arcface_w600k_r50.onnx": {
        "url": f"{_FF}/models-3.0.0/arcface_w600k_r50.onnx",
        "sha256": "f1f79dc3b0b79a69f94799af1fffebff09fbd78fd96a275fd8f0cbbea23270d1",
    },
    "inswapper_128.onnx": {
        "url": f"{_FF}/models-3.0.0/inswapper_128.onnx",
        "sha256": "a290273ed497312095dac48cdef20feec9d5208298223dd01288ab202b54bea7",
    },
    "inswapper_128_fp16.onnx": {
        "url": f"{_FF}/models-3.0.0/inswapper_128_fp16.onnx",
        "sha256": "c4eccca86ad177586c85c28bf1a64a9d9ed237e283a15818d831f7facfd3f420",
    },
    "gfpgan_1.4.onnx": {
        "url": f"{_FF}/models-3.0.0/gfpgan_1.4.onnx",
        "sha256": "accc4757b26bdb89b32b4d3500d4f79c9dff97c1dd7c7104bf9dcb95e3311385",
    },
    "xseg_1.onnx": {
        "url": f"{_FF}/models-3.1.0/xseg_1.onnx",
        "sha256": "c4d1498b8a03b5fe2a3a5d2ef2a0402ab03bd51edaf5b2d8d5fb764702a97dd3",
    },
}
# inswapper's embedding projection matrix, extracted from the model at build time
# so the app does not need the `onnx` package at runtime.
EMAP_FILE = "inswapper_emap.npy"
EMAP_SHA256 = "cee626bc81721d71c5d6cb1f76f830b9ae46f595514b0884dd8ae34785576764"

REQUIRED = ["det_500m.onnx", "arcface_w600k_r50.onnx", "inswapper_128.onnx"]


class ModelsMissing(RuntimeError):
    pass


def load_emap(models_dir: Path, swapper_path: Path) -> np.ndarray:
    p = models_dir / EMAP_FILE
    if p.exists():
        return np.load(p)
    try:  # dev fallback: read it straight from the model
        import onnx
        from onnx import numpy_helper
        emap = numpy_helper.to_array(onnx.load(str(swapper_path)).graph.initializer[-1]).astype(np.float32)
        np.save(p, emap)
        return emap
    except ImportError as e:
        raise ModelsMissing(f"{EMAP_FILE} missing; run build/fetch_models.py") from e


@dataclass
class ModelSet:
    providers: list[str]
    detector: Detector
    recognizer: Recognizer
    swapper: Swapper
    enhancer: Enhancer | None = None
    occluder: Occluder | None = None
    files: dict = field(default_factory=dict)

    @classmethod
    def load(cls, provider_pref: str = "auto", models_dir: Path | None = None,
             with_enhancer: bool = True, with_occluder: bool = True) -> "ModelSet":
        d = models_dir or paths.models_dir()
        missing = [f for f in REQUIRED if not (d / f).exists()]
        if missing:
            raise ModelsMissing(f"Missing models in {d}: {', '.join(missing)}. Run build/fetch_models.py")
        providers = prov.resolve(provider_pref)
        gpu = prov.is_gpu(providers)
        swap_file = "inswapper_128_fp16.onnx" if gpu and (d / "inswapper_128_fp16.onnx").exists() else "inswapper_128.onnx"
        mk = lambda f: prov.make_session(str(d / f), providers)
        # The recognizer runs only at import time; keep it on CPU so it never competes with the live pipeline.
        recog = Recognizer(prov.make_session(str(d / "arcface_w600k_r50.onnx"), [prov.CPU]))
        swap_sess = mk(swap_file)
        ms = cls(
            providers=list(swap_sess.get_providers()),
            detector=Detector(mk("det_500m.onnx")),
            recognizer=recog,
            swapper=Swapper(swap_sess, load_emap(d, d / "inswapper_128.onnx")),
            files={"swapper": swap_file},
        )
        if with_enhancer and (d / "gfpgan_1.4.onnx").exists():
            ms.enhancer = Enhancer(mk("gfpgan_1.4.onnx"))
        if with_occluder and (d / "xseg_1.onnx").exists():
            ms.occluder = Occluder(mk("xseg_1.onnx"))
        log.info("models loaded on %s (%s)", ms.providers[0], swap_file)
        return ms
