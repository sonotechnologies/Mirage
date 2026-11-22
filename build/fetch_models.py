"""Download the ONNX models and the virtual-camera driver, and verify SHA-256.

    python build/fetch_models.py            # models/ + drivers/
    python build/fetch_models.py --check    # verify only, download nothing

Also extracts inswapper's embedding matrix to models/inswapper_emap.npy so the
app does not need the `onnx` package at runtime (needs `onnx` here, once).
Re-running is cheap: files whose checksum already matches are skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.models import EMAP_FILE, EMAP_SHA256, MANIFEST  # noqa: E402

_UC = "https://raw.githubusercontent.com/schellingb/UnityCapture/master/Install"
# Unity Capture DirectShow filter (MIT), registered by the installer as "Mirage Camera".
DRIVERS = {
    "UnityCaptureFilter64.dll": {"url": f"{_UC}/UnityCaptureFilter64.dll",
                                 "sha256": "72812f5363d8ecb45632253f8c8c888844b1b62e27616f3c8cc21064ccde25e5"},
    "UnityCaptureFilter32.dll": {"url": f"{_UC}/UnityCaptureFilter32.dll",
                                 "sha256": "aa3ebdf03dea7f3aab3dd7b724751f49ed71672256b57c6a19aa6809cabf30ba"},
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "mirage-build"})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            buf.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r    {done / 1e6:7.1f} / {total / 1e6:.1f} MB", end="", flush=True)
        print()
        return buf.getvalue()


def fetch(name: str, spec: dict, dest_dir: Path, check_only: bool) -> bool:
    dest = dest_dir / name
    if dest.exists() and sha256(dest) == spec["sha256"]:
        print(f"  ok        {name}")
        return True
    if check_only:
        print(f"  MISSING   {name}" if not dest.exists() else f"  BAD HASH  {name}")
        return False
    print(f"  download  {name}  <- {spec['url']}")
    data = download(spec["url"])
    if "zip_member" in spec:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            member = next(m for m in z.namelist() if m.endswith(spec["zip_member"]))
            data = z.read(member)
    got = hashlib.sha256(data).hexdigest()
    if got != spec["sha256"]:
        print(f"  FAILED    {name}: sha256 {got} != {spec['sha256']}")
        return False
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return True


def extract_emap(models: Path, check_only: bool) -> bool:
    dest = models / EMAP_FILE
    if dest.exists() and sha256(dest) == EMAP_SHA256:
        print(f"  ok        {EMAP_FILE}")
        return True
    if check_only:
        print(f"  MISSING   {EMAP_FILE}")
        return False
    try:
        import numpy as np
        import onnx
        from onnx import numpy_helper
    except ImportError:
        print("  FAILED    need `pip install onnx` to extract the inswapper embedding matrix")
        return False
    model = onnx.load(str(models / "inswapper_128.onnx"))
    np.save(dest, numpy_helper.to_array(model.graph.initializer[-1]).astype(np.float32))
    ok = sha256(dest) == EMAP_SHA256
    print(f"  {'ok' if ok else 'BAD HASH':9} {EMAP_FILE} (extracted)")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="verify only")
    ap.add_argument("--models-dir", default=str(ROOT / "models"))
    ap.add_argument("--drivers-dir", default=str(ROOT / "drivers"))
    ap.add_argument("--no-fp16", action="store_true", help="skip the fp16 swapper (GPU speed-up)")
    ap.add_argument("--no-enhancer", action="store_true", help="skip GFPGAN (~340 MB)")
    a = ap.parse_args()
    models, drivers = Path(a.models_dir), Path(a.drivers_dir)
    models.mkdir(parents=True, exist_ok=True)
    drivers.mkdir(parents=True, exist_ok=True)

    ok = True
    print("models:")
    for name, spec in MANIFEST.items():
        if a.no_fp16 and "fp16" in name or a.no_enhancer and name.startswith("gfpgan"):
            continue
        ok &= fetch(name, spec, models, a.check)
    ok &= extract_emap(models, a.check)
    print("drivers:")
    for name, spec in DRIVERS.items():
        ok &= fetch(name, spec, drivers, a.check)
    print("all files verified" if ok else "SOME FILES ARE MISSING OR INVALID")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
