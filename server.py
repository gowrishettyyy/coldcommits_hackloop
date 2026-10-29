"""
The HTTP server. Standard library only on purpose: one less thing to install at
midnight.

Routes
  GET  /                      the app
  GET  /api/snapshot          one JSON snapshot
  GET  /api/events            live stream (SSE), pushes a snapshot every second
  POST /api/chat              {"message": "...", "group": 4, "matters": [...]}
  POST /api/squad             {"venue_id": ..., "seats": [...]}
  POST /api/config            {"speed": 30, "scenario": "ghost", "day": "Tue"}
  GET  /api/health
"""
import json
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cv.insights import answer

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

QUIET = bool(os.environ.get("PULSE_QUIET"))


class Handler(BaseHTTPRequestHandler):
    engine = None
    server_version = "CampusPulse/1.0"

    def log_message(self, fmt, *args):
        if not QUIET:
            super().log_message(fmt, *args)

    # ------------------------------------------------------------------ #
    def _json(self, payload, status=200):
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode() or "{}")
        except Exception:
            return {}

    def _static(self, path):
        rel = path.lstrip("/") or "index.html"
        target = os.path.normpath(os.path.join(WEB, rel))
        if not target.startswith(WEB) or not os.path.isfile(target):
            target = os.path.join(WEB, "index.html")
            if not os.path.isfile(target):
                return self._json({"error": "web/ not found"}, 404)
        ext = os.path.splitext(target)[1].lower()
        with open(target, "rb") as fh:
            body = fh.read()
        # The bundled font is content-addressed by name and never changes, so let
        # the browser cache it. Re-fetching a webfont on every page load is what
        # makes a font-display swap flash the type in and out.
        cached = rel.replace(os.sep, "/").startswith("fonts/")
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",
                         "public, max-age=31536000, immutable" if cached else "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------ #
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)

        if path == "/api/health":
            return self._json({"ok": True, "clock": self.engine.now()})

        if path == "/api/snapshot":
            group = query.get("group", [None])[0]
            return self._json(self.engine.snapshot(int(group) if group else None))

        if path == "/api/events":
            return self._stream(query.get("group", [None])[0])

        return self._static(path)

    def do_POST(self):
        """Every POST is wrapped, so an unexpected engine error comes back as JSON with a
        status instead of killing the connection. An empty reply is what made a failing
        button press look like nothing had happened."""
        try:
            self._post(urllib.parse.urlparse(self.path).path, self._body())
        except Exception as exc:
            try:
                self._json({"error": f"server error: {type(exc).__name__}: {exc}"}, 500)
            except Exception:
                pass

    def _post(self, path, data):
        if path == "/api/chat":
            snap = self.engine.snapshot()
            group = data.get("group")
            try:
                group = int(group) if group else None
            except (TypeError, ValueError):
                group = None
            return self._json(answer(
                data.get("message", ""), snap,
                group=group,
                matters=data.get("matters") or [],
                focus=data.get("venue_id"),
            ))

        if path == "/api/squad":
            if not data.get("venue_id"):
                return self._json({"error": "venue_id is required"}, 400)
            return self._json(self.engine.squad_code(data.get("venue_id"), data.get("seats") or []))

        if path == "/api/config":
            kwargs = {}
            if "speed" in data:
                kwargs["speed"] = float(data["speed"])
            if "scenario" in data:
                kwargs["scenario"] = data["scenario"] or None
            if "day" in data:
                kwargs["day"] = data["day"]
            if "time" in data:
                kwargs["time_of_day"] = data["time"]
            self.engine.configure(**kwargs)
            return self._json({"ok": True, **kwargs})

        return self._json({"error": "unknown endpoint"}, 404)

    # ------------------------------------------------------------------ #
    def _stream(self, group):
        group = int(group) if group else None
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            while True:
                payload = json.dumps(self.engine.snapshot(group), separators=(",", ":"))
                self.wfile.write(f"event: snapshot\ndata: {payload}\n\n".encode())
                self.wfile.flush()
                time.sleep(1.0)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True
            return


def serve(engine, host="0.0.0.0", port=8000):
    Handler.engine = engine
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd
