"""Isolated learning closeout tests; never touch real feedback or recordings."""
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

SPEC = importlib.util.spec_from_file_location("learning_fixture", Path(__file__).with_name("test_server.py"))
fixture_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture_module)
learning = fixture_module.review.learning_module


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.ReviewTests()
        self.fixture.setUp()
        self.store = self.fixture.store
        self.project = self.fixture.root
        (self.project / "docs").mkdir()
        self.fixture.server.workspace.project = self.project

    def tearDown(self):
        self.fixture.tearDown()

    def select(self):
        for number in (3, 7):
            self.fixture.post(self.fixture.label(frame=number)).close()

    def payload(self, **overrides):
        return {"video": 0, "revision": self.store.read()[1], "confirm": True,
                "hide_nonselected": True, "save_diagram": True, "compare": True,
                "notes": "Retain text changes, not just large image changes.", **overrides}

    def run_closeout(self, **overrides):
        return learning.run(self.store, self.project, self.payload(**overrides))

    def test_curation_preserves_sources_labels_split_and_readback(self):
        self.select()
        before, _ = self.store.read()
        originals = {p.name: p.read_bytes() for p in (self.store.archive / "fixture-recording").glob("*.jpg")}
        result = self.run_closeout(notes="<script>not executable</script>")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["recoverably_hidden"], 8)
        after, _ = self.store.read()
        self.assertEqual(after["cases"], before["cases"])
        self.assertEqual(after["split_by_video"], before["split_by_video"])
        self.assertEqual([n["frame"] for n in self.store.diagram(0)["nodes"]], [3, 7])
        self.assertEqual(len(self.store.state()["trashed"]), 8)
        self.assertEqual(originals, {p.name: p.read_bytes() for p in (self.store.archive / "fixture-recording").glob("*.jpg")})
        package = json.loads((self.project / result["package"] / "manifest.json").read_text())
        self.assertEqual(package["status"], "verified")
        for entry in package["videos"][0]["frames"]:
            raw = (self.project / result["package"] / "fixture-recording" / entry["file"]).read_bytes()
            self.assertEqual(learning.digest(raw), entry["sha256"])
        page = (self.project / "docs" / result["diagram"]).read_text()
        self.assertEqual(page.count('src="data:image/jpeg;base64,'), 2)
        self.assertIn("&lt;script&gt;", page)
        self.assertFalse(result["model_fitted"])
        self.assertEqual(learning.history(self.project)[0], result)

    def test_optional_archive_only_does_not_modify_feedback(self):
        self.select()
        before = self.store.dataset.read_bytes()
        result = self.run_closeout(hide_nonselected=False, save_diagram=False, compare=False)
        self.assertEqual(before, self.store.dataset.read_bytes())
        self.assertNotIn("diagram", result)
        analysis = json.loads((self.project / "data/soda-review-learning" / result["id"] / "analysis.json").read_text())
        self.assertNotIn("comparisons", analysis)

    def test_rejects_empty_unconfirmed_stale_and_invalid_options(self):
        self.assertRaises(ValueError, self.run_closeout)
        self.select()
        before = self.store.dataset.read_bytes()
        for options in ({"confirm": False}, {"revision": "stale"}, {"hide_nonselected": "yes"},
                        {"video": -1}, {"notes": "x" * 12001}):
            self.assertRaises(ValueError, self.run_closeout, **options)
        self.assertEqual(before, self.store.dataset.read_bytes())
        self.assertEqual(learning.history(self.project), [])

    def test_changed_feedback_during_export_is_not_overwritten(self):
        self.select()
        write = learning.write_json
        def concurrent_write(path, data):
            write(path, data)
            if path.name == "analysis.json":
                self.store.annotate(self.fixture.label(frame=5, label="uncertain"))
        with patch.object(learning, "write_json", side_effect=concurrent_write):
            self.assertRaises(ValueError, self.run_closeout)
        data, _ = self.store.read()
        self.assertEqual(len(data["cases"]), 3)
        self.assertNotIn("curated_windows", data)
        self.assertEqual(learning.history(self.project)[0]["status"], "incomplete")
        self.assertEqual(list((self.project / "data/soda-approved-frames").glob("*/manifest.json")), [])

    def test_missing_source_inventory_refuses_curation(self):
        self.select()
        before = self.store.dataset.read_bytes()
        self.store.frame(0, 1).unlink()
        self.assertRaises(ValueError, self.run_closeout)
        self.assertEqual(self.store.dataset.read_bytes(), before)
        self.assertEqual(learning.history(self.project)[0]["status"], "incomplete")

    def test_partial_artifact_failure_preserves_curation_receipt(self):
        self.select()
        write = Path.write_text
        def failing_html(path, text, *args, **kwargs):
            if path.suffix == ".html":
                raise OSError("Fixture diagram disk failure")
            return write(path, text, *args, **kwargs)
        with patch.object(Path, "write_text", failing_html):
            self.assertRaises(ValueError, self.run_closeout)
        receipt = learning.history(self.project)[0]
        self.assertEqual(receipt["status"], "incomplete")
        self.assertTrue(receipt["curation_applied"])
        self.assertIn("Fixture diagram disk failure", receipt["error"])
        self.assertEqual(len(self.store.state()["trashed"]), 8)

    def test_http_preview_authorization_and_closeout(self):
        self.select()
        base = self.fixture.base
        with urlopen(base + "/api/learning?video=0") as response:
            self.assertEqual(json.load(response)["preview"]["selected"], 2)
        payload = self.payload()
        headers = {"Content-Type": "application/json", "X-Review-Token": self.store.token}
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(base + "/api/learning/run", json.dumps(payload).encode(), headers=headers))
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        headers["Origin"] = base
        with urlopen(Request(base + "/api/learning/run", json.dumps(payload).encode(), headers=headers)) as response:
            self.assertEqual(json.load(response)["status"], "complete")

    def test_similarity_scan_proposes_but_never_mutates(self):
        self.store.frame(0, 7).write_bytes(self.store.frame(0, 3).read_bytes())
        self.select()
        before = self.store.dataset.read_bytes()
        with urlopen(self.fixture.base + "/api/learning/similar?video=0") as response:
            scan = json.load(response)
        self.assertEqual(scan["candidate_pairs"], 1)
        self.assertTrue(scan["pairs"][0]["identical_file"])
        self.assertEqual(scan["pairs"][0]["left"]["frame"], 3)
        self.assertEqual(scan["pairs"][0]["right"]["frame"], 7)
        self.assertEqual(before, self.store.dataset.read_bytes())

    def test_reviewed_similar_discard_updates_trash_package_and_diagrams(self):
        self.store.frame(0, 7).write_bytes(self.store.frame(0, 3).read_bytes())
        self.select()
        scan = learning.similar(self.store, 0)
        cases = self.store.read()[0]["cases"]
        source = self.store.frame(0, 7).read_bytes()
        result = self.run_closeout(discard_frames=[7], similarity_hashes=scan["hashes"])
        self.assertEqual(result["selected"], 1)
        self.assertEqual(result["selected_before_cleanup"], 2)
        self.assertEqual(result["discarded_similar"][0]["frame"], 7)
        self.assertEqual(result["recoverably_hidden"], 9)
        self.assertEqual([n["frame"] for n in self.store.diagram(0)["nodes"]], [3])
        self.assertEqual(self.store.read()[0]["cases"], cases)
        self.assertEqual(self.store.frame(0, 7).read_bytes(), source)
        manifest = json.loads((self.project / result["package"] / "manifest.json").read_text())
        self.assertEqual([f["frame"] for f in manifest["videos"][0]["frames"]], [3])
        graph = json.loads((self.project / "docs" / result["diagram"]).with_suffix(".json").read_text())
        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(graph["edges"], [])
        self.assertNotIn("f7[", graph["mermaid"])
        self.assertIn({"video": "fixture-recording", "frame": 7}, self.store.state()["trashed"])
        self.store.annotate(self.fixture.label(frame=7, action="restore"))
        self.assertEqual([n["frame"] for n in self.store.diagram(0)["nodes"]], [3, 7])

    def test_invalid_or_unreviewed_similar_discards_refuse_write(self):
        self.select()
        before = self.store.dataset.read_bytes()
        scan = learning.similar(self.store, 0)
        for options in ({"discard_frames": [7]}, {"discard_frames": [3, 7]},
                        {"discard_frames": [1]}, {"discard_frames": [True]},
                        {"discard_frames": [7, 7]}, {"discard_frames": "7"},
                        {"discard_frames": [7], "similarity_hashes": scan["hashes"], "hide_nonselected": False}):
            self.assertRaises(ValueError, self.run_closeout, **options)
        self.assertEqual(self.store.dataset.read_bytes(), before)
        self.assertEqual(learning.history(self.project), [])
        self.store.frame(0, 7).write_bytes(self.store.frame(0, 1).read_bytes())
        self.assertRaises(ValueError, self.run_closeout, discard_frames=[7], similarity_hashes=scan["hashes"])
        self.assertEqual(self.store.dataset.read_bytes(), before)

    def test_dissimilar_frames_are_not_candidates(self):
        self.select()
        scan = learning.similar(self.store, 0)
        self.assertEqual(scan["pairs"], [])

    def test_similarity_threshold_and_pair_limit(self):
        Image.new("RGB", (30, 60), (0, 0, 0)).save(self.store.frame(0, 3), format="PNG")
        Image.new("RGB", (30, 60), (15, 0, 0)).save(self.store.frame(0, 7), format="PNG")
        self.select()
        scan = learning.similar(self.store, 0)
        self.assertEqual(scan["pairs"][0]["difference"], 5.0)
        self.assertFalse(scan["pairs"][0]["identical_file"])
        Image.new("RGB", (30, 60), (16, 0, 0)).save(self.store.frame(0, 7), format="PNG")
        self.assertEqual(learning.similar(self.store, 0)["pairs"], [])
        for number in range(1, 11):
            Image.new("RGB", (30, 60), (0, 0, 0)).save(self.store.frame(0, number), format="PNG")
            if number not in (3, 7):
                self.fixture.post(self.fixture.label(frame=number)).close()
        scan = learning.similar(self.store, 0)
        self.assertEqual(scan["candidate_pairs"], 45)
        self.assertEqual(scan["shown"], 40)
        self.assertEqual(len(scan["pairs"]), 40)

    def test_similarity_refuses_oversized_scan(self):
        with patch.object(self.store, "diagram", return_value={"nodes": [{}] * 301}):
            self.assertRaisesRegex(ValueError, "300", learning.similar, self.store, 0)


if __name__ == "__main__":
    unittest.main()
