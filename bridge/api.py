"""Methods exposed to the UI as window.pywebview.api.*

Every method returns a JSON-serialisable dict; failures come back as
{ok: false, error: <code>, message: <text>} instead of raising into JS.
Attributes are underscore-prefixed so pywebview does not expose them.
"""
from __future__ import annotations

import functools
import logging

from faces.library import ImportError_

log = logging.getLogger("mirage.bridge")


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            out = fn(self, *args, **kwargs)
            if out is None:
                return {"ok": True}
            if isinstance(out, dict) and "ok" not in out:
                out = {"ok": True, **out}
            return out
        except ImportError_ as e:
            return {"ok": False, "error": e.code, "message": str(e)}
        except ValueError as e:
            return {"ok": False, "error": "bad_value", "message": str(e)}
        except RuntimeError as e:
            return {"ok": False, "error": "not_ready", "message": str(e)}
        except Exception as e:  # noqa: BLE001
            log.exception("bridge %s failed", fn.__name__)
            return {"ok": False, "error": "internal", "message": str(e)}
    return wrapper


class Api:
    def __init__(self, core, window_ref=None):
        self._core = core
        self._window_ref = window_ref or (lambda: None)

    # --- state ---------------------------------------------------------
    @_safe
    def get_state(self):
        return self._core.get_state()

    @_safe
    def list_cameras(self, refresh=False):
        return {"cameras": self._core.list_cameras(bool(refresh))}

    @_safe
    def set_camera(self, id, resolution=None):  # noqa: A002 — name from the brief
        return self._core.set_camera(id, resolution)

    # --- live ----------------------------------------------------------
    @_safe
    def start(self):
        return self._core.start()

    @_safe
    def stop(self):
        return self._core.stop()

    # --- faces ---------------------------------------------------------
    @_safe
    def import_face(self, data, name="", consent=False):
        """data: file path, data: URL or base64."""
        return self._core.import_face(data, name, consent)

    @_safe
    def analyze_face(self, data):
        return self._core.analyze_face(data)

    @_safe
    def rename_face(self, id, name):  # noqa: A002
        return self._core.rename_face(id, name)

    @_safe
    def delete_face(self, id):  # noqa: A002
        return self._core.delete_face(id)

    @_safe
    def reorder_faces(self, ids):
        return self._core.reorder_faces(ids)

    @_safe
    def select_face(self, id=None):  # noqa: A002
        return self._core.select_face(id)

    # --- params / performance -------------------------------------------
    @_safe
    def set_param(self, key, value):
        return self._core.set_param(key, value)

    @_safe
    def set_tier(self, tier):
        return self._core.set_tier(tier)

    @_safe
    def run_benchmark(self):
        return self._core.run_benchmark()

    @_safe
    def set_provider(self, provider):
        return self._core.set_provider(provider)

    @_safe
    def set_hotkey(self, action, combo):
        return self._core.set_hotkey(action, combo)

    # --- misc ----------------------------------------------------------
    @_safe
    def fix_error(self, code):
        return self._core.fix_error(code)

    @_safe
    def dismiss_helper(self):
        return self._core.dismiss_helper()

    @_safe
    def open_licences(self):
        import paths
        p = paths.app_root() / "LICENSES.md"
        return {"text": p.read_text("utf-8") if p.exists() else "See LICENSES.md in the install folder."}

    # --- window controls (frameless window) -------------------------------
    @_safe
    def window_minimize(self):
        w = self._window_ref()
        if w:
            w.minimize()

    @_safe
    def window_toggle_maximize(self):
        w = self._window_ref()
        if w:
            if getattr(self, "_maximized", False):
                w.restore()
            else:
                w.maximize()
            self._maximized = not getattr(self, "_maximized", False)

    @_safe
    def window_close(self):
        w = self._window_ref()
        if w:
            w.destroy()
