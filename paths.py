"""Filesystem locations used by Mirage.

App files (models, ui) live next to this module, or inside the PyInstaller
bundle when frozen. User data lives in %APPDATA%/Mirage on Windows and
~/.config/Mirage elsewhere. MIRAGE_HOME overrides the user data folder
(handy for tests and portable installs).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def models_dir() -> Path:
    env = os.environ.get("MIRAGE_MODELS")
    return Path(env) if env else app_root() / "models"


def ui_dir() -> Path:
    return app_root() / "ui"


def user_dir() -> Path:
    env = os.environ.get("MIRAGE_HOME")
    if env:
        base = Path(env)
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Mirage"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "Mirage"
    base.mkdir(parents=True, exist_ok=True)
    return base


def faces_dir() -> Path:
    d = user_dir() / "faces"
    d.mkdir(parents=True, exist_ok=True)
    return d
