"""Loopback-only local web interface for the paper collector."""

from __future__ import annotations

import json
import logging
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .webservice import CollectionJob

HOST = "127.0.0.1"
PORT = 8765
STATIC = Path(__file__).with_name("static")
DEFAULT_OUTPUT = Path.home() / "Documents" / "PaperCollector"


def default_workspace() -> Path:
    """Return the package-local workspace, isolated from any older project collection."""
    project = Path(__file__).resolve().parents[2]
    return project if project.name.casefold() == "paperflow" else project / "Paperflow"


def resolve_workspace(value: str | Path) -> Path:
    text = str(value).strip()
    if text.casefold() in {"paperflow", ".", "./"}:
        return default_workspace()
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    return path.resolve()


def display_output(workspace_value: str | Path) -> str:
    text = str(workspace_value).strip()
    if text.casefold() in {"paperflow", ".", "./"}:
        return "Paperflow\\Research_Papers"
    return str(Path(text) / "Research_Papers")


class AppState:
    def __init__(self):
        self.lock = threading.RLock()
        self.job: CollectionJob | None = None
        self.thread: threading.Thread | None = None

    def snapshot(self):
        with self.lock:
            if self.job is None:
                return {"has_job": False, "running": False, "phase": "idle", "message": "Ready when you are.", "counts": {}}
            data = self.job.snapshot()
            data["has_job"] = True
            return data


STATE = AppState()


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperCollectorLocal/1.0"

    def log_message(self, fmt, *args):
        # Never log request bodies, which include the user's personal API key.
        logging.info("%s - %s", self.address_string(), fmt % args)

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid request size.")
        if size > 100_000:
            raise ValueError("Request is too large.")
        return json.loads(self.rfile.read(size) or b"{}")

    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/api/status":
            return self._json(200, STATE.snapshot())
        if route == "/api/defaults":
            workspace = default_workspace()
            output = workspace / "Research_Papers"
            return self._json(200, {
                "workspace": "Paperflow",
                "output": "Paperflow\\Research_Papers",
                "output_path": str(output),
                "output_exists": output.is_dir(),
            })
        filename = "index.html" if route in {"/", "/index.html"} else route.removeprefix("/")
        if filename not in {"index.html", "app.js", "styles.css"}:
            return self._json(404, {"error": "Not found."})
        file = STATIC / filename
        content_type = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}[file.suffix]
        content = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        route = urlparse(self.path).path
        if route == "/api/start":
            return self._start()
        if route == "/api/stop":
            with STATE.lock:
                if STATE.job and STATE.job.running:
                    STATE.job.stop()
                    return self._json(200, {"ok": True, "message": "Stopping safely after the current paper."})
            return self._json(409, {"error": "There is no active collection run."})
        if route == "/api/browse-folder":
            return self._browse_folder()
        if route == "/api/open-folder":
            try:
                payload = self._body()
                folder_text = str(payload.get("path", ""))
                folder = resolve_workspace(folder_text) / "Research_Papers" if "Research_Papers" not in folder_text else Path(folder_text).expanduser().resolve()
                if not folder.is_dir():
                    return self._json(400, {"error": "That folder does not exist."})
                os.startfile(folder)  # Windows desktop local app
                return self._json(200, {"ok": True})
            except Exception as exc:
                return self._json(400, {"error": str(exc)})
        if route == "/api/workspace-summary":
            try:
                payload = self._body()
                workspace_value = str(payload.get("workspace", ""))
                workspace = resolve_workspace(workspace_value)
                output = workspace / "Research_Papers"
                return self._json(200, {
                    "saved_count": 0,
                    "output": display_output(workspace_value),
                    "output_path": str(output),
                    "output_exists": output.is_dir(),
                })
            except Exception as exc:
                return self._json(400, {"error": str(exc)})
        return self._json(404, {"error": "Not found."})

    def _browse_folder(self):
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askdirectory(title="Choose your paper collection folder")
            root.destroy()
            return self._json(200, {"path": selected})
        except Exception as exc:
            return self._json(500, {"error": f"Could not open the folder picker: {exc}"})

    def _start(self):
        try:
            options = self._body()
            job = CollectionJob(options)
        except (ValueError, TypeError) as exc:
            return self._json(400, {"error": str(exc)})
        with STATE.lock:
            if STATE.job and STATE.job.running:
                return self._json(409, {"error": "A collection is already running."})
            STATE.job = job
            STATE.thread = threading.Thread(target=job.run, name="paper-collection", daemon=True)
            STATE.thread.start()
        return self._json(202, {"ok": True, "message": "Collection started."})


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    url = f"http://{HOST}:{PORT}"
    print(f"Paper Collector is running locally at {url}\nClose this window to stop the local web app.")
    threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        if STATE.job and STATE.job.running:
            STATE.job.stop()
        server.server_close()


if __name__ == "__main__":
    main()
