"""Read-only, loopback-only review history. No raw bundles or logs are served."""
from __future__ import annotations

import fcntl
import json
import math
import os
import re
import signal
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

RUN_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
ASSETS = {"/": ("ccg_review_web.html", "text/html"),
          "/app.js": ("ccg_review_web.js", "text/javascript"),
          "/style.css": ("ccg_review_web.css", "text/css"),
          "/poster.webp": ("ccg_review_center_poster.webp", "image/webp")}


def asset_path(filename: str) -> Path:
    installed = Path(__file__).with_name(filename)
    if installed.is_file():
        return installed
    source_name = "ccg-review-center-poster.webp" if filename == "ccg_review_center_poster.webp" else filename
    source_asset = Path(__file__).with_name("assets") / source_name
    if source_asset.is_file():
        return source_asset
    raise FileNotFoundError(filename)


def read_file(directory: int, name: str, limit: int = 512 * 1024) -> str | None:
    try:
        fd = os.open(name, READ_FLAGS, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            data = stream.read(limit + 1)
            if len(data) > limit:
                return None
            return data.decode("utf-8", errors="replace")
    except OSError:
        return None


def obj(value):
    return value if isinstance(value, dict) else {}


def numeric(value, default=0):
    try:
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else default
    except OverflowError:
        return default


def safe_text(value, limit=2000):
    if not isinstance(value, str):
        return ""
    # Best-effort masking of common credential shapes, not general DLP.
    value = re.sub(r"(?i)Bearer\s+[a-z0-9._~+/=-]{24,}", "Bearer [redacted]", value)
    value = re.sub(r"\b(?:sk-|PMAK-|afxp_)[a-zA-Z0-9_-]{8,}", "[redacted]", value)
    return value[:limit]


class History:
    def __init__(self, root: Path, scan_limit=2048):
        self.root = root
        self.scan_limit = scan_limit

    def _run(self, root_fd, run_id, detail=False):
        if not RUN_ID.fullmatch(run_id):
            return None
        try:
            fd = os.open(run_id, READ_FLAGS | os.O_DIRECTORY, dir_fd=root_fd)
        except OSError:
            return None
        try:
            raw = read_file(fd, "status.json") or read_file(fd, "running.json")
            try:
                meta = obj(json.loads(raw or "null"))
            except (ValueError, RecursionError):
                return None
            if not meta or meta.get("mode") != "dual_leaf_review":
                return None
            state = meta.get("state", "unknown")
            if not isinstance(state, str) or state not in {"running", "succeeded", "failed", "timed_out", "cancelled"}:
                state = "unknown"
            if state == "running":
                try:
                    lock = os.open("run.lock", READ_FLAGS, dir_fd=fd)
                    try:
                        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                        state = "interrupted"
                    except BlockingIOError:
                        pass
                    finally:
                        os.close(lock)
                except OSError:
                    state = "unknown"
            result = {
                "id": run_id, "state": state,
                "workdir": safe_text(meta.get("workdir")),
                "started": numeric(meta.get("started_at_epoch")),
                "finished": numeric(meta.get("finished_at_epoch")),
                "retry_of": meta.get("retry_of") if RUN_ID.fullmatch(str(meta.get("retry_of", ""))) else None,
                "patch_bytes": numeric(meta.get("patch_bytes")),
                "error": safe_text(meta.get("supervisor_error")),
                "backends": {},
            }
            for name in ("codex", "claude"):
                backend = obj(obj(meta.get("backends")).get(name))
                activity = obj(backend.get("activity")) or obj(obj(meta.get("progress")).get(name))
                models = activity.get("actual_models")
                models = [safe_text(m, 120) for m in models[:8]] if isinstance(models, list) else []
                report_info = obj(backend.get("report"))
                partial_info = obj(backend.get("partial_report"))
                verdict = backend.get("verdict")
                row = {
                    "state": safe_text(backend.get("state") or activity.get("phase"), 80) or "unknown",
                    "verdict": verdict if verdict in ("APPROVE", "REQUEST_CHANGES") else None,
                    "models": models,
                    "requested_model": safe_text(obj(meta.get("preflight")).get("codex_review_model"), 120) if name == "codex" else "",
                    "duration": numeric(backend.get("duration_seconds")),
                    "termination": safe_text(backend.get("termination_reason") or activity.get("termination_reason")),
                    "format_error": safe_text(backend.get("format_error")),
                    "last_event": numeric(activity.get("last_event_at_epoch")),
                    "truncated": bool(report_info.get("truncated") or partial_info.get("truncated")),
                }
                if detail:
                    report = read_file(fd, f"{name}.report.md", 2 * 1024 * 1024)
                    partial = read_file(fd, f"{name}.partial.md", 2 * 1024 * 1024) if not report else None
                    row["report"] = safe_text(report or partial or "", 2 * 1024 * 1024)
                    row["partial"] = not report and bool(partial)
                result["backends"][name] = row
            rows = list(result["backends"].values())
            if state != "running" and any(r["verdict"] == "REQUEST_CHANGES" for r in rows):
                result["verdict"] = "changes"
            elif state == "succeeded" and all(r["state"] == "succeeded" and r["verdict"] == "APPROVE" for r in rows):
                result["verdict"] = "approved"
            else:
                result["verdict"] = "pending" if state == "running" else "incomplete"
            return result
        finally:
            os.close(fd)

    def query(self, run_id=None):
        root_fd = os.open(self.root, READ_FLAGS | os.O_DIRECTORY)
        try:
            if run_id is not None:
                return self._run(root_fd, run_id, detail=True)
            runs, capped = [], False
            with os.scandir(root_fd) as entries:
                for index, entry in enumerate(entries):
                    if index >= self.scan_limit:
                        capped = True
                        break
                    if entry.is_dir(follow_symlinks=False):
                        row = self._run(root_fd, entry.name)
                        if row:
                            runs.append(row)
            runs.sort(key=lambda r: (r["started"], r["id"]), reverse=True)
            return {"runs": runs, "capped": capped, "now": time.time()}
        finally:
            os.close(root_fd)


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, root, port=19876):
        self.history = History(root)
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        request.settimeout(5)
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass  # Do not persist request URLs or credentials.

    def send(self, code, data, content_type="application/json"):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(code)
        suffix = "; charset=utf-8" if content_type.startswith("text/") or content_type == "application/json" else ""
        self.send_header("Content-Type", content_type + suffix)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except ConnectionError:
            pass

    def do_GET(self):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            return self.send(403, {"error": "Local origin required"})
        if self.headers.get("Origin") not in (None, self.server.origin) or self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
            return self.send(403, {"error": "Cross-origin access denied"})
        path = urlsplit(self.path).path
        if path in ASSETS:
            filename, mime = ASSETS[path]
            try:
                return self.send(200, asset_path(filename).read_bytes(), mime)
            except OSError:
                return self.send(503, {"error": "Review Center asset unavailable"})
        try:
            if path == "/api/runs":
                return self.send(200, self.server.history.query())
            match = re.fullmatch(r"/api/runs/([0-9a-f-]+)", path)
            if match:
                row = self.server.history.query(match[1])
                return self.send(200, row) if row else self.send(404, {"error": "Review missing or expired"})
            return self.send(404, {"error": "Not found"})
        except OSError:
            return self.send(503, {"error": "Review directory unavailable"})


def serve(root: Path, port: int, write_receipt):
    server = ReviewServer(root, port)
    receipt = root / ".review-ui.json"
    url = server.origin + "/"
    write_receipt(receipt, {"pid": os.getpid(), "url": url})
    print(f"CCG Review Center: {url}", flush=True)
    def stop(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        # A second server on another port may own a newer receipt.
        try:
            if json.loads(receipt.read_text()).get("pid") == os.getpid():
                receipt.unlink()
        except (OSError, ValueError):
            pass
    return 0
