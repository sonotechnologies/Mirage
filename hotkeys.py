"""Global hotkeys (work while WhatsApp has focus). Uses the `keyboard` package on Windows."""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger("mirage.hotkeys")

ACTIONS = [*(f"face_{n}" for n in range(1, 10)), "bypass", "next_face", "prev_face", "toggle_live"]


def normalise(combo: str) -> str:
    parts = [p.strip().lower() for p in str(combo).replace(" ", "").split("+") if p.strip()]
    alias = {"control": "ctrl", "option": "alt", "→": "right", "←": "left", "arrowright": "right",
             "arrowleft": "left", "win": "windows", "meta": "windows", "cmd": "windows"}
    parts = [alias.get(p, p) for p in parts]
    mods = [m for m in ("ctrl", "alt", "shift", "windows") if m in parts]
    keys = [p for p in parts if p not in mods]
    if len(keys) != 1:
        raise ValueError("A hotkey needs exactly one non-modifier key, e.g. Ctrl+Alt+1")
    if not mods:
        raise ValueError("Use at least one modifier (Ctrl, Alt, Shift or Win) so typing isn't hijacked")
    return "+".join(mods + keys)


class Hotkeys:
    def __init__(self, handler: Callable[[str], None]):
        self.handler = handler
        self.bindings: dict[str, str] = {}
        self._handles: list = []
        self._lock = threading.Lock()
        self.available = False
        self.error = ""
        try:
            import keyboard  # noqa: F401
            self._kb = keyboard
            self.available = True
        except Exception as e:  # noqa: BLE001 — ImportError, or OSError (needs root on Linux)
            self._kb = None
            self.error = str(e)

    def apply(self, bindings: dict[str, str]) -> dict[str, str]:
        """(Re)register every binding. Returns {action: error} for any that failed."""
        errors: dict[str, str] = {}
        with self._lock:
            self._clear()
            self.bindings = dict(bindings)
            if not self._kb:
                return {a: self.error or "hotkeys unavailable" for a in bindings}
            for action, combo in bindings.items():
                if action not in ACTIONS or not combo:
                    continue
                try:
                    h = self._kb.add_hotkey(normalise(combo), self._fire, args=(action,), suppress=False)
                    self._handles.append(h)
                except Exception as e:  # noqa: BLE001
                    errors[action] = str(e)
                    log.warning("hotkey %s=%s failed: %s", action, combo, e)
        return errors

    def _fire(self, action: str) -> None:
        try:
            self.handler(action)
        except Exception:  # noqa: BLE001
            log.exception("hotkey handler failed")

    def _clear(self) -> None:
        if self._kb:
            for h in self._handles:
                try:
                    self._kb.remove_hotkey(h)
                except Exception:  # noqa: BLE001
                    pass
        self._handles.clear()

    def close(self) -> None:
        with self._lock:
            self._clear()
