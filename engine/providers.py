"""Execution provider selection (DirectML / CPU), ONNX sessions, perf tiers, benchmark."""
from __future__ import annotations

import logging
import os
import platform
import subprocess
import time
from dataclasses import dataclass, asdict
from typing import Callable

import onnxruntime as ort

log = logging.getLogger("mirage.providers")

DML = "DmlExecutionProvider"
CPU = "CPUExecutionProvider"


@dataclass(frozen=True)
class Tier:
    name: str
    width: int
    height: int
    detect_every: int
    enhancer_allowed: bool
    enhancer_default: bool
    occlusion_every: int
    occlusion_lowres: bool
    target_fps: int
    expected: str


TIERS: dict[str, Tier] = {
    "Ultra": Tier("Ultra", 1920, 1080, 1, True, True, 1, False, 30, "30"),
    "Balanced": Tier("Balanced", 1280, 720, 2, True, False, 2, False, 30, "25–30"),
    "Performance": Tier("Performance", 854, 480, 5, False, False, 3, True, 20, "15–20"),
}
TIER_ORDER = ["Ultra", "Balanced", "Performance"]


def physical_cores() -> int:
    try:
        import psutil  # optional
        n = psutil.cpu_count(logical=False)
        if n:
            return n
    except Exception:
        pass
    n = os.cpu_count() or 2
    return max(1, n // 2) if n > 2 else n


def available() -> list[str]:
    return ort.get_available_providers()


def resolve(preference: str = "auto") -> list[str]:
    """Provider list for a preference: auto | directml | cpu."""
    avail = available()
    if preference in ("auto", "directml") and DML in avail:
        return [DML, CPU]
    return [CPU]


def is_gpu(providers: list[str]) -> bool:
    return providers[0] == DML


def make_session(path: str, providers: list[str]) -> ort.InferenceSession:
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    opts.log_severity_level = 3
    if DML in providers:
        # DirectML requires these two settings.
        opts.enable_mem_pattern = False
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    else:
        opts.intra_op_num_threads = physical_cores()
        opts.inter_op_num_threads = 1
    try:
        return ort.InferenceSession(path, sess_options=opts, providers=providers)
    except Exception as e:  # DirectML init can fail on old drivers: fall back to CPU
        if DML in providers:
            log.warning("DirectML session failed for %s (%s); using CPU", path, e)
            return make_session(path, [CPU])
        raise


_gpu_name_cache: str | None = None


def gpu_name() -> str:
    """Primary display adapter name (Windows), else a CPU description."""
    global _gpu_name_cache
    if _gpu_name_cache is not None:
        return _gpu_name_cache
    name = ""
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) -join '|'"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
            names = [n.strip() for n in out.split("|") if n.strip()]
            # Prefer a discrete GPU when several adapters are present.
            names.sort(key=lambda n: (not any(k in n for k in ("NVIDIA", "Radeon RX", "Arc")), n))
            name = names[0] if names else ""
        except Exception:
            name = ""
    _gpu_name_cache = name or platform.processor() or "CPU"
    return _gpu_name_cache


def provider_label(providers: list[str]) -> dict:
    gpu = is_gpu(providers)
    return {"kind": "DirectML" if gpu else "CPU", "device": gpu_name() if gpu else "CPU", "gpu": gpu}


def pick_tier(ms_per_stage: dict, gpu: bool) -> str:
    """Pick the best tier whose estimated FPS clears its lower target.

    ms_per_stage: measured mean ms for 'detect', 'swap', 'enhance', 'occlude'
    at a single face; resolution scaling is approximated from capture size.
    """
    for name in TIER_ORDER:
        t = TIERS[name]
        if name == "Ultra" and not gpu:
            continue
        per_frame = ms_per_stage["swap"] + ms_per_stage["detect"] / t.detect_every
        per_frame += ms_per_stage["occlude"] / t.occlusion_every * (0.5 if t.occlusion_lowres else 1)
        if t.enhancer_default:
            per_frame += ms_per_stage["enhance"]
        per_frame += 4 + (t.width * t.height) / (1280 * 720) * 3  # capture/paste/encode overhead
        fps = 1000.0 / per_frame
        floor = {"Ultra": 30, "Balanced": 25, "Performance": 0}[name]
        if fps >= floor:
            return name
    return "Performance"


def run_benchmark(models, seconds: float = 3.0,
                  progress: Callable[[float, str], None] | None = None) -> dict:
    """Time each model on dummy input for ~`seconds` total and choose a tier.

    `models` is an engine.models.ModelSet. Returns a JSON-able dict.
    """
    import numpy as np

    progress = progress or (lambda p, s: None)
    rng = np.random.default_rng(0)
    frame = (rng.random((360, 640, 3)) * 255).astype(np.uint8)
    crop128 = (rng.random((128, 128, 3)) * 255).astype(np.uint8)
    crop512 = (rng.random((512, 512, 3)) * 255).astype(np.uint8)
    latent = rng.standard_normal((1, 512)).astype(np.float32)
    latent /= np.linalg.norm(latent)

    stages = [
        ("detect", lambda: models.detector.detect(frame)),
        ("swap", lambda: models.swapper.swap(crop128, latent)),
        ("occlude", lambda: models.occluder.mask(crop128) if models.occluder else None),
        ("enhance", lambda: models.enhancer.enhance(crop512) if models.enhancer else None),
    ]
    budget = seconds / len(stages)
    result: dict[str, float] = {}
    for i, (name, fn) in enumerate(stages):
        fn()  # warm-up (session init, DML shader compile)
        times = []
        t_end = time.perf_counter() + budget
        while time.perf_counter() < t_end or not times:
            t0 = time.perf_counter()
            fn()
            times.append((time.perf_counter() - t0) * 1000)
            progress((i + min(1.0, len(times) / 8)) / len(stages), name)
            if len(times) >= 30:
                break
        times.sort()
        result[name] = round(times[len(times) // 2], 2)
        progress((i + 1) / len(stages), name)
    gpu = is_gpu(models.providers)
    tier = pick_tier(result, gpu)
    return {"ms": result, "tier": tier, "expected_fps": TIERS[tier].expected,
            "swap_fps": round(1000.0 / max(result["swap"], 1e-3), 1),
            "provider": provider_label(models.providers)}


def tier_dict(name: str) -> dict:
    return asdict(TIERS[name])
