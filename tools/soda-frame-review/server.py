"""Local, source-read-only frame reviewer. Run with the project's Pillow-capable Python."""

import argparse
import bisect
import csv
import fcntl
import hashlib
import io
import importlib.util
import json
import logging
import os
import secrets
import threading
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image


PROJECT = Path(__file__).resolve().parents[2]
ASSETS = Path(__file__).resolve().parent
ARCHIVE = Path(os.environ.get("SODA_FRAME_ARCHIVE", str(PROJECT / "data/source-frames")))
DATASET = PROJECT / "data/soda-keyframe-calibration/dataset.json"
PREVIEW = PROJECT / ".ai-work/temporary/soda-continuous-review/complete-recording-preview.mp4"
LABELS = {"new_state", "animation", "same_state", "uncertain"}
LOG = logging.getLogger("soda-review")
LOG_PATH = PROJECT / ".ai-work/temporary/soda-frame-review-logs/dashboard.log"
WORKSPACE_SPEC = importlib.util.spec_from_file_location("soda_workspace", ASSETS / "workspace.py")
workspace_module = importlib.util.module_from_spec(WORKSPACE_SPEC)
WORKSPACE_SPEC.loader.exec_module(workspace_module)
LEARNING_SPEC = importlib.util.spec_from_file_location("soda_learning", ASSETS / "learning.py")
learning_module = importlib.util.module_from_spec(LEARNING_SPEC)
LEARNING_SPEC.loader.exec_module(learning_module)


def configure_logging(path=LOG_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    return handler


class ConflictError(Exception):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Store:
    def __init__(self, archive=ARCHIVE, dataset=DATASET, preview=PREVIEW, reviewer="Victoria"):
        self.archive, self.dataset, self.preview = archive, dataset, preview
        self.reviewer = reviewer
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        manifest = json.loads((archive / "manifest.json").read_text())
        self.videos = []
        for index, video in enumerate(manifest["videos"]):
            name = video["output_directory"]
            source = (archive / name).resolve()
            if source.parent != archive.resolve():
                raise ValueError("Invalid archive directory")
            with (source / video["timestamps_file"]).open() as handle:
                rows = list(csv.DictReader(handle))
            times = [float(r["timestamp_seconds"]) for r in rows]
            if not times or len(times) != video["extracted_frame_count"]:
                raise ValueError(f"Source timestamp count mismatch: {name}")
            if [int(r["frame_number"]) for r in rows] != list(range(1, len(rows) + 1)):
                raise ValueError(f"Source frame order mismatch: {name}")
            if times[0] < 0 or any(b <= a for a, b in zip(times, times[1:])):
                raise ValueError(f"Invalid source timestamps: {name}")
            retained = video.get("retained_frames")
            if retained is not None and (not isinstance(retained, list) or not retained
                    or any(type(n) is not int or not 1 <= n <= len(times) for n in retained)
                    or retained != sorted(set(retained))):
                raise ValueError(f"Invalid retained frame inventory: {name}")
            self.videos.append({
                "id": index, "name": name, "times": times, "frame_count": len(times),
                "default_end": min(86.0, times[-1]) if index == 0 else times[-1],
                "preview": index == 0 and preview.is_file(),
                "retained_frames": video.get("retained_frames"),
                "retention_only": "retained_frames" in video,
            })
        self.read()

    def video(self, index):
        if type(index) is not int or not 0 <= index < len(self.videos):
            raise ValueError("Unknown recording")
        return self.videos[index]

    def frame(self, video, number):
        v = self.video(video)
        if type(number) is not int or not 1 <= number <= v["frame_count"]:
            raise ValueError("Frame is outside the source recording")
        path = self.archive / v["name"] / f"frame_{number:06d}.jpg"
        if path.resolve().parent != (self.archive / v["name"]).resolve():
            raise ValueError("Frame must remain inside its recording")
        if v["retention_only"] and number not in v["retained_frames"]:
            raise ValueError("This unselected frame was permanently deleted at the user's request")
        return path

    def read(self):
        raw = self.dataset.read_bytes()
        data = json.loads(raw)
        if not isinstance(data.get("cases"), list) or not isinstance(data.get("split_by_video"), dict):
            raise ValueError("Invalid canonical calibration dataset")
        return data, sha(raw)

    def state(self):
        data, revision = self.read()
        labels = []
        trash = {}
        for window in data.get("curated_windows", []):
            kept = set(window["keep_frames"])
            for number in range(window["first_frame"], window["last_frame"] + 1):
                trash[(window["video"], number)] = (
                    window["recorded_at"], number not in kept)
        for case in data["cases"]:
            if case.get("trash_history"):
                target = next(c for c in case["context"] if c["role"] == "target")
                key = (case["video"], target["source_frame"])
                event = case["trash_history"][-1]
                if key not in trash or event["recorded_at"] >= trash[key][0]:
                    trash[key] = (event["recorded_at"], event["trashed"])
            if case.get("label_history"):
                target = next(c for c in case["context"] if c["role"] == "target")
                event = case["label_history"][-1]
                labels.append({
                    "id": case["id"], "video": case["video"],
                    "frame": target["source_frame"], "time": target["source_time"],
                    "label": event["label"], "note": event.get("statement", ""),
                    "recorded_at": event["recorded_at"],
                })
        trashed = [{"video": name, "frame": frame}
                   for (name, frame), (_, removed) in trash.items() if removed]
        return {"revision": revision, "labels": labels, "trashed": trashed,
                "curated_windows": data.get("curated_windows", []),
                "preselections": data.get("preselections", []),
                "intervals": data.get("intervals", []),
                "split_by_video": data["split_by_video"],
                "dataset_path": str(self.dataset), "token": self.token}

    def context(self, index, number):
        v = self.video(index)
        times = v["times"]
        target_time = times[number - 1]
        result = []
        for role, t in (("before", max(0, target_time - 1)),
                        ("target", target_time), ("after", target_time + 1)):
            right = bisect.bisect_left(times, t)
            j = min((j for j in (right - 1, right) if 0 <= j < len(times)),
                    key=lambda j: abs(times[j] - t))
            path = self.frame(index, j + 1)
            result.append({"role": role, "source": str(path), "source_frame": j + 1,
                           "source_time": times[j], "sha256": sha(path.read_bytes())})
        return result

    def diagram(self, index):
        video = self.video(index)
        state = self.state()
        trash = {(x["video"], x["frame"]) for x in state["trashed"]}
        latest = {}
        for label in state["labels"]:
            if label["video"] != video["name"]:
                continue
            previous = latest.get(label["frame"])
            if previous is None or label["recorded_at"] >= previous["recorded_at"]:
                latest[label["frame"]] = label
        nodes = []
        for frame, label in sorted(latest.items()):
            if label["label"] != "new_state" or (video["name"], frame) in trash:
                continue
            self.frame(index, frame)
            nodes.append({
                "id": f"f{frame}", "frame": frame, "time": video["times"][frame - 1],
                "title": f"Frame {frame}", "note": label["note"],
                "image": f"/api/frame?video={index}&frame={frame}&width=160",
            })
        edges = [{"from": a["id"], "to": b["id"], "kind": "chronological",
                  "elapsed_seconds": b["time"] - a["time"]}
                 for a, b in zip(nodes, nodes[1:])]
        mermaid = ["flowchart TD"]
        for node in nodes:
            mermaid.append(f'  {node["id"]}["Frame {node["frame"]} | {node["time"]:.3f}s"]')
        for edge in edges:
            mermaid.append(f'  {edge["from"]} -->|later in recording| {edge["to"]}')
        return {"video": video["name"], "revision": state["revision"],
                "scope": "All approved, non-trashed frames in this recording, independent of review window.",
                "meaning": "Chronological sequence only; arrows do not assert actions, causality or branches.",
                "nodes": nodes, "edges": edges, "mermaid": "\n".join(mermaid)}

    def annotate(self, payload):
        index, first = payload.get("video"), payload.get("frame")
        v = self.video(index)
        if v["retention_only"]:
            raise ValueError("This completed recording is a retained-only, read-only archive after permanent cleanup")
        self.frame(index, first)
        last = payload.get("end_frame", first)
        self.frame(index, last)
        label, note = payload.get("label"), payload.get("note", "")
        action = payload.get("action", "label")
        if action not in ("label", "trash", "restore"):
            raise ValueError("Unknown dashboard action")
        if action != "label" and first != last:
            raise ValueError("Trash actions apply to individual frames")
        if action == "label" and (last < first or label not in LABELS or
                                  (last != first and label == "new_state")):
            raise ValueError("Invalid label or interval; keep labels apply to individual frames")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("Note must contain at most 2,000 characters")
        with self.lock, self.dataset.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data, revision = self.read()
            if payload.get("revision") != revision:
                raise ConflictError("Feedback changed in another window. Reload saved decisions, then retry.")
            event = {
                "label": label, "reviewer": self.reviewer, "statement": note,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "method": "explicit_dashboard_action",
            }
            if first == last:
                case_id = f"dashboard-v{index + 1}-frame-{first:06d}"
                case = next((c for c in data["cases"] if c["id"] == case_id), None)
                if case is None:
                    case = {"id": case_id, "video": v["name"],
                            "split": data["split_by_video"][v["name"]],
                            "context": self.context(index, first), "label_history": []}
                    data["cases"].append(case)
                if action == "label":
                    if any(t["video"] == v["name"] and t["frame"] == first
                           for t in self.state()["trashed"]):
                        raise ValueError("Restore this frame from Trash before labeling it")
                    case["label_history"].append(event)
                else:
                    case.setdefault("trash_history", []).append({
                        "trashed": action == "trash", "reviewer": self.reviewer,
                        "recorded_at": event["recorded_at"],
                        "method": "explicit_dashboard_action",
                    })
            else:
                interval_id = f"dashboard-v{index + 1}-range-{first:06d}-{last:06d}"
                intervals = data.setdefault("intervals", [])
                interval = next((r for r in intervals if r["id"] == interval_id), None)
                if interval is None:
                    interval = {"id": interval_id, "video": v["name"],
                                "split": data["split_by_video"][v["name"]],
                                "first_frame": first, "last_frame": last,
                                "start_time": v["times"][first - 1], "end_time": v["times"][last - 1],
                                "endpoint_sha256": [sha(self.frame(index, n).read_bytes())
                                                    for n in (first, last)],
                                "point_override_policy": "Individual frame decisions take priority",
                                "label_history": []}
                    intervals.append(interval)
                interval["label_history"].append(event)
            data["status"] = "human_labeling_in_progress"
            staging = self.dataset.with_suffix(".writing")
            with staging.open("w") as handle:
                json.dump(data, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            staging.replace(self.dataset)
            return self.state()

    @lru_cache(maxsize=160)
    def image(self, index, frame, width):
        path = self.frame(index, frame)
        if width == 0:
            return path.read_bytes()
        with Image.open(path) as image:
            image.thumbnail((width, width * 3))
            output = io.BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=85)
            return output.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if args and str(args[1]) not in ("200", "206", "304"):
            LOG.info(fmt, *args)

    @property
    def store(self):
        return self.server.store

    def allowed(self):
        return self.headers.get("Host") == self.server.authority

    def send(self, status, body, mime="application/json"):
        if status >= 400:
            LOG.warning("http_error %s", json.dumps({
                "status": status, "method": self.command,
                "route": urlsplit(self.path).path,
                "error": body.get("error") if isinstance(body, dict) else None,
            }))
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if mime != "image/jpeg" else "private, max-age=300")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        artifact = urlsplit(self.path).path == "/workspace-artifact"
        self.send_header("Content-Security-Policy",
                         ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
                          "frame-ancestors 'self'; base-uri 'none'; form-action 'none'") if artifact else
                         "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' blob: data:; media-src 'self'; connect-src 'self'; frame-src 'self'; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def preview(self):
        path = self.store.preview
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        value = self.headers.get("Range")
        if value:
            import re
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", value)
            if not match:
                raise ValueError("Unsupported media range")
            start = int(match[1])
            end = min(int(match[2]) if match[2] else end, size - 1)
            if start > end:
                self.send(416, {"error": "Media range outside preview"})
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self):
        if not self.allowed():
            self.send(403, {"error": "Local host mismatch"})
            return
        try:
            route = urlsplit(self.path)
            if route.path in ("/", "/app.js", "/enhancements.js", "/workspace.js", "/learning.js", "/style.css"):
                file, mime = {"/": ("index.html", "text/html; charset=utf-8"),
                              "/app.js": ("app.js", "text/javascript"),
                              "/enhancements.js": ("enhancements.js", "text/javascript"),
                              "/workspace.js": ("workspace.js", "text/javascript"),
                              "/learning.js": ("learning.js", "text/javascript"),
                              "/style.css": ("style.css", "text/css")}[route.path]
                self.send(200, (ASSETS / file).read_bytes(), mime)
            elif route.path == "/api/workspace":
                self.send(200, self.server.workspace.library())
            elif route.path == "/api/learning":
                query = parse_qs(route.query, strict_parsing=True)
                self.send(200, {"preview": learning_module.preview(self.store, int(query["video"][0])),
                                "history": learning_module.history(self.server.workspace.project)})
            elif route.path == "/api/learning/similar":
                query = parse_qs(route.query, strict_parsing=True)
                self.send(200, learning_module.similar(self.store, int(query["video"][0])))
            elif route.path == "/api/workspace/collection":
                query = parse_qs(route.query, strict_parsing=True)
                self.send(200, self.server.workspace.collection(query["run"][0], int(query["video"][0])))
            elif route.path == "/workspace-artifact":
                query = parse_qs(route.query, strict_parsing=True)
                self.send(200, self.server.workspace.artifact(query["name"][0]), "text/html; charset=utf-8")
            elif route.path == "/workspace-file":
                query = parse_qs(route.query, strict_parsing=True)
                path, mime = self.server.workspace.file(query["name"][0])
                if path.suffix == ".html":
                    raise ValueError("Open diagrams through the library")
                self.send(200, path.read_bytes(), mime)
            elif route.path == "/api/jira/types":
                query = parse_qs(route.query, strict_parsing=True)
                self.send(200, self.server.workspace.types(query["project"][0]))
            elif route.path == "/api/state":
                self.send(200, self.store.state())
            elif route.path == "/api/videos":
                self.send(200, self.store.videos)
            elif route.path == "/api/diagram":
                query = parse_qs(route.query, strict_parsing=True)
                self.send(200, self.store.diagram(int(query["video"][0])))
            elif route.path == "/api/diagnostics":
                path = self.server.log_path
                entries = path.read_text().splitlines()[-100:] if path and path.exists() else []
                self.send(200, {"log_path": str(path) if path else None, "entries": entries,
                                "source_directory": str(ASSETS)})
            elif route.path == "/api/export":
                data, revision = self.store.read()
                self.send(200, {"revision": revision, "dataset": data})
            elif route.path == "/api/frame":
                query = parse_qs(route.query, strict_parsing=True)
                width = int(query.get("width", ["660"])[0])
                if width not in (0, 160, 660):
                    raise ValueError("Unsupported preview size")
                self.send(200, self.store.image(int(query["video"][0]), int(query["frame"][0]), width),
                          "image/jpeg")
            elif route.path == "/preview":
                self.preview()
            else:
                self.send(404, {"error": "Not found"})
        except (ValueError, KeyError, IndexError) as error:
            self.send(400, {"error": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError:
            LOG.exception("Cannot read local source or dataset")
            self.send(503, {"error": "Cannot read local source or dataset. Check the external drive and server log."})

    def do_POST(self):
        if not self.allowed() or self.headers.get("Origin") != f"http://{self.server.authority}":
            self.send(403, {"error": "Only the local dashboard can save feedback"})
            return
        if self.headers.get("X-Review-Token") != self.store.token:
            self.send(403, {"error": "Invalid review session; reload the dashboard"})
            return
        if self.path not in ("/api/annotation", "/api/client-error", "/api/workspace/save",
                             "/api/jira/connect", "/api/jira/disconnect", "/api/jira/submit", "/api/learning/run"):
            self.send(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            limit = 34_000_000 if self.path == "/api/workspace/save" else (65536 if self.path == "/api/learning/run" else 8192)
            if not 0 < length <= limit:
                raise ValueError("Invalid feedback size")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Expected JSON feedback")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Expected a feedback object")
            if self.path == "/api/workspace/save":
                self.send(200, self.server.workspace.save(payload))
            elif self.path == "/api/learning/run":
                with self.server.workspace.lock:
                    result = learning_module.run(self.store, self.server.workspace.project, payload)
                self.send(200, result)
            elif self.path == "/api/jira/connect":
                self.send(200, self.server.workspace.connect(payload))
            elif self.path == "/api/jira/disconnect":
                with self.server.workspace.lock:
                    self.server.workspace.connection = None
                self.send(200, {"connected": False})
            elif self.path == "/api/jira/submit":
                self.send(200, self.server.workspace.submit(payload))
            elif self.path == "/api/client-error":
                message = payload.get("message")
                if not isinstance(message, str) or not 0 < len(message) <= 2000:
                    raise ValueError("Expected a bounded error message")
                # Record messages only, never headers, tokens, notes or request bodies.
                LOG.error("browser_error %s", json.dumps({"message": message}))
                self.send(200, {"logged": True})
            else:
                self.send(200, self.store.annotate(payload))
        except ConflictError as error:
            self.send(409, {"error": str(error)})
        except (ValueError, KeyError, TypeError) as error:
            self.send(400, {"error": str(error)})
        except OSError:
            LOG.exception("Cannot persist human feedback")
            self.send(503, {"error": "Feedback was not confirmed saved. Check disk access, then reload saved decisions."})


class ReviewServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        LOG.exception("Unhandled local dashboard request error")


def create_server(store, port, log_path=None):
    server = ReviewServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.store = store
    server.workspace = workspace_module.Workspace(store, PROJECT)
    server.log_path = log_path
    server.authority = f"127.0.0.1:{server.server_port}"
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--archive", type=Path, default=ARCHIVE,
        help="Local frame archive containing manifest.json (defaults to the configured archive)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    configure_logging()
    try:
        server = create_server(Store(archive=args.archive), args.port, LOG_PATH)
    except (OSError, ValueError):
        LOG.exception("Dashboard startup failed; check source drive, dataset and port")
        raise
    print(f"REVIEW_URL=http://{server.authority}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
