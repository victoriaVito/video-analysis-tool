"""Isolated workspace tests. No real Jira requests or human feedback writes."""
import base64
import importlib.util
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

SPEC = importlib.util.spec_from_file_location("workspace_test_server", Path(__file__).with_name("server.py"))
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)
module = server.workspace_module


class FixtureStore:
    videos = [{"name": "fixture"}]

    def __init__(self, root):
        self.path = root / "frame_000001.jpg"
        Image.new("RGB", (20, 40), "red").save(self.path)

    def frame(self, video, frame):
        if type(video) is not int or video != 0 or type(frame) is not int or frame != 1:
            raise ValueError("Unknown frame")
        return self.path

    def video(self, index):
        if index != 0:
            raise ValueError("Unknown recording")
        return {"times": [0.0]}

    def diagram(self, index):
        return {"nodes": [{"frame": 1}]}

    def read(self):
        return {}, "fixture-revision"


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        root = server.PROJECT / ".ai-work/temporary"
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="workspace-test-", dir=root)
        self.project = Path(self.temp.name)
        (self.project / "docs").mkdir()
        (self.project / "docs/soda-fixture.html").write_text("<h1>Fixture</h1>")
        self.store = FixtureStore(self.project)
        self.workspace = module.Workspace(self.store, self.project)

    def tearDown(self):
        self.temp.cleanup()

    def draft(self, **overrides):
        return self.workspace.save({"summary": "Fixture QA", "description": "Observed fixture only",
                                    "project": "TEST", "issue_type": "10001",
                                    "frames": [{"video": 0, "frame": 1}], **overrides})

    def connection(self):
        self.workspace.connection = {"site": "https://fixture.atlassian.net", "authorization": "Basic secret"}

    def test_library_and_local_draft_persist_without_feedback(self):
        data = self.workspace.library()
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(self.workspace.artifact("soda-fixture.html"), b"<h1>Fixture</h1>")
        self.assertRaises(ValueError, self.workspace.artifact, "../outside.html")
        before = self.store.path.read_bytes()
        draft = self.draft()
        self.assertEqual(self.workspace.load(draft["id"]), draft)
        self.assertEqual(draft["attachments"][0]["time"], 0)
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertNotIn("secret", json.dumps(self.workspace.library()))
        newer = self.draft(id=draft["id"], version=draft["version"])
        self.assertEqual(newer["version"], 2)
        self.assertRaises(ValueError, self.draft, id=draft["id"], version=draft["version"])

    def test_upload_validation_and_limits(self):
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, "PNG")
        upload = {"name": "../../evidence.png", "data": base64.b64encode(buf.getvalue()).decode()}
        draft = self.draft(uploads=[upload])
        self.assertEqual(draft["uploads"][0]["name"], "evidence.png")
        self.assertRaises(ValueError, self.draft, uploads=[upload, upload])
        self.assertRaises(ValueError, self.draft, uploads=[{"data": "not-base64"}])
        self.assertRaises(ValueError, self.draft, frames=[{"video": True, "frame": 1}])
        self.assertRaises(ValueError, self.draft, frames=[{"video": 0, "frame": 1}] * 21)

    def test_context_is_explicit_local_request(self):
        draft = self.workspace.save({"summary": "Context request", "question": "Explain fixture",
                                     "artifact": "soda-fixture.html", "description": "Read evidence first."})
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["attachments"], [])
        self.assertEqual(self.workspace.drafts()[0]["question"], "Explain fixture")

    def test_connection_restricts_destinations_and_never_persists_credentials(self):
        for site in ("http://fixture.atlassian.net", "https://evil.example",
                     "https://fixture.atlassian.net.evil.example", "https://user@fixture.atlassian.net"):
            self.assertRaises(ValueError, self.workspace.connect,
                              {"site": site, "email": "fixture@example.com", "token": "secret"})
        with patch.object(self.workspace, "jira", return_value={"values": [{"key": "TEST"}]}) as request:
            result = self.workspace.connect({"site": "https://fixture.atlassian.net/jira/software/projects/TEST/boards/1",
                                             "email": "fixture@example.com", "token": "secret"})
        self.assertEqual(result["site"], "https://fixture.atlassian.net")
        self.assertEqual(request.call_count, 1)
        self.assertNotIn("authorization", json.dumps(self.workspace.library()))
        self.assertFalse(self.workspace.root.exists())

    def test_submission_requires_confirmation_and_attaches_once(self):
        self.connection()
        draft = self.draft()
        payload = {"id": draft["id"], "version": draft["version"], "site": self.workspace.connection["site"]}
        self.assertRaises(ValueError, self.workspace.submit, payload)
        with patch.object(self.workspace, "jira", side_effect=[{"key": "TEST-1"}, [{"id": "42"}]]) as request:
            result = self.workspace.submit({**payload, "confirm": True})
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(request.call_count, 2)
        create = json.loads(request.call_args_list[0].args[2])
        self.assertEqual(create["fields"]["description"]["type"], "doc")
        self.assertIn(b'filename="v1-frame_000001.jpg"', request.call_args_list[1].args[2])
        self.assertRaises(ValueError, self.workspace.submit, {**payload, "confirm": True})

    def test_partial_and_uncertain_submissions_are_not_retried(self):
        self.connection()
        for responses, expected in (([ValueError("Unknown response")], "creation_unconfirmed"),
                                    ([{"key": "TEST-2"}, ValueError("Upload unknown")], "attachments_unconfirmed")):
            draft = self.draft()
            payload = {"id": draft["id"], "version": 1, "confirm": True,
                       "site": self.workspace.connection["site"]}
            with patch.object(self.workspace, "jira", side_effect=responses):
                result = self.workspace.submit(payload)
            self.assertEqual(result["status"], expected)
            self.assertRaises(ValueError, self.workspace.submit, payload)
            self.assertEqual(self.workspace.load(draft["id"])["status"], expected)

    def test_changed_evidence_refuses_remote_write(self):
        self.connection()
        draft = self.draft()
        self.store.path.write_bytes(b"changed")
        with patch.object(self.workspace, "jira") as request:
            self.assertRaises(ValueError, self.workspace.submit, {"id": draft["id"], "version": 1,
                              "confirm": True, "site": self.workspace.connection["site"]})
            request.assert_not_called()

    def test_artifact_links_and_file_scope(self):
        folder = self.project / "data/soda-approved-frames/fixture"
        folder.mkdir(parents=True)
        (folder / "manifest.json").write_text("{}")
        (self.project / "docs/soda-fixture.html").write_text(
            '<a href="../data/soda-approved-frames/fixture/manifest.json">Manifest</a>')
        self.assertIn(b"/workspace-file?name=data%2Fsoda-approved-frames", self.workspace.artifact("soda-fixture.html"))
        for name in ("../../outside", ".env", "data/soda-review-workspace/anything.json",
                     ".ai-work/temporary/dataset-before.json"):
            self.assertRaises((ValueError, OSError), self.workspace.file, name)

    def test_saved_collection_is_distinct_from_current_approvals(self):
        run = "20261027T173500826227Z"
        folder = self.project / "data/soda-approved-frames" / run / "fixture"
        folder.mkdir(parents=True)
        (folder / "frame_000001.jpg").write_bytes(self.store.path.read_bytes())
        package = {"status": "verified", "dataset_revision": "historical-revision",
                   "videos": [{"video": "fixture", "count": 1,
                               "frames": [{"frame": 1, "timestamp_seconds": 0.0, "file": "frame_000001.jpg"}]}]}
        (folder.parent / "manifest.json").write_text(json.dumps(package))
        items = self.workspace.library()["items"]
        self.assertEqual(items[-1]["kind"], "snapshot")
        graph = self.workspace.collection(run, 0)
        self.assertEqual(graph["revision"], "historical-revision")
        self.assertEqual(graph["nodes"][0]["frame"], 1)
        self.assertRaises(ValueError, self.workspace.collection, "../../elsewhere", 0)

    def test_workspace_http_origin_token_and_persistence(self):
        live = server.create_server(self.store, 0)
        live.workspace = self.workspace
        thread = threading.Thread(target=live.serve_forever, daemon=True)
        thread.start()
        base = f"http://{live.authority}"
        self.store.token = "fixture-session"
        try:
            with urlopen(base + "/api/workspace") as response:
                self.assertEqual(len(json.load(response)["items"]), 2)
            with urlopen(base + "/workspace-artifact?name=soda-fixture.html") as response:
                self.assertIn("frame-ancestors 'self'", response.headers["Content-Security-Policy"])
                self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
            body = json.dumps({"summary": "HTTP fixture", "description": "Fixture"}).encode()
            headers = {"Content-Type": "application/json", "Origin": base, "X-Review-Token": self.store.token}
            with urlopen(Request(base + "/api/workspace/save", body, headers)) as response:
                draft = json.load(response)
            self.assertEqual(self.workspace.load(draft["id"])["summary"], "HTTP fixture")
            for name in ("Origin", "X-Review-Token"):
                altered = dict(headers)
                altered.pop(name)
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + "/api/workspace/save", body, altered))
                self.assertEqual(error.exception.code, 403)
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/workspace-file?name=.env")
            self.assertEqual(error.exception.code, 400)
        finally:
            live.shutdown()
            live.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
