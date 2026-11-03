"""Local artifact library, reviewable QA drafts and explicit Jira Cloud submission."""
import base64
import binascii
import hashlib
import html
import io
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Workspace:
    def __init__(self, store, project):
        self.store, self.project = store, project
        self.root = project / "data/soda-review-workspace"
        self.lock = threading.Lock()
        self.connection = None

    def library(self):
        items = []
        for path in sorted((self.project / "docs").glob("soda-*.html")):
            items.append({"id": path.name, "kind": "diagram", "title": path.stem.replace("-", " "),
                          "url": "/workspace-artifact?name=" + path.name})
        for index, video in enumerate(self.store.videos):
            graph = self.store.diagram(index)
            items.append({"id": f"video-{index}", "kind": "collection",
                          "title": f"Recording {index + 1} - {len(graph['nodes'])} approved frames",
                          "video": index, "count": len(graph["nodes"])})
        for path in sorted((self.project / "data/soda-approved-frames").glob("*/manifest.json")):
            if not re.fullmatch(r"\d{8}T\d{12}Z", path.parent.name):
                continue
            package = json.loads(path.read_text())
            if package.get("status") != "verified":
                continue
            for record in package.get("videos", []):
                index = next((i for i, v in enumerate(self.store.videos) if v["name"] == record["video"]), None)
                if index is not None:
                    items.append({"id": f"snapshot-{path.parent.name}-{index}", "kind": "snapshot",
                                  "title": f"Saved collection {path.parent.name}: recording {index+1} ({record['count']} frames)",
                                  "run": path.parent.name, "video": index, "count": record["count"]})
        return {"items": items, "drafts": self.drafts(),
                "jira": {"connected": self.connection is not None,
                         "site": self.connection["site"] if self.connection else None,
                         "projects": self.connection.get("projects", []) if self.connection else []}}

    def artifact(self, name):
        if not re.fullmatch(r"soda-[a-zA-Z0-9-]+\.html", name):
            raise ValueError("Unknown diagram")
        path = self.project / "docs" / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("Unknown diagram")
        # Existing reports reference selected images and manifests relative to docs.
        def link(match):
            value = html.unescape(match[2])
            if value.startswith(("data:", "#")):
                return match[0]
            parsed = urlsplit(value)
            if parsed.scheme not in ("", "file"):
                return match[0]
            target = Path(unquote(parsed.path)) if parsed.scheme == "file" else path.parent / unquote(parsed.path)
            try:
                relative = target.resolve().relative_to(self.project.resolve()).as_posix()
                self.file(relative)
            except (ValueError, OSError):
                return match[0]
            url = ("/workspace-artifact?name=" + quote(target.name)) if target.suffix == ".html" else (
                "/workspace-file?name=" + quote(relative, safe=""))
            return f'{match[1]}="{html.escape(url, quote=True)}"'
        return re.sub(r'(href|src)="([^"]+)"', link, path.read_text()).encode()

    def collection(self, run, video):
        if not re.fullmatch(r"\d{8}T\d{12}Z", run):
            raise ValueError("Unknown saved collection")
        self.store.video(video)
        name = self.store.videos[video]["name"]
        package = json.loads((self.project / "data/soda-approved-frames" / run / "manifest.json").read_text())
        if package.get("status") != "verified":
            raise ValueError("Collection is not a verified export")
        record = next((r for r in package["videos"] if r["video"] == name), None)
        if record is None:
            raise ValueError("Recording not found in collection")
        nodes = []
        for entry in record["frames"]:
            path = f"data/soda-approved-frames/{run}/{name}/{entry['file']}"
            self.file(path)
            nodes.append({"frame": entry["frame"], "time": entry["timestamp_seconds"],
                          "image": "/workspace-file?name=" + quote(path, safe="")})
        return {"nodes": nodes, "revision": package["dataset_revision"]}

    def file(self, name):
        path = (self.project / name).resolve()
        relative = path.relative_to(self.project.resolve())
        valid = (relative.parts[:2] == ("data", "soda-approved-frames") or
                 relative.parts[:2] == (".ai-work", "temporary") or
                 (relative.parts[0] == "docs" and path.name.startswith("soda-")))
        mimes = {".jpg": "image/jpeg", ".png": "image/png", ".json": "application/json",
                 ".csv": "text/plain", ".tsv": "text/plain", ".mmd": "text/plain",
                 ".html": "text/html; charset=utf-8"}
        if not valid or path.suffix not in mimes or not path.is_file():
            raise ValueError("File is outside the review library")
        # Temporary files may include private backups. Only referenced public review assets are served.
        if relative.parts[:2] == (".ai-work", "temporary") and not (
                path.suffix in (".jpg", ".png") or
                path.name in ("connected-flow-frames.csv", "v7-selection.tsv", "review-manifest.json")):
            raise ValueError("Private temporary file")
        if path.suffix == ".html" and relative.parts[0] != "docs":
            raise ValueError("Only diagram HTML is served")
        return path, mimes[path.suffix]

    def drafts(self):
        return [json.loads(path.read_text()) for path in sorted(self.root.glob("*.json"))]

    def load(self, identifier):
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid draft ID")
        return json.loads((self.root / f"{identifier}.json").read_text())

    def persist(self, draft):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{draft['id']}.json"
        staging = path.with_suffix(".writing")
        with staging.open("w") as handle:
            json.dump(draft, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        staging.replace(path)
        if json.loads(path.read_text()) != draft:
            raise OSError("Draft readback failed")
        return draft

    def save(self, payload):
        with self.lock:
            old = self.load(payload["id"]) if payload.get("id") else None
            if old and (old["status"] != "draft" or payload.get("version") != old["version"]):
                raise ValueError("Draft changed or has a submission receipt; reload or create a new draft")
            fields = {}
            for key, limit in (("summary", 250), ("description", 20000), ("question", 4000),
                               ("artifact", 200), ("project", 100), ("issue_type", 100)):
                value = payload.get(key, "")
                if not isinstance(value, str) or len(value) > limit:
                    raise ValueError(f"Invalid {key}")
                fields[key] = value
            frames = payload.get("frames", [])
            uploads = payload.get("uploads", [])
            if not isinstance(frames, list) or not isinstance(uploads, list) or len(frames) + len(uploads) > 20:
                raise ValueError("Choose at most 20 attachments")
            attachments = []
            total = 0
            for frame in frames:
                video, number = frame["video"], frame["frame"]
                path = self.store.frame(video, number)
                raw = path.read_bytes()
                total += len(raw)
                attachments.append({"kind": "frame", "video": video, "frame": number,
                                    "time": self.store.video(video)["times"][number - 1],
                                    "name": f"v{video+1}-{path.name}", "sha256": hashlib.sha256(raw).hexdigest()})
            checked_uploads = []
            for upload in uploads:
                try:
                    raw = base64.b64decode(upload["data"], validate=True)
                    if not 0 < len(raw) <= 8_000_000:
                        raise ValueError("Each image must be at most 8 MB")
                    with Image.open(io.BytesIO(raw)) as image:
                        if image.format not in ("JPEG", "PNG", "WEBP") or image.width * image.height > 25_000_000:
                            raise ValueError("Use JPEG, PNG or WebP images up to 25 megapixels")
                        image.verify()
                except (binascii.Error, KeyError, OSError) as error:
                    raise ValueError("Invalid image attachment") from error
                name = Path(upload.get("name", "attachment")).name
                name = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)[:100] or "attachment"
                total += len(raw)
                checked_uploads.append({"name": name, "data": upload["data"]})
                attachments.append({"kind": "upload", "name": name,
                                    "sha256": hashlib.sha256(raw).hexdigest()})
            if total > 24_000_000:
                raise ValueError("Attachments exceed 24 MB total")
            if len({item["name"] for item in attachments}) != len(attachments):
                raise ValueError("Attachment filenames must be unique; rename duplicate uploads")
            draft = {**fields, "id": old["id"] if old else uuid.uuid4().hex,
                     "version": (old["version"] + 1) if old else 1, "status": "draft",
                     "updated_at": datetime.now(timezone.utc).isoformat(),
                     "frames": frames, "uploads": checked_uploads, "attachments": attachments,
                     "dataset_revision": self.store.read()[1]}
            self.persist(draft)
            return draft

    def connect(self, payload):
        site = payload.get("site", "")
        parsed = urlsplit(site)
        if (parsed.scheme != "https" or not parsed.hostname or
                not re.fullmatch(r"[a-z0-9-]+\.atlassian\.net", parsed.hostname) or
                parsed.port not in (None, 443) or parsed.username or parsed.password):
            raise ValueError("Enter an HTTPS Jira Cloud site or board URL on your tenant.atlassian.net")
        email, token = payload.get("email", ""), payload.get("token", "")
        if not isinstance(email, str) or not isinstance(token, str) or not email or not token or len(email) > 320 or len(token) > 4000:
            raise ValueError("Enter your Jira email and API token; credentials stay in server memory")
        connection = {"site": f"https://{parsed.hostname}",
                      "authorization": "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()}
        projects = self.jira("/rest/api/3/project/search?maxResults=100", connection=connection)
        connection["projects"] = projects.get("values", [])
        with self.lock:
            self.connection = connection
        return {"site": connection["site"], "projects": projects.get("values", []),
                "note": "First 100 accessible projects. Tickets belong to projects; board filters control visibility."}

    def jira(self, path, method="GET", body=None, content_type="application/json", connection=None):
        connection = connection or self.connection
        if not connection:
            raise ValueError("Connect Jira Cloud first")
        headers = {"Authorization": connection["authorization"], "Accept": "application/json",
                   "Content-Type": content_type}
        if content_type.startswith("multipart/"):
            headers["X-Atlassian-Token"] = "no-check"
        request = Request(connection["site"] + path, data=body, headers=headers, method=method)
        try:
            with build_opener(NoRedirect()).open(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            raise ValueError(f"Jira returned HTTP {error.code}. Check permissions, required fields and attachment settings.") from error
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise ValueError("Jira response was not confirmed. Inspect Jira before retrying.") from error

    def types(self, project):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", project):
            raise ValueError("Choose a Jira project key")
        return self.jira(f"/rest/api/3/issue/createmeta/{project}/issuetypes?maxResults=100")

    def submit(self, payload):
        with self.lock:
            draft = self.load(payload["id"])
            if draft["version"] != payload.get("version") or draft["status"] != "draft":
                raise ValueError("Draft changed or already submitted; reload its receipt")
            if payload.get("confirm") is not True:
                raise ValueError("Explicit confirmation is required to send this draft and its images")
            if not self.connection or payload.get("site") != self.connection["site"]:
                raise ValueError("Jira destination changed; review and confirm again")
            if not draft["summary"].strip() or not draft["description"].strip():
                raise ValueError("Add a summary and description")
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", draft["project"]) or not draft["issue_type"].isdigit():
                raise ValueError("Choose a project and issue type")
            text = draft["description"] + "\n\nEvidence snapshot: " + draft["dataset_revision"]
            for item in draft["attachments"]:
                text += f"\n{item['name']} | SHA-256 {item['sha256']}"
                if item["kind"] == "frame":
                    text += f" | recording {item['video']+1}, frame {item['frame']}, {item['time']:.6f}s"
            files = []
            for item in draft["attachments"]:
                if item["kind"] == "frame":
                    raw = self.store.frame(item["video"], item["frame"]).read_bytes()
                else:
                    upload = next(x for x in draft["uploads"] if x["name"] == item["name"])
                    raw = base64.b64decode(upload["data"])
                if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    raise ValueError("Attachment changed since review; save a new draft")
                files.append((item["name"], raw))
            draft.update(status="sending", site=self.connection["site"])
            self.persist(draft)
            fields = {"project": {"key": draft["project"]}, "issuetype": {"id": draft["issue_type"]},
                      "summary": draft["summary"], "description": {"type": "doc", "version": 1,
                      "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}]}
                                  for line in text.splitlines() if line]}}
            try:
                issue = self.jira("/rest/api/3/issue", "POST", json.dumps({"fields": fields}).encode())
                key = issue.get("key", "")
                if not re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", key):
                    raise ValueError("Jira returned no valid issue key; inspect Jira before retrying")
                draft.update(status="created", issue_key=key,
                             issue_url=self.connection["site"] + "/browse/" + key)
                self.persist(draft)
                if files:
                    boundary = uuid.uuid4().hex
                    body = b""
                    for name, raw in files:
                        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                                 "Content-Type: application/octet-stream\r\n\r\n").encode() + raw + b"\r\n"
                    body += f"--{boundary}--\r\n".encode()
                    self.jira(f"/rest/api/3/issue/{key}/attachments", "POST", body,
                              f"multipart/form-data; boundary={boundary}")
                draft["status"] = "submitted"
            except ValueError as error:
                draft["status"] = "attachments_unconfirmed" if draft.get("issue_key") else "creation_unconfirmed"
                draft["error"] = str(error)
            return self.persist(draft)
