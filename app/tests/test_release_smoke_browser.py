"""Rendered results remain testable while a background connection stays open."""

import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from time import monotonic

import httpx


def test_rendered_result_does_not_wait_for_background_network_idle(tmp_path):
    path = Path(__file__).parents[1] / "scripts/release_smoke.py"
    spec = importlib.util.spec_from_file_location("release_smoke_browser_regression", path)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    stop, requested = Event(), Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/background":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.flush()
                requested.set()
                stop.wait(60)
                return
            body = b"""<!doctype html><html><body style="margin:0">
              <article class="overview-card">Rendered result</article>
              <details><summary>Weitere Aktionen</summary>
              <a href="/export">HTML herunterladen</a></details>
              <script>fetch('/background');</script></body></html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client() as client:
            started = monotonic()
            result = smoke.browser_proof(
                client, f"http://127.0.0.1:{server.server_port}", "controlled-result", tmp_path
            )
            assert monotonic() - started < 20
        assert requested.is_set() and not stop.is_set()
        assert result == {"widths": [1280, 375], "console_errors": 0, "failed_requests": 0}
        assert (tmp_path / "desktop-1280.png").exists()
        assert (tmp_path / "desktop-375.png").exists()
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
