import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image


SPEC = importlib.util.spec_from_file_location("review_server", Path(__file__).with_name("server.py"))
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = review.PROJECT / ".ai-work/temporary"
        temporary.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="dashboard-test-", dir=temporary)
        self.root = Path(self.temp.name)
        self.archive = self.root / "archive"
        source = self.archive / "fixture-recording"
        source.mkdir(parents=True)
        for i in range(1, 11):
            Image.new("RGB", (30, 60), (i * 20, 30, 60)).save(source / f"frame_{i:06d}.jpg")
        (source / "frame_timestamps.csv").write_text(
            "frame_number,timestamp_seconds\n" + "\n".join(f"{i+1},{i/10}" for i in range(10)))
        (self.archive / "manifest.json").write_text(json.dumps({"videos": [{
            "output_directory": "fixture-recording", "extracted_frame_count": 10,
            "timestamps_file": "frame_timestamps.csv",
        }]}))
        self.dataset = self.root / "dataset.json"
        self.dataset.write_text(json.dumps({"cases": [], "split_by_video": {"fixture-recording": "calibration"}}))
        self.store = review.Store(self.archive, self.dataset, self.root / "no-preview.mp4", "Synthetic test")
        self.server = review.create_server(self.store, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://{self.server.authority}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def post(self, payload, origin=True):
        headers = {"Content-Type": "application/json", "X-Review-Token": self.store.token}
        if origin:
            headers["Origin"] = self.base
        return urlopen(Request(self.base + "/api/annotation", json.dumps(payload).encode(), headers=headers))

    def label(self, **overrides):
        return {"video": 0, "frame": 3, "label": "new_state", "note": "Fixture only",
                "revision": self.store.state()["revision"], **overrides}

    def test_save_reload_and_change_preserve_history_and_source(self):
        before = self.store.frame(0, 3).read_bytes()
        with self.post(self.label()) as response:
            state = json.load(response)
        self.assertEqual(state["labels"][0]["label"], "new_state")
        restored = review.Store(self.archive, self.dataset, self.root / "none")
        self.assertEqual(restored.state()["labels"], state["labels"])
        with self.post(self.label(label="animation")) as response:
            self.assertEqual(json.load(response)["labels"][0]["label"], "animation")
        data, _ = self.store.read()
        self.assertEqual(len(data["cases"][0]["label_history"]), 2)
        self.assertEqual(data["cases"][0]["context"][1]["source_frame"], 3)
        self.assertEqual(before, self.store.frame(0, 3).read_bytes())

    def test_retained_only_archive_preserves_time_mapping_and_refuses_deleted_frames(self):
        self.post(self.label()).close()
        path = self.archive / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["videos"][0]["retained_frames"] = [3]
        path.write_text(json.dumps(manifest))
        store = review.Store(self.archive, self.dataset, self.root / "no-preview")
        self.assertTrue(store.video(0)["retention_only"])
        self.assertEqual(store.video(0)["frame_count"], 10)
        self.assertEqual(store.video(0)["times"][2], .2)
        self.assertTrue(store.frame(0, 3).is_file())
        self.assertRaisesRegex(ValueError, "permanently deleted", store.frame, 0, 4)
        self.assertRaisesRegex(ValueError, "read-only", store.annotate, self.label())
        self.assertEqual(len(store.diagram(0)["nodes"]), 1)

    def test_stale_revision_refuses_write(self):
        payload = self.label()
        self.post(payload).close()
        before = self.dataset.read_bytes()
        with self.assertRaises(HTTPError) as error:
            self.post(payload)
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(before, self.dataset.read_bytes())

    def test_trash_restore_retains_labels_and_originals(self):
        source = self.store.frame(0, 3).read_bytes()
        self.post(self.label()).close()
        self.post(self.label(action="trash")).close()
        state = review.Store(self.archive, self.dataset, self.root / "none").state()
        self.assertEqual(state["trashed"], [{"video": "fixture-recording", "frame": 3}])
        self.assertEqual(state["labels"][0]["label"], "new_state")
        with self.assertRaises(HTTPError) as error:
            self.post(self.label())
        self.assertEqual(error.exception.code, 400)

        self.post(self.label(action="restore")).close()
        self.assertEqual(self.store.state()["trashed"], [])
        data, _ = self.store.read()
        self.assertEqual(len(data["cases"][0]["label_history"]), 1)
        self.assertEqual([e["trashed"] for e in data["cases"][0]["trash_history"]], [True, False])
        self.assertEqual(source, self.store.frame(0, 3).read_bytes())
        for overrides in ({"action": "delete"}, {"action": "trash", "end_frame": 5}):
            with self.assertRaises(HTTPError) as error:
                self.post(self.label(**overrides))
            self.assertEqual(error.exception.code, 400)

    def test_curated_window_hides_only_unselected_and_allows_restore(self):
        data, _ = self.store.read()
        data["curated_windows"] = [{
            "video": "fixture-recording", "first_frame": 1, "last_frame": 8,
            "keep_frames": [1, 3], "recorded_at": "2026-01-01T00:00:00+00:00",
        }]
        self.dataset.write_text(json.dumps(data))
        self.assertEqual([x["frame"] for x in self.store.state()["trashed"]], [2, 4, 5, 6, 7, 8])
        self.post(self.label(frame=2, action="restore")).close()
        self.assertNotIn(2, [x["frame"] for x in self.store.state()["trashed"]])
        self.post(self.label(frame=2)).close()
        self.assertRaises(HTTPError, self.post, self.label(frame=4))
        self.assertEqual(len(list((self.archive / "fixture-recording").glob("*.jpg"))), 10)

    def test_interval_and_individual_keep_are_separate(self):
        self.post(self.label(frame=2, end_frame=5, label="animation")).close()
        self.post(self.label(frame=6)).close()
        data, _ = self.store.read()
        self.assertEqual(len(data["cases"]), 1)
        self.assertEqual(data["intervals"][0]["first_frame"], 2)
        self.assertEqual(data["intervals"][0]["last_frame"], 5)
        self.assertEqual(data["intervals"][0]["label_history"][0]["reviewer"], "Synthetic test")
        self.assertEqual(len(data["intervals"][0]["endpoint_sha256"]), 2)

    def test_preselection_is_not_human_feedback_or_trash(self):
        data, _ = self.store.read()
        data["preselections"] = [{
            "video": "fixture-recording", "frames": [
                {"source_frame": 3, "source_time": .2, "claim": "Synthetic suggestion"}],
        }]
        self.dataset.write_text(json.dumps(data))
        state = self.store.state()
        self.assertEqual(state["preselections"], data["preselections"])
        self.assertEqual(state["labels"], [])
        self.assertEqual(state["trashed"], [])
        self.post(self.label()).close()
        self.assertEqual(self.store.state()["preselections"], data["preselections"])

    def test_validation_and_origin_refuse_write(self):
        before = self.dataset.read_bytes()
        for overrides in ({"frame": 0}, {"frame": 11}, {"label": "invented"},
                          {"video": -1}, {"frame": True}, {"end_frame": 7},
                          {"note": "x" * 2001}, {"end_frame": 1}):
            with self.assertRaises(HTTPError) as error:
                self.post(self.label(**overrides))
            self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            self.post(self.label(), origin=False)
        self.assertEqual(error.exception.code, 403)
        self.assertEqual(before, self.dataset.read_bytes())

    def test_source_gallery_and_export_never_write(self):
        before = self.dataset.read_bytes()
        for route in ("/", "/style.css", "/app.js", "/api/videos", "/api/state",
                      "/api/export", "/api/frame?video=0&frame=2&width=160"):
            with urlopen(self.base + route) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read())
        self.assertEqual(before, self.dataset.read_bytes())
        with self.assertRaises(HTTPError):
            urlopen(self.base + "/api/frame?video=0&frame=100")
        with self.assertRaises(HTTPError):
            urlopen(self.base + "/../../dataset.json")

    def test_host_and_session_token_refuse_write(self):
        payload = json.dumps(self.label()).encode()
        headers = {"Origin": self.base, "Content-Type": "application/json",
                   "X-Review-Token": "wrong"}
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.base + "/api/annotation", payload, headers))
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.base + "/api/state", headers={"Host": "untrusted.example"}))
        self.assertEqual(error.exception.code, 403)

    def test_diagram_uses_only_latest_approved_nontrashed_points(self):
        self.post(self.label(frame=8)).close()
        self.post(self.label(frame=2)).close()
        self.post(self.label(frame=4, label="uncertain")).close()
        self.post(self.label(frame=5)).close()
        self.post(self.label(frame=5, action="trash")).close()
        self.post(self.label(frame=6)).close()
        self.post(self.label(frame=6, label="same_state")).close()
        before = self.dataset.read_bytes()
        with urlopen(self.base + "/api/diagram?video=0") as response:
            graph = json.load(response)
        self.assertEqual([n["frame"] for n in graph["nodes"]], [2, 8])
        self.assertEqual(graph["edges"][0]["from"], "f2")
        self.assertEqual(graph["edges"][0]["to"], "f8")
        self.assertAlmostEqual(graph["edges"][0]["elapsed_seconds"], .6)
        self.assertEqual(graph["revision"], review.sha(before))
        self.assertIn("f2 -->|later in recording| f8", graph["mermaid"])
        self.assertEqual(before, self.dataset.read_bytes())
        with self.assertRaises(HTTPError):
            urlopen(self.base + "/api/diagram?video=-1")

    def test_empty_diagram_and_local_error_log(self):
        self.assertEqual(self.store.diagram(0)["nodes"], [])
        log_path = self.root / "dashboard.log"
        handler = review.configure_logging(log_path)
        self.server.log_path = log_path
        before = self.dataset.read_bytes()
        try:
            headers = {"Origin": self.base, "Content-Type": "application/json",
                       "X-Review-Token": self.store.token}
            def report(message, origin=True):
                h = dict(headers)
                if not origin:
                    h.pop("Origin")
                return urlopen(Request(self.base + "/api/client-error",
                                      json.dumps({"message": message}).encode(), headers=h))
            with report("Synthetic image failure") as response:
                self.assertTrue(json.load(response)["logged"])
            with self.assertRaises(HTTPError):
                report("x" * 2001)
            with self.assertRaises(HTTPError):
                report("not allowed", origin=False)
            with urlopen(self.base + "/api/diagnostics") as response:
                diagnostics = json.load(response)
            self.assertEqual(diagnostics["log_path"], str(log_path))
            self.assertTrue(any("Synthetic image failure" in line for line in diagnostics["entries"]))
            self.assertNotIn(self.store.token, log_path.read_text())
            self.assertNotIn("not allowed", log_path.read_text())
            self.assertEqual(before, self.dataset.read_bytes())
        finally:
            review.LOG.removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    unittest.main()
