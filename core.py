"""MirageCore: owns settings, models, face library, pipeline and hotkeys.

Both the pywebview bridge (bridge/api.py) and the headless runner use this.
Events go out through `emit(event, data)`; the UI receives them via
window.mirage.on(event, data).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

import paths
from config import PARAM_KEYS, VERSION, Settings
from engine import providers as prov
from engine.capture import CameraSource, VideoSource, list_cameras, parse_res
from engine.models import ModelSet, ModelsMissing
from engine.pipeline import Pipeline
from engine.vcam import NullCamera, VirtualCamera
from faces.library import FaceLibrary
from hotkeys import ACTIONS, Hotkeys, normalise

log = logging.getLogger("mirage.core")


class MirageCore:
    def __init__(self, emit: Callable[[str, dict], None] | None = None, video: str | None = None,
                 no_vcam: bool = False, settings: Settings | None = None, enable_hotkeys: bool = True):
        self.emit = emit or (lambda e, d: None)
        self.video = video
        self.no_vcam = no_vcam
        self.settings = settings or Settings()
        self.models: ModelSet | None = None
        self.library: FaceLibrary | None = None
        self.pipeline: Pipeline | None = None
        self.status = "loading"          # loading | benchmarking | ready | error
        self.load_error: str | None = None
        self.benchmark: dict | None = self.settings.get("benchmark")
        self._lock = threading.RLock()
        self._enhancer_loading = False
        self._cameras: list[dict] | None = None
        self.hotkeys = Hotkeys(self.on_hotkey) if enable_hotkeys else None
        self.t_start = time.perf_counter()

    # ------------------------------------------------------------------ boot
    def boot_async(self) -> threading.Thread:
        t = threading.Thread(target=self.boot, name="mirage-boot", daemon=True)
        t.start()
        return t

    def boot(self) -> None:
        """Load models, benchmark on first launch, start the preview pipeline."""
        try:
            self.models = ModelSet.load(self.settings.get("provider"), with_enhancer=False)
        except ModelsMissing as e:
            self._fail("models_missing", str(e))
            return
        except Exception as e:  # noqa: BLE001
            log.exception("model load failed")
            self._fail("models_failed", str(e))
            return
        self.library = FaceLibrary(self.models.detector, self.models.recognizer, self.models.swapper)
        self.library.warm()

        if not self.settings.get("tier"):
            self.status = "benchmarking"
            self.emit("benchmark_progress", {"progress": 0.0, "stage": "start",
                                             "provider": prov.provider_label(self.models.providers)})
            self._run_benchmark()
        self._start_pipeline()
        if self.hotkeys:
            errs = self.hotkeys.apply(self.settings.get("hotkeys"))
            if errs and self.hotkeys.available:
                self.emit("error", {"code": "hotkeys", "message": "Some hotkeys could not be registered: "
                                    + ", ".join(errs)})
        self.status = "ready"
        log.info("ready in %.1fs", time.perf_counter() - self.t_start)
        self.emit("ready", self.get_state())

    def _fail(self, code: str, message: str) -> None:
        self.status = "error"
        self.load_error = message
        self.emit("error", {"code": code, "message": message})

    def _run_benchmark(self) -> dict:
        def progress(p, stage):
            self.emit("benchmark_progress", {"progress": round(p, 3), "stage": stage,
                                             "provider": prov.provider_label(self.models.providers)})
        res = prov.run_benchmark(self.models, 3.0, progress)
        self.benchmark = res
        self.settings.set("benchmark", res, save=False)
        self.settings.set("tier", res["tier"])
        self.emit("benchmark_progress", {"progress": 1.0, "stage": "done", "result": res})
        return res

    # -------------------------------------------------------------- pipeline
    @property
    def tier(self) -> prov.Tier:
        return prov.TIERS.get(self.settings.get("tier") or "Balanced", prov.TIERS["Balanced"])

    def _source_factory(self):
        t = self.tier
        w, h = parse_res(self.settings.get("resolution"), (t.width, t.height))
        if self.video:
            return lambda: VideoSource(self.video, w, h)
        cam = int(self.settings.get("camera") or 0)
        return lambda: CameraSource(cam, w, h, 30)

    def _vcam_factory(self):
        return NullCamera if self.no_vcam else VirtualCamera

    def _start_pipeline(self) -> None:
        p = Pipeline(self.models, self.tier, self.emit, self._source_factory(), self._vcam_factory())
        p.mirror_preview = bool(self.settings.get("mirror"))
        for k, v in self.settings.get("params").items():
            if k in PARAM_KEYS:
                p.set_param(k, v)
        active = self.settings.get("active_face")
        if active and not self.library.get(active):
            active = None
        if active is None and self.library.items:
            active = self.library.items[0]["id"]
        self.settings.set("active_face", active)
        p.set_latent(self.library.latent(active))
        self.pipeline = p
        if self.settings.get("params").get("enhancer"):
            self._ensure_enhancer()
        p.start()

    def _ensure_enhancer(self) -> None:
        if not self.models or self.models.enhancer or self._enhancer_loading or not self.tier.enhancer_allowed:
            return
        path = paths.models_dir() / "gfpgan_1.4.onnx"
        if not path.exists():
            return
        self._enhancer_loading = True

        def load():
            try:
                from engine.enhancer import Enhancer
                self.models.enhancer = Enhancer(prov.make_session(str(path), self.models.providers))
            except Exception as e:  # noqa: BLE001
                self.emit("error", {"code": "enhancer_failed", "message": str(e)})
            finally:
                self._enhancer_loading = False
        threading.Thread(target=load, name="mirage-enhancer-load", daemon=True).start()

    def shutdown(self) -> None:
        if self.hotkeys:
            self.hotkeys.close()
        if self.pipeline:
            self.pipeline.shutdown()

    # ----------------------------------------------------------------- state
    def faces(self) -> list[dict]:
        return self.library.list() if self.library else []

    def get_state(self) -> dict:
        s = self.settings.snapshot()
        tier = s.get("tier")
        prov_info = prov.provider_label(self.models.providers) if self.models else {
            "kind": "…", "device": prov.gpu_name() if os.name == "nt" else "…", "gpu": False}
        return {
            "status": self.status,
            "error": self.load_error,
            "version": VERSION,
            "settings": {k: s[k] for k in ("camera", "resolution", "mirror", "tier", "provider", "params",
                                           "hotkeys", "helper_seen", "active_face")},
            "faces": self.faces(),
            "cameras": self._cameras or [],
            "tiers": {n: prov.tier_dict(n) for n in prov.TIER_ORDER},
            "tier": tier,
            "benchmark": self.benchmark,
            "provider": prov_info,
            "providers_available": prov.available(),
            "live": bool(self.pipeline and self.pipeline.live),
            "vcam": {"name": "Mirage Camera", "disabled": self.no_vcam},
            "source": {"video": self.video} if self.video else {"camera": s.get("camera")},
            "hotkeys_available": bool(self.hotkeys and self.hotkeys.available),
            "async_swap": bool(self.pipeline and self.pipeline.processor.async_mode),
            "stats": self.pipeline.stats() if self.pipeline else None,
        }

    def list_cameras(self, refresh: bool = False) -> list[dict]:
        if self.video:
            return [{"id": 0, "name": f"Video file ({os.path.basename(self.video)})", "resolutions": []}]
        if self._cameras is None or refresh:
            # Probing a camera that is already open can fail; skip resolution probe for the active one.
            self._cameras = list_cameras()
            active = self.settings.get("camera")
            if self.pipeline and self.pipeline.source and not any(c["id"] == active for c in self._cameras):
                self._cameras.insert(0, {"id": active, "name": f"Camera {active + 1}",
                                         "resolutions": [f"{self.pipeline.size[0]}x{self.pipeline.size[1]}"]})
        return self._cameras

    # -------------------------------------------------------------- actions
    def require_ready(self) -> None:
        if self.status != "ready" or not self.pipeline:
            raise RuntimeError("Mirage is still starting")

    def set_camera(self, cam_id, resolution: str | None = None) -> dict:
        self.settings.set("camera", int(cam_id), save=False)
        self.settings.set("resolution", resolution or None)
        if self.pipeline:
            ok = self.pipeline.restart_source(self._source_factory())
            return {"ok": ok, "size": list(self.pipeline.size)}
        return {"ok": True}

    def start(self) -> dict:
        self.require_ready()
        res = self.pipeline.go_live()
        if res.get("ok"):
            first = not self.settings.get("helper_seen")
            res["show_helper"] = first
            self.emit("live", {"live": True, "device": res.get("device")})
        return res

    def stop(self) -> dict:
        if self.pipeline:
            self.pipeline.stop_live()
        self.emit("live", {"live": False})
        return {"ok": True}

    def select_face(self, face_id: str | None) -> dict:
        self.require_ready()
        if face_id is not None and not self.library.get(face_id):
            return {"ok": False, "error": "not_found"}
        self.pipeline.set_latent(self.library.latent(face_id))
        self.settings.set("active_face", face_id)
        idx = self.library.index_of(face_id) if face_id else -1
        data = {"id": face_id, "index": idx, "name": self.library.get(face_id)["name"] if face_id else None}
        self.emit("face_selected", data)
        return {"ok": True, **data}

    def set_param(self, key: str, value) -> dict:
        if key == "mirror":
            self.settings.set("mirror", bool(value))
            if self.pipeline:
                self.pipeline.mirror_preview = bool(value)
            return {"ok": True}
        if key == "view":
            if value not in ("Mirage", "Original", "Split"):
                return {"ok": False, "error": "bad_value"}
            if self.pipeline:
                self.pipeline.view = value
            return {"ok": True}
        if key not in PARAM_KEYS:
            return {"ok": False, "error": "unknown_param", "message": key}
        if key == "enhancer" and value and not self.tier.enhancer_allowed:
            return {"ok": False, "error": "enhancer_disabled", "message": "The enhancer is off on the Performance tier."}
        if self.pipeline:
            self.pipeline.set_param(key, value)
            value = getattr(self.pipeline.processor.params, key)
        self.settings.set_param(key, value)
        if key == "enhancer" and value:
            self._ensure_enhancer()
        return {"ok": True, "key": key, "value": value}

    def set_tier(self, tier: str) -> dict:
        if tier not in prov.TIERS:
            return {"ok": False, "error": "bad_tier"}
        self.settings.set("tier", tier)
        if self.pipeline:
            self.pipeline.set_tier(prov.TIERS[tier])
            if not prov.TIERS[tier].enhancer_allowed:
                self.pipeline.set_param("enhancer", False)
            elif self.settings.get("params").get("enhancer"):
                self.pipeline.set_param("enhancer", True)
                self._ensure_enhancer()
            if not self.settings.get("resolution"):
                self.pipeline.restart_source(self._source_factory())
        self.emit("tier", {"tier": tier})
        return {"ok": True, "tier": tier}

    def run_benchmark(self) -> dict:
        self.require_ready()
        # Pause processing so the benchmark measures the hardware, not contention.
        self.pipeline.preview_enabled = False
        try:
            with self.pipeline._proc_lock:
                res = self._run_benchmark()
        finally:
            self.pipeline.preview_enabled = True
        self.set_tier(res["tier"])
        return {"ok": True, **res}

    def set_provider(self, pref: str) -> dict:
        if pref not in ("auto", "directml", "cpu"):
            return {"ok": False, "error": "bad_provider"}
        self.settings.set("provider", pref)
        was_live = bool(self.pipeline and self.pipeline.live)
        if self.pipeline:
            self.pipeline.shutdown()
            self.pipeline = None
        self.status = "loading"
        self.models = ModelSet.load(pref, with_enhancer=False)
        self.library.swapper = self.models.swapper
        self.library.detector, self.library.recognizer = self.models.detector, self.models.recognizer
        self.library._latents.clear()
        self.library.warm()
        self._start_pipeline()
        self.status = "ready"
        if was_live:
            self.pipeline.go_live()
        state = self.get_state()
        self.emit("ready", state)
        return {"ok": True, "provider": state["provider"]}

    def set_hotkey(self, action: str, combo: str | None) -> dict:
        if action not in ACTIONS:
            return {"ok": False, "error": "bad_action"}
        hk = self.settings.get("hotkeys")
        if combo:
            combo = normalise(combo)
            clash = next((a for a, c in hk.items() if a != action and c == combo), None)
            if clash:
                return {"ok": False, "error": "hotkey_in_use", "message": f"{combo} is already used by {clash}"}
        hk[action] = combo or ""
        self.settings.set("hotkeys", hk)
        errs = self.hotkeys.apply(hk) if self.hotkeys else {}
        if action in errs and self.hotkeys and self.hotkeys.available:
            return {"ok": False, "error": "hotkey_failed", "message": errs[action]}
        return {"ok": True, "hotkeys": hk}

    def on_hotkey(self, action: str) -> None:
        if not self.library or not self.pipeline:
            return
        ids = [d["id"] for d in self.library.items]
        current = self.settings.get("active_face")
        if action.startswith("face_"):
            n = int(action.split("_")[1]) - 1
            if n < len(ids):
                if not self.pipeline.processor.params.swap:
                    self.set_param("swap", True)
                    self.emit("param", {"key": "swap", "value": True})
                self.select_face(ids[n])
        elif action == "bypass":
            on = not self.pipeline.processor.params.swap
            self.set_param("swap", on)
            self.emit("param", {"key": "swap", "value": on})
        elif action in ("next_face", "prev_face") and ids:
            i = ids.index(current) if current in ids else -1
            i = (i + (1 if action == "next_face" else -1)) % len(ids)
            self.select_face(ids[i])
        elif action == "toggle_live":
            self.stop() if self.pipeline.live else self.start()

    def fix_error(self, code: str) -> dict:
        if code in ("camera_in_use", "camera_lost", "camera_failed"):
            ok = self.pipeline.restart_source() if self.pipeline else False
            return {"ok": ok}
        if code == "vcam_missing":
            return register_vcam_driver()
        if code in ("models_missing", "models_failed"):
            self.status = "loading"
            self.load_error = None
            self.boot_async()
            return {"ok": True}
        return {"ok": False, "error": "unknown_code"}

    def dismiss_helper(self) -> dict:
        self.settings.set("helper_seen", True)
        return {"ok": True}

    # ---------------------------------------------------------------- faces
    def import_face(self, src, name: str, consent: bool) -> dict:
        self.require_library()
        face = self.library.import_face(src, name, bool(consent))
        if self.settings.get("active_face") is None:
            self.select_face(face["id"])
        self.emit("faces", {"faces": self.faces()})
        return {"ok": True, "face": face}

    def require_library(self) -> None:
        if not self.library:
            raise RuntimeError("Mirage is still starting")

    def analyze_face(self, src) -> dict:
        self.require_library()
        return {"ok": True, **self.library.analyze(src)}

    def rename_face(self, face_id: str, name: str) -> dict:
        self.require_library()
        r = self.library.rename(face_id, name)
        self.emit("faces", {"faces": self.faces()})
        return {"ok": True, **r}

    def delete_face(self, face_id: str) -> dict:
        self.require_library()
        was_active = self.settings.get("active_face") == face_id
        self.library.delete(face_id)
        if was_active:
            nxt = self.library.items[0]["id"] if self.library.items else None
            if self.pipeline:
                self.select_face(nxt)
            else:
                self.settings.set("active_face", nxt)
        self.emit("faces", {"faces": self.faces()})
        return {"ok": True}

    def reorder_faces(self, ids: list[str]) -> dict:
        self.require_library()
        self.library.reorder(list(ids))
        self.emit("faces", {"faces": self.faces()})
        return {"ok": True}


def register_vcam_driver() -> dict:
    """Re-register the bundled Unity Capture filter as "Mirage Camera" (asks for admin)."""
    if os.name != "nt":
        return {"ok": False, "error": "unsupported", "message": "The virtual camera driver is Windows-only."}
    drv = paths.app_root() / "drivers"
    dlls = [p for p in (drv / "UnityCaptureFilter64.dll", drv / "UnityCaptureFilter32.dll") if p.exists()]
    if not dlls:
        return {"ok": False, "error": "driver_files_missing", "message": "Reinstall Mirage to restore the camera driver."}
    import ctypes
    for dll in dlls:
        args = f'/s "{dll}" "/i:UnityCaptureName=Mirage Camera"'
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "regsvr32.exe", args, None, 0)
        if rc <= 32:
            return {"ok": False, "error": "elevation_denied", "message": "Admin permission is needed to install the camera."}
    time.sleep(1.5)
    return {"ok": True}

