"""Settings persisted to %APPDATA%/Mirage/settings.json."""
from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any

import paths

VERSION = "1.0.0"

DEFAULT_HOTKEYS = {
    **{f"face_{n}": f"ctrl+alt+{n}" for n in range(1, 10)},
    "bypass": "ctrl+alt+0",
    "next_face": "ctrl+alt+right",
    "prev_face": "ctrl+alt+left",
    "toggle_live": "ctrl+alt+s",
}

DEFAULTS: dict[str, Any] = {
    "camera": 0,
    "resolution": None,          # "1280x720"; None = tier default
    "mirror": True,              # mirror the preview only (never the virtual camera)
    "tier": None,                # None until the first benchmark has run
    "provider": "auto",          # auto | directml | cpu
    "active_face": None,
    "params": {
        "swap": True,
        "blend": 60,
        "sharpness": 30,
        "colour": 70,
        "enhancer": False,
        "occlusion": True,
    },
    "hotkeys": DEFAULT_HOTKEYS,
    "helper_seen": False,
    "benchmark": None,           # last benchmark result
}

PARAM_KEYS = set(DEFAULTS["params"])


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    """Thread-safe settings store; writes are atomic (temp file + replace)."""

    def __init__(self, path: Path | None = None):
        self.path = path or paths.user_dir() / "settings.json"
        self._lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            if isinstance(raw, dict):
                self.data = _merge(DEFAULTS, raw)
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), "utf-8")
            tmp.replace(self.path)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return copy.deepcopy(self.data.get(key, default))

    def set(self, key: str, value: Any, save: bool = True) -> None:
        with self._lock:
            self.data[key] = value
            if save:
                self.save()

    def set_param(self, key: str, value: Any) -> None:
        with self._lock:
            self.data["params"][key] = value
            self.save()

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.data)
