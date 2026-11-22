"""Run the real engine behind the UI in an ordinary browser (no pywebview).

    python scripts/dev_server.py --video clip.mp4 --no-vcam [--port 8765]
    then open http://127.0.0.1:8765/

The page gets a small shim that looks like window.pywebview.api: each method
call is a POST to /api/<name>, and engine events stream over Server-Sent
Events into window.mirage.on(). Useful for UI work on any OS and for
end-to-end tests. Binds to 127.0.0.1 only.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import queue
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import paths  # noqa: E402

SHIM = """<script>
(function () {
  const call = (name) => (...args) => fetch('/api/' + name, {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(args)}).then(r => r.json());
  window.pywebview = { api: new Proxy({}, { get: (_, name) => call(name) }) };
  const es = new EventSource('/events');
  es.onmessage = (m) => { const d = JSON.parse(m.data); window.mirage && window.mirage.on(d.event, d.data); };
  window.dispatchEvent(new Event('pywebviewready'));
})();
</script>"""


class Hub:
    """Fan-out of engine events to every connected browser; coalesces frames."""

    def __init__(self):
        self.clients: list[queue.Queue] = []
        self.lock = threading.Lock()

    def __call__(self, event: str, data: dict) -> None:
        with self.lock:
            for q in self.clients:
                if event in ("preview", "stats") and q.qsize() > 4:
                    continue
                q.put((event, data))


def make_handler(api, hub: Hub):
    ui = paths.ui_dir()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                q: queue.Queue = queue.Queue()
                with hub.lock:
                    hub.clients.append(q)
                try:
                    while True:
                        event, data = q.get()
                        self.wfile.write(f"data: {json.dumps({'event': event, 'data': data})}\n\n".encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with hub.lock:
                        hub.clients.remove(q)
                return
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            f = (ui / rel).resolve()
            if not str(f).startswith(str(ui.resolve())) or not f.is_file():
                self._send(404, b"not found", "text/plain")
                return
            body = f.read_bytes()
            if rel == "index.html":
                body = body.replace(b"<script src=\"mock.js\"></script>", SHIM.encode() + b"\n<script src=\"mock.js\"></script>")
            self._send(200, body, mimetypes.guess_type(str(f))[0] or "application/octet-stream")

        def do_POST(self):
            if not self.path.startswith("/api/"):
                self._send(404, b"{}", "application/json")
                return
            name = self.path[5:]
            n = int(self.headers.get("Content-Length") or 0)
            args = json.loads(self.rfile.read(n) or b"[]")
            fn = getattr(api, name, None)
            if name.startswith("_") or not callable(fn):
                out = {"ok": False, "error": "no_method"}
            else:
                out = fn(*args)
            self._send(200, json.dumps(out).encode(), "application/json")

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video")
    ap.add_argument("--no-vcam", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--tier", choices=["Ultra", "Balanced", "Performance"])
    a = ap.parse_args()

    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from bridge.api import Api
    from core import MirageCore

    hub = Hub()
    core = MirageCore(emit=hub, video=a.video, no_vcam=a.no_vcam, enable_hotkeys=False)
    if a.tier:
        core.settings.set("tier", a.tier)
    api = Api(core)
    core.boot_async()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(api, hub))
    srv.daemon_threads = True
    print(f"Mirage dev server on http://127.0.0.1:{a.port}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        core.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
