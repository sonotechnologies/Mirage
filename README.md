# Mirage

Mirage is a Windows desktop app that swaps your face live from your webcam. It outputs the result as a virtual camera called **Mirage Camera**, which WhatsApp Desktop (and any video app) can select. It runs on most Windows 10/11 PCs: any DirectX 12 GPU speeds it up through DirectML, and it falls back to the CPU otherwise. The installer is one `setup.exe` with no Python, CUDA or OBS needed.

Faces are consent-only. The import dialog asks you to confirm "This is me, or this person agreed to it". There are no bundled face packs and no scraping features.

## Quick start (development)

```powershell
py -3.11 -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt -r requirements-build.txt
python build\fetch_models.py        # ~1.4 GB of models + the virtual-camera driver, SHA-256 checked
python app.py                       # window + webcam + virtual camera
```

To make "Mirage Camera" appear without running the installer, register the driver once from an admin prompt:

```powershell
regsvr32 drivers\UnityCaptureFilter64.dll "/i:UnityCaptureName=Mirage Camera"
regsvr32 drivers\UnityCaptureFilter32.dll "/i:UnityCaptureName=Mirage Camera"
```

### Command-line flags

| Flag | What it does |
| --- | --- |
| `--video clip.mp4` | Use a looping video file instead of the webcam |
| `--no-vcam` | Don't open the virtual camera |
| `--headless` | No window: run the pipeline and log FPS and per-stage timings |
| `--face photo.jpg --consent` | Headless only: import this face and swap it in |
| `--seconds N`, `--live`, `--record out.mp4` | Headless run length, send frames to the virtual camera, save the output |
| `--tier Ultra\|Balanced\|Performance`, `--provider auto\|directml\|cpu` | Override the benchmark's choices |
| `--debug` | Open WebView devtools and turn on verbose logs |

Example headless check:

```
python app.py --headless --video clip.mp4 --no-vcam --face me.jpg --consent --seconds 20
```

### Working on the UI

* **Mock mode:** open `ui/index.html` straight in a browser. Faces, FPS and a placeholder preview are faked. Add `?scenario=live|empty|noface|lowperf|error-camera|error-driver|first-run|collapsed` to the URL to see each state.
* **Real engine in a browser:** run `python scripts/dev_server.py --video clip.mp4 --no-vcam`, then open http://127.0.0.1:8765/. This serves the same UI over HTTP with a `window.pywebview.api` shim, and works on any OS.

### Tests

```
python -m pytest -q tests
```

Unit tests need nothing extra. The model tests need the downloaded models plus four photos you supply, because the repo ships no photos of real people. Point `MIRAGE_TEST_ASSETS` at a folder containing:

* `face_a.jpg` and `face_b.jpg`: two different people, one face each
* `target.jpg`: one face
* `group.jpg`: several faces

The model tests cover:

* identity transfer,
* face switching on the next frame,
* hand occlusion,
* no-face passthrough,
* the library rules: consent, no face, multiple faces, a 12-face maximum, saved across restarts,
* the headless `--video` / `--no-vcam` pipeline.

## Building the installer

On Windows with Python 3.11 and [Inno Setup 6](https://jrsoftware.org/isinfo.php):

```powershell
powershell -ExecutionPolicy Bypass -File build\build.ps1    # -> dist\MirageSetup.exe
```

The script does the following:

1. Creates a venv and installs the requirements.
2. Fetches and verifies the models.
3. Runs the tests.
4. Downloads the WebView2 bootstrapper.
5. Runs PyInstaller (a one-folder build).
6. Runs Inno Setup.

The installer does the following:

* installs to Program Files,
* registers the camera as "Mirage Camera" (64- and 32-bit) and unregisters it on uninstall,
* installs WebView2 if it's missing,
* adds Start menu and desktop shortcuts.

User data in `%APPDATA%\Mirage` is kept when you uninstall.

CI (`.github/workflows/ci.yml`) runs the unit tests on every push. To build the installer on `windows-latest`, run the workflow manually with **installer** ticked.

## How it works

```
app.py               entry: pywebview window (or --headless), boots core in the background
core.py              owns settings, models, face library, pipeline, hotkeys; emits UI events
engine/
  pipeline.py        capture -> process -> output threads, size-1 "latest frame" slots (lag never builds)
  capture.py         webcam enumeration + DirectShow capture (MJPG), --video file source
  detector.py        SCRFD (buffalo_s det_500m) at 320 px, 5 landmarks
  tracker.py         Lucas-Kanade landmark tracking between detections + One Euro smoothing
  swapper.py         inswapper_128 (+ ArcFace r50 embedding, computed once at import)
  enhancer.py        GFPGAN 1.4 (optional, 512 px)
  occlusion.py       XSeg mask: hands and objects stay in front of the face
  color.py           LAB colour transfer + face-only unsharp mask
  processor.py       per-frame: detect/track -> align -> swap -> enhance -> colour -> mask -> paste
  vcam.py            pyvirtualcam output ("Mirage Camera" via Unity Capture)
  providers.py       DirectML/CPU selection, ONNX sessions, tiers, 3-second benchmark
faces/library.py     import (JPG/PNG/WEBP, 0 or >1 faces rejected), embeddings, thumbnails, max 12
bridge/api.py        methods exposed as window.pywebview.api.* (all return {ok, ...})
hotkeys.py           global hotkeys (work while WhatsApp has focus)
config.py            %APPDATA%/Mirage/settings.json
ui/                  plain HTML/CSS/JS from the design brief; fonts bundled; mock mode
build/               fetch_models.py, mirage.spec (PyInstaller), installer.iss, build.ps1
```

### Performance tiers

The tier is chosen by a 3-second benchmark on first launch. You can re-run it from Settings → Performance or override the choice.

| Tier | Capture | Detect every | Enhancer | Occlusion | Target |
| --- | --- | --- | --- | --- | --- |
| Ultra (strong GPU) | 1080p | 1 frame | On | Every frame | 30 FPS |
| Balanced (iGPU / mid GPU) | 720p | 2 frames | Off by default | Every 2 frames | 25–30 FPS |
| Performance (CPU) | 480p | 5 frames | Disabled | Every 3 frames, low-res | 15–20 FPS |

**On CPU, the swap runs in the background.** inswapper takes hundreds of milliseconds per face on a laptop CPU, so it can't run on every frame. The swap runs on a worker thread, and each frame reuses the newest swapped face. Because that face is stored in aligned space, it follows the live head every frame. The following parts run every frame:

* tracking,
* colour/lighting match,
* the occlusion mask,
* a change mask that lets a hand that just moved in show through.

Expressions lag by one swap. A face switch is still swapped immediately on the next frame.

### Bridge events

Python pushes events through `window.mirage.on(event, data)`:

| Event | Data / when |
| --- | --- |
| `preview` | `{frame, original?}` base64 JPEG, ≤15 FPS |
| `stats` | FPS, ms, per-stage timings, provider, twice a second |
| `face_lost` / `face_found` | the face leaves or returns to view |
| `error` | `{code, message}`: `camera_in_use`, `camera_lost`, `vcam_missing`, `models_missing`, … |
| `benchmark_progress` | `{progress, stage, result?}` |
| `ready` | full state |
| `faces` | the library changed |
| `face_selected` | a face was selected |
| `param` | a setting changed, e.g. a hotkey toggled bypass |
| `live` | live state changed |
| `tier` | the performance tier changed |

## Licensing

The inswapper model is for **non-commercial use only**. That's fine for the competition, but needs to be resolved before any commercial release. See [LICENSES.md](LICENSES.md) for the models, the driver, the fonts and the libraries.
