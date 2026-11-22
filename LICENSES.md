# Third-party licences

Mirage bundles or downloads the following. Check each before any commercial release.

## Models (downloaded by `build/fetch_models.py`)

| File | Source | Licence |
| --- | --- | --- |
| `inswapper_128.onnx`, `inswapper_128_fp16.onnx` | InsightFace (via facefusion-assets) | **Non-commercial use only.** Fine for the competition build; needs a commercial licence or a different swapper before selling Mirage. |
| `det_500m.onnx` (SCRFD, buffalo_s) | InsightFace | Non-commercial research use for the pretrained weights. |
| `arcface_w600k_r50.onnx` | InsightFace | Non-commercial research use for the pretrained weights. |
| `gfpgan_1.4.onnx` | TencentARC GFPGAN (ONNX export via facefusion-assets) | Apache-2.0 (code); check the model card for weights. |
| `xseg_1.onnx` | DeepFaceLab XSeg (via facefusion-assets) | GPL-3.0 (DeepFaceLab); check before redistribution in a closed-source product. |

## Virtual camera driver

* **Unity Capture filter** (`UnityCaptureFilter64.dll`, `UnityCaptureFilter32.dll`) by Bernhard Schelling — MIT licence. Registered by the installer as "Mirage Camera". https://github.com/schellingb/UnityCapture

## Fonts (in `ui/fonts/`)

* **Inter** — SIL Open Font License 1.1 (`ui/fonts/LICENSE-Inter.txt`).
* **JetBrains Mono** — SIL Open Font License 1.1 (`ui/fonts/LICENSE-JetBrainsMono.txt`).

## Python libraries

* NumPy (BSD-3-Clause), OpenCV (Apache-2.0), ONNX Runtime / onnxruntime-directml (MIT),
  pywebview (BSD-3-Clause), pyvirtualcam (GPL-2.0), keyboard (MIT), psutil (BSD-3-Clause),
  PyInstaller bootloader (GPL-2.0 with bootloader exception).

Note: pyvirtualcam is GPL-2.0. Distributing Mirage as a closed-source product with it bundled
has licence implications; for the competition build this is not an issue, but replace it (the
Unity Capture shared-memory protocol is small) before a commercial release.

## Responsible use

Mirage only swaps faces the user imports after confirming "This is me, or this person agreed to it".
It ships no face packs and has no scraping features.
