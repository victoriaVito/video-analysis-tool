import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

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

    def post(self, payload, origin=True, route="/api/annotation", token=None):
        headers = {"Content-Type": "application/json", "X-Review-Token": token or self.store.token}
        if origin:
            headers["Origin"] = self.base
        return urlopen(Request(self.base + route, json.dumps(payload).encode(), headers=headers))

    def label(self, **overrides):
        return {"video": 0, "frame": 3, "label": "new_state", "note": "Fixture only",
                "revision": self.store.state()["revision"], **overrides}

    def group(self, **overrides):
        return {"video": 0, "action": "create", "type": "animation", "frames": [8, 2],
                "name": "", "revision": self.store.state()["revision"], **overrides}

    def category(self, **overrides):
        payload = {"video": 0, "category_id": "saga_map", "target_type": "frame",
                   "frame": 2, "assigned": True, "revision": self.store.state()["revision"]}
        if overrides.get("target_type") == "group":
            payload.pop("frame")
        return {**payload, **overrides}

    def cleanup_preview(self, **overrides):
        payload = {"action": "preview", "video": 0, "revision": self.store.read()[1], **overrides}
        with self.post(payload, route="/api/cleanup") as response:
            return json.load(response)

    def cleanup_apply(self, preview, **overrides):
        return self.post({"action": "apply", "video": preview["video"], "revision": preview["revision"],
                          "preview_token": preview["preview_token"], "confirm": True, **overrides},
                         route="/api/cleanup")

    def test_cleanup_union_exact_counts_preservation_restoration_and_no_side_effects(self):
        self.post(self.label(frame=2)).close()
        self.post(self.label(frame=3)).close()
        self.post(self.label(frame=3, action="trash")).close()
        self.post(self.label(frame=9, action="trash")).close()
        self.post(self.group(frames=[2, 4, 8], selected_unit=True), route="/api/frame-group").close()
        self.post(self.group(frames=[5, 6]), route="/api/frame-group").close()
        self.post(self.category(frame=5), route="/api/category").close()
        before, _ = self.store.read()
        before["unrelated"] = {"video": "other", "history": ["preserve"]}
        before.setdefault("curated_windows", []).append({
            "video": "other", "first_frame": 1, "last_frame": 2,
            "keep_frames": [1], "recorded_at": "2000-01-01",
        })
        self.dataset.write_text(json.dumps(before))
        original = self.dataset.read_bytes()
        images = {n: self.store.frame(0, n).read_bytes() for n in range(1, 11)}
        preview = self.cleanup_preview()
        self.assertEqual(preview["selected_count"], 3, "Union deduplicates Keep+unit")
        self.assertEqual(preview["eligible_count"], 5, "Metadata/category do not approve")
        self.assertEqual(preview["existing_trash_count"], 2)
        self.assertEqual(self.dataset.read_bytes(), original, "Preview/cancel never writes dataset")
        with patch.object(review.learning_module, "run", side_effect=AssertionError("No learning")), \
             patch.object(self.store, "context", side_effect=AssertionError("No per-frame cases")):
            with self.cleanup_apply(preview) as response:
                result = json.load(response)
        self.assertEqual(result["cleanup_result"]["moved_count"], 5)
        self.assertEqual({x["frame"] for x in result["trashed"] if x["video"] == "fixture-recording"},
                         {1, 3, 5, 6, 7, 9, 10})
        after, _ = self.store.read()
        self.assertEqual({k: v for k, v in after.items() if k != "curated_windows"},
                         {k: v for k, v in before.items() if k != "curated_windows"})
        self.assertEqual(after["curated_windows"][:-1], before["curated_windows"])
        self.assertEqual(after["curated_windows"][-1]["keep_frames"], [2, 4, 8])
        self.assertEqual(images, {n: self.store.frame(0, n).read_bytes() for n in range(1, 11)})
        self.assertFalse((self.root / "data/soda-review-learning").exists())
        self.post(self.label(frame=5, action="restore")).close()
        self.assertNotIn({"video": "fixture-recording", "frame": 5}, self.store.state()["trashed"])
        self.assertEqual(self.store.read()[0]["frame_groups"], before["frame_groups"])
        self.assertEqual(self.store.read()[0]["category_assignments"], before["category_assignments"])
        # Re-preview sees restored non-selection as eligible, no hidden idempotency assumption.
        self.assertEqual(self.cleanup_preview()["eligible_count"], 1)

    def test_cleanup_confirmation_stale_token_origin_foreign_retained_guards(self):
        preview = self.cleanup_preview()
        original = self.dataset.read_bytes()
        for overrides, code in [({"confirm": False}, 400), ({"confirm": 1}, 400),
                                ({"preview_token": "foreign"}, 409), ({"video": 99}, 400),
                                ({"video": True}, 400), ({"revision": "stale"}, 409)]:
            with self.subTest(overrides=overrides), self.assertRaises(HTTPError) as caught:
                self.cleanup_apply(preview, **overrides)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(self.dataset.read_bytes(), original)
        payload = {"action": "preview", "video": 0, "revision": preview["revision"]}
        for headers in ({"origin": False}, {"token": "wrong"}):
            with self.assertRaises(HTTPError) as caught:
                self.post(payload, route="/api/cleanup", **headers)
            self.assertEqual(caught.exception.code, 403)
        # Valid other-recording index still cannot use this recording's proof.
        self.store.videos.append({**self.store.videos[0], "id": 1, "name": "other-recording"})
        with self.assertRaises(HTTPError) as caught:
            self.cleanup_apply(preview, video=1)
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(self.dataset.read_bytes(), original)
        self.store.videos.pop()
        self.post(self.label(frame=2)).close()
        changed = self.dataset.read_bytes()
        with self.assertRaises(HTTPError) as caught:
            self.cleanup_apply(preview)
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(self.dataset.read_bytes(), changed)
        self.store.videos[0].update(retention_only=True, retained_frames=[2, 8])
        for action in ("preview", "apply"):
            with self.assertRaises(HTTPError) as caught:
                self.post({**payload, "action": action, "revision": self.store.read()[1]},
                          route="/api/cleanup")
            self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.dataset.read_bytes(), changed)

    def test_cleanup_zero_selected_zero_eligible_compact_and_unknown_write_errors(self):
        # No Keep means all untrashed frames are eligible; explicit preview and confirmation still required.
        preview = self.cleanup_preview()
        self.assertEqual((preview["selected_count"], preview["eligible_count"]), (0, 10))
        original = self.dataset.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("Synthetic atomic replace failure")):
            with self.assertRaises(HTTPError) as caught:
                self.cleanup_apply(preview)
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(self.dataset.read_bytes(), original)
        with self.cleanup_apply(preview) as response:
            self.assertEqual(json.load(response)["cleanup_result"]["moved_count"], 10)
        after = self.dataset.read_bytes()
        self.assertEqual(len(self.store.read()[0]["cases"]), 0)
        self.assertEqual(len(self.store.read()[0]["curated_windows"]), 1)
        empty = self.cleanup_preview()
        self.assertEqual(empty["eligible_count"], 0)
        with self.cleanup_apply(empty) as response:
            self.assertEqual(json.load(response)["cleanup_result"]["moved_count"], 0)
        self.assertEqual(self.dataset.read_bytes(), after, "Zero eligible is a true no-op")
        self.post(self.label(frame=1, action="restore")).close()
        self.assertEqual(self.cleanup_preview()["eligible_count"], 1)

    def test_cleanup_large_recording_is_one_compact_write(self):
        self.store.videos[0].update(frame_count=114334, times=list(range(114334)))
        preview = self.cleanup_preview()
        self.assertEqual(preview["eligible_count"], 114334)
        with patch.object(self.store, "write_feedback", wraps=self.store.write_feedback) as write:
            with self.cleanup_apply(preview) as response:
                self.assertEqual(json.load(response)["cleanup_result"]["moved_count"], 114334)
            self.assertEqual(write.call_count, 1)
        self.assertLess(self.dataset.stat().st_size, 2000)
        self.assertEqual(self.store.read()[0]["cases"], [])

    def test_cleanup_concurrent_confirmation_only_writes_once_and_failed_readback_is_explicit(self):
        preview = self.cleanup_preview()
        outcomes = []
        def apply():
            try:
                with self.cleanup_apply(preview) as response:
                    outcomes.append(response.status)
            except HTTPError as error:
                outcomes.append(error.code)
        threads = [threading.Thread(target=apply) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(outcomes), [200, 409])
        self.assertEqual(len(self.store.read()[0]["curated_windows"]), 1)
        self.post(self.label(frame=1, action="restore")).close()
        preview = self.cleanup_preview()
        before = self.dataset.read_bytes()
        original_read = self.store.read
        def uncertain_read():
            # Simulate the readback failing after the atomic replacement, not
            # a pre-write rejection. This must not be reported as success/retried.
            if self.dataset.read_bytes() != before:
                raise OSError("Synthetic readback unavailable")
            return original_read()
        with patch.object(self.store, "read", side_effect=uncertain_read):
            with self.assertRaises(HTTPError) as caught:
                self.cleanup_apply(preview)
        self.assertEqual(caught.exception.code, 503)
        self.assertNotEqual(self.dataset.read_bytes(), before)
        self.assertEqual(len(self.store.read()[0]["curated_windows"]), 2)

    def test_cleanup_latest_individual_point_overrides_legacy_keep(self):
        self.post(self.label(frame=2)).close()
        data, _ = self.store.read()
        duplicate = json.loads(json.dumps(data["cases"][0]))
        duplicate["id"] = "legacy-second-case"
        duplicate["label_history"][-1].update(label="uncertain", recorded_at="2999-01-01")
        data["cases"].append(duplicate)
        self.dataset.write_text(json.dumps(data))
        preview = self.cleanup_preview()
        self.assertEqual((preview["selected_count"], preview["eligible_count"]), (0, 10))
        original_cases = data["cases"]
        with self.cleanup_apply(preview):
            pass
        self.assertEqual(self.store.read()[0]["cases"], original_cases)

    def test_similarity_above_300_compares_all_pairs_including_cross_blocks_read_only(self):
        # Match only the first and last frame, spanning both the old cap and blocks.
        original = self.dataset.read_bytes()
        source = self.archive / "fixture-recording"
        Image.new("RGB", (30, 60), (0, 0, 0)).save(source / "frame_000001.jpg")
        Image.new("RGB", (30, 60), (0, 0, 0)).save(source / "frame_000413.jpg")
        nodes = [{"frame": n, "time": n, "image": f"/fixture/{n}"} for n in range(1, 414)]
        for n in range(2, 413):
            # Different source dimensions exclude all other pairs cheaply and faithfully.
            Image.new("RGB", (n + 30, 1), (255, 255, 255)).save(source / f"frame_{n:06d}.jpg")
        self.store.videos[0].update(frame_count=413, times=list(range(413)))
        with patch.object(self.store, "diagram", return_value={"nodes": nodes, "revision": self.store.read()[1]}):
            with self.assertRaisesRegex(ValueError, "up to 300"):
                review.learning_module.similar(self.store, 0)
            with urlopen(self.base + "/api/learning/similar?video=0") as response:
                result = json.load(response)
        self.assertEqual(result["compared_pairs"], 413 * 412 // 2)
        self.assertEqual(result["selected_count"], 413)
        self.assertEqual(len(result["hashes"]), 413)
        self.assertEqual(result["candidate_pairs"], 1)
        self.assertEqual([result["pairs"][0][side]["frame"] for side in ("left", "right")], [1, 413])
        self.assertEqual(self.dataset.read_bytes(), original)

    def test_similarity_top40_matches_existing_method_and_reports_empty_limit_stale(self):
        for n in range(1, 11):
            Image.new("RGB", (30, 60), (0, 0, 0)).save(self.store.frame(0, n))
            self.post(self.label(frame=n)).close()
        old = review.learning_module.similar(self.store, 0)
        new = self.store.similar_frames(0)
        for key in ("pairs", "hashes", "shown", "candidate_pairs", "revision"):
            self.assertEqual(new[key], old[key], key)
        self.assertEqual(new["candidate_pairs"], 45)
        original = self.dataset.read_bytes()
        with patch.object(self.store, "diagram", return_value={"nodes": [], "revision": self.store.read()[1]}):
            self.assertEqual(self.store.similar_frames(0)["compared_pairs"], 0)
        with patch.object(self.store, "diagram", return_value={"nodes": [{}] * 1001, "revision": "any"}):
            with self.assertRaisesRegex(ValueError, "no frames were sampled"):
                self.store.similar_frames(0)
        with patch.object(self.store, "diagram", return_value={"nodes": [], "revision": "stale"}):
            with self.assertRaises(HTTPError) as caught:
                urlopen(self.base + "/api/learning/similar?video=0")
            self.assertEqual(caught.exception.code, 409)
        self.assertEqual(self.dataset.read_bytes(), original)

    def test_category_legacy_independent_assign_clear_export_and_originals(self):
        original = self.dataset.read_bytes()
        state = self.store.state()
        self.assertEqual(state["categories"], [{"id": "saga_map", "name": "Saga map"}])
        self.assertEqual(state["category_assignments"], [])
        with urlopen(self.base + "/api/export") as response:
            self.assertNotIn("category_assignments", json.load(response)["dataset"])
        self.post(self.category(assigned=False), route="/api/category").close()
        self.assertEqual(self.dataset.read_bytes(), original, "Legacy read/export/no-op never migrates")
        self.post(self.label()).close()
        self.post(self.label(action="trash")).close()
        self.post(self.group(frames=[2, 8]), route="/api/frame-group").close()
        self.post(self.group(frames=[4, 9], selected_unit=True), route="/api/frame-group").close()
        before, _ = self.store.read()
        originals = {n: self.store.frame(0, n).read_bytes() for n in range(1, 11)}
        group_ids = [g["id"] for g in before["frame_groups"]]
        for frame in (2, 3, 10):  # Exact, nonmember and trashed targets remain metadata-only.
            self.post(self.category(frame=frame), route="/api/category").close()
        for group_id in group_ids:
            self.post(self.category(target_type="group", group_id=group_id), route="/api/category").close()
        after, _ = self.store.read()
        self.assertEqual({k: v for k, v in after.items()
                          if k not in ("category_assignments", "category_history")}, before)
        self.assertEqual(len(after["category_assignments"]), 5)
        self.assertEqual(len(after["category_history"]), 5)
        self.assertEqual({item["frame"] for item in after["category_assignments"]
                          if item["target_type"] == "frame"}, {2, 3, 10})
        for item in after["category_assignments"]:
            self.assertEqual(item["reviewer"], "Synthetic test")
            self.assertEqual(item["method"], "explicit_dashboard_category")
            self.assertEqual(item["video"], "fixture-recording")
            self.assertTrue(item["recorded_at"])
        unchanged = self.dataset.read_bytes()
        self.post(self.category(frame=2), route="/api/category").close()
        self.assertEqual(self.dataset.read_bytes(), unchanged, "Repeated assignment is a no-op")
        reloaded = review.Store(self.archive, self.dataset, self.root / "no-preview")
        self.assertEqual(reloaded.state()["category_assignments"], after["category_assignments"])
        with urlopen(self.base + "/api/export") as response:
            self.assertEqual(json.load(response)["dataset"], after)
        self.post(self.category(target_type="group", group_id=group_ids[0], assigned=False),
                  route="/api/category").close()
        self.post(self.category(frame=2, assigned=False), route="/api/category").close()
        remaining, _ = self.store.read()
        self.assertEqual(len(remaining["category_assignments"]), 3)
        self.assertEqual([item["assigned"] for item in remaining["category_history"][-2:]], [False, False])
        self.assertEqual(remaining["cases"], before["cases"])
        self.assertEqual(remaining["frame_groups"], before["frame_groups"])
        self.assertEqual(originals, {n: self.store.frame(0, n).read_bytes() for n in range(1, 11)})

    def test_category_membership_merge_settings_and_group_cleanup(self):
        self.post(self.group(frames=[2, 3, 8]), route="/api/frame-group").close()
        self.post(self.group(frames=[4, 9], selected_unit=True), route="/api/frame-group").close()
        source, destination = self.store.state()["frame_groups"]
        for group in (source, destination):
            self.post(self.category(target_type="group", group_id=group["id"]), route="/api/category").close()
        self.post(self.category(frame=2), route="/api/category").close()
        frame_category = next(item for item in self.store.state()["category_assignments"]
                              if item["target_type"] == "frame")
        self.post(self.group(action="configure", id=source["id"], display_number=7, color="#123ABC"),
                  route="/api/frame-group").close()
        self.post(self.group(action="merge", id=source["id"], frame=10), route="/api/frame-group").close()
        self.post(self.group(action="membership", source_id=source["id"], frame=2,
                             member_action="move", destination_id=destination["id"]),
                  route="/api/frame-group").close()
        self.assertEqual(len(self.store.state()["category_assignments"]), 3)
        self.post(self.group(action="membership", source_id=source["id"], frame=3,
                             member_action="remove"), route="/api/frame-group").close()
        self.post(self.group(action="membership", source_id=source["id"], frame=8,
                             member_action="remove", dissolve_ack=True), route="/api/frame-group").close()
        assignments = self.store.state()["category_assignments"]
        self.assertEqual(len(assignments), 2)
        self.assertIn(frame_category, assignments, "Moves and removals preserve direct category provenance")
        self.assertFalse(any(item.get("frame") == 10 for item in assignments), "No inheritance on merge")
        self.post(self.group(action="ungroup", id=destination["id"]), route="/api/frame-group").close()
        self.assertEqual(self.store.state()["category_assignments"], [frame_category])
        data, _ = self.store.read()
        self.assertEqual([event["group_id"] for event in data["category_history"]
                          if event.get("reason") == "group_removed"], [source["id"], destination["id"]])
        self.assertEqual(data["cases"], [])

    def test_category_invalid_stale_origin_token_and_foreign_target_no_write(self):
        self.post(self.group(), route="/api/frame-group").close()
        group = self.store.state()["frame_groups"][0]
        data, _ = self.store.read()
        data["frame_groups"].append({**group, "id": "foreign", "video": "other-recording"})
        data["category_assignments"] = [{
            "category_id": "saga_map", "video": "other-recording", "target_type": "frame", "frame": 2,
        }]
        self.dataset.write_text(json.dumps(data))
        invalid = [
            {"category_id": "unknown"}, {"category_id": True}, {"target_type": "interval"},
            {"assigned": 1}, {"assigned": "true"}, {"video": True}, {"video": -1},
            {"video": "fixture-recording"}, {"frame": True}, {"frame": "2"}, {"frame": 0},
            {"frame": 11}, {"group_id": group["id"]},
            {"target_type": "group", "group_id": group["id"], "frame": 2},
            {"target_type": "group", "group_id": "foreign"},
            {"target_type": "group", "group_id": "missing"},
            {"target_type": "group", "group_id": False},
            {"revision": "stale"},
        ]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                before = self.dataset.read_bytes()
                with self.assertRaises(HTTPError) as error:
                    self.post(self.category(**overrides), route="/api/category")
                self.assertEqual(error.exception.code, 409 if "revision" in overrides else 400)
                self.assertTrue(json.loads(error.exception.read())["error"])
                error.exception.close()
                self.assertEqual(self.dataset.read_bytes(), before)
        for options in ({"origin": False}, {"token": "invalid"}):
            before = self.dataset.read_bytes()
            with self.assertRaises(HTTPError) as error:
                self.post(self.category(), route="/api/category", **options)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            self.assertEqual(self.dataset.read_bytes(), before)
        missing = self.store.frame(0, 7)
        missing.rename(missing.with_suffix(".missing"))
        with self.assertRaises(HTTPError) as error:
            self.post(self.category(frame=7), route="/api/category")
        error.exception.close()
        self.assertEqual(self.dataset.read_bytes(), before)
        stale = self.category(frame=3)
        self.post(self.category(), route="/api/category").close()
        before = self.dataset.read_bytes()
        with self.assertRaises(HTTPError) as error:
            self.post(stale, route="/api/category")
        self.assertEqual(error.exception.code, 409)
        error.exception.close()
        self.assertEqual(self.dataset.read_bytes(), before)
        self.assertIn(data["category_assignments"][0], self.store.state()["category_assignments"])

    def test_category_retained_only_and_invalid_metadata_guards(self):
        manifest_path = self.archive / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["videos"][0]["retained_frames"] = [2, 8]
        manifest_path.write_text(json.dumps(manifest))
        self.store.videos[0].update(retention_only=True, retained_frames=[2, 8])
        self.post(self.category(), route="/api/category").close()
        with self.assertRaises(HTTPError) as error:
            self.post(self.category(frame=3), route="/api/category")
        error.exception.close()
        self.post(self.group(), route="/api/frame-group").close()
        group = self.store.state()["frame_groups"][0]
        self.post(self.category(target_type="group", group_id=group["id"]), route="/api/category").close()
        self.post(self.category(assigned=False), route="/api/category").close()
        self.assertEqual(self.store.read()[0]["cases"], [])
        for field in ("category_assignments", "category_history"):
            data, _ = self.store.read()
            data[field] = {}
            self.dataset.write_text(json.dumps(data))
            before = self.dataset.read_bytes()
            with self.assertRaises(HTTPError) as error:
                self.post(self.category(), route="/api/category")
            error.exception.close()
            self.assertEqual(self.dataset.read_bytes(), before)
            data[field] = []
            self.dataset.write_text(json.dumps(data))

    def test_category_and_group_writes_share_file_lock_and_revision(self):
        self.post(self.group(), route="/api/frame-group").close()
        group = self.store.state()["frame_groups"][0]
        second_store = review.Store(self.archive, self.dataset, self.root / "no-preview")
        barrier = threading.Barrier(2)
        results = []
        category = self.category()
        configure = self.group(action="configure", id=group["id"], display_number=7, color="#123ABC")

        def save(operation, payload):
            barrier.wait(timeout=5)
            try:
                operation(payload)
                results.append("saved")
            except review.ConflictError:
                results.append("conflict")

        threads = [
            threading.Thread(target=save, args=(self.store.save_category, category)),
            threading.Thread(target=save, args=(second_store.save_frame_group, configure)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, ["saved", "conflict"])
        data, _ = self.store.read()
        has_category = bool(data.get("category_assignments"))
        configured = data["frame_groups"][0]["display_number"] == 7
        self.assertNotEqual(has_category, configured, "Only one revision-guarded operation can win")
        self.assertEqual(data["cases"], [])

    def test_category_failed_atomic_replacement_leaves_original_dataset(self):
        before = self.dataset.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("Synthetic disk failure")):
            with self.assertRaises(HTTPError) as error:
                self.post(self.category(), route="/api/category")
        self.assertEqual(error.exception.code, 503)
        self.assertIn("not confirmed saved", error.exception.read().decode())
        error.exception.close()
        self.assertEqual(self.dataset.read_bytes(), before)
        self.assertEqual(self.store.state()["category_assignments"], [])

    def test_category_readback_failure_reports_uncertain_saved_state(self):
        original_read = self.store.read

        def fail_readback():
            data, revision = original_read()
            if data.get("category_assignments"):
                raise OSError("Synthetic readback failure after replacement")
            return data, revision

        with patch.object(self.store, "read", side_effect=fail_readback):
            with self.assertRaises(HTTPError) as error:
                self.post(self.category(), route="/api/category")
        self.assertEqual(error.exception.code, 503)
        self.assertIn("not confirmed saved", error.exception.read().decode())
        error.exception.close()
        # A failed response is not proof of no write: reload before a manual retry.
        self.assertEqual(len(self.store.state()["category_assignments"]), 1)
        before = self.dataset.read_bytes()
        self.post(self.category(), route="/api/category").close()
        self.assertEqual(self.dataset.read_bytes(), before, "Fresh identical Save is an explicit no-op")

    def test_exact_groups_persist_export_and_ungroup_without_changing_decisions(self):
        self.post(self.label()).close()
        self.post(self.label(label="same_state")).close()
        self.post(self.label(action="trash")).close()
        data, _ = self.store.read()
        data.update({"status": "preserve-status", "intervals": [{"id": "untouched"}],
                     "other_metadata": {"nested": ["preserve"]}})
        data["split_by_video"]["other-recording"] = "holdout"
        data["preselections"] = [{"video": "other-recording", "frames": [{"source_frame": 5}]}]
        data["cases"].append({
            "id": "other-recording-decision", "video": "other-recording",
            "context": [{"role": "target", "source_frame": 5, "source_time": .4}],
            "label_history": [{"label": "uncertain", "recorded_at": "2026-01-01"}],
            "trash_history": [{"trashed": True, "recorded_at": "2026-01-02"}],
        })
        self.dataset.write_text(json.dumps(data))
        before, _ = self.store.read()
        originals = {n: self.store.frame(0, n).read_bytes() for n in range(1, 11)}
        self.assertEqual(self.store.state()["frame_groups"], [])
        with self.post(self.group(name="  Reveal <b>two</b>  "), route="/api/frame-group") as response:
            state = json.load(response)
        group = state["frame_groups"][0]
        self.assertEqual(group["frames"], [2, 8])
        self.assertEqual(group["name"], "Reveal <b>two</b>")
        self.assertEqual(group["type"], "animation")
        self.assertEqual(group["video"], "fixture-recording")
        self.assertEqual(group["method"], "explicit_dashboard_group")
        self.assertEqual(state["revision"], review.sha(self.dataset.read_bytes()))
        after, _ = self.store.read()
        self.assertEqual({k: v for k, v in after.items() if k != "frame_groups"}, before)
        restored = review.Store(self.archive, self.dataset, self.root / "none")
        self.assertEqual(restored.state()["frame_groups"], [group])
        with urlopen(self.base + "/api/export") as response:
            self.assertEqual(json.load(response)["dataset"]["frame_groups"], [group])
        self.assertEqual(self.store.diagram(0)["nodes"], [], "Grouping never approves frames")
        self.post(self.group(action="ungroup", id=group["id"]), route="/api/frame-group").close()
        self.assertEqual(self.store.state()["frame_groups"], [])
        ungrouped, _ = self.store.read()
        self.assertEqual({k: v for k, v in ungrouped.items() if k != "frame_groups"}, before)
        for n, content in originals.items():
            self.assertEqual(self.store.frame(0, n).read_bytes(), content)

    def test_group_validation_security_and_stale_writes_are_atomic(self):
        before = self.dataset.read_bytes()
        invalid = [
            {"frames": []}, {"frames": [2]}, {"frames": [2, 2]},
            {"frames": [0, 3]}, {"frames": [2, 11]}, {"frames": [True, 3]},
            {"frames": [2.0, 3]}, {"frames": ["2", 3]}, {"frames": "2,3"},
            {"frames": [{"video": 1, "frame": 2}, 3]}, {"video": 1},
            {"type": "invented"}, {"type": ["animation"]}, {"name": None},
            {"name": "x" * 121}, {"name": "new\nline"}, {"name": "\0"}, {"name": "\x85"},
            {"action": "replace"}, {"action": "ungroup", "id": "missing"},
            {"action": "ungroup", "id": []},
            {"selected_unit": "true"}, {"selected_unit": 1}, {"selected_unit": None},
        ]
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(HTTPError) as error:
                    self.post(self.group(**overrides), route="/api/frame-group")
                self.assertEqual(error.exception.code, 400)
                self.assertEqual(self.dataset.read_bytes(), before)
        for options in ({"origin": False}, {"token": "wrong-token"}):
            with self.assertRaises(HTTPError) as error:
                self.post(self.group(), route="/api/frame-group", **options)
            self.assertEqual(error.exception.code, 403)
            self.assertEqual(self.dataset.read_bytes(), before)
        stale = self.group()
        self.post(self.label()).close()
        before = self.dataset.read_bytes()
        with self.assertRaises(HTTPError) as error:
            self.post(stale, route="/api/frame-group")
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.dataset.read_bytes(), before)
        old_annotation = self.label()
        self.post(self.group(type="same_scene"), route="/api/frame-group").close()
        before = self.dataset.read_bytes()
        with self.assertRaises(HTTPError) as error:
            self.post(old_annotation)
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.dataset.read_bytes(), before)
        group = self.store.state()["frame_groups"][0]
        self.assertEqual(group["type"], "same_scene")
        with self.assertRaises(HTTPError) as error:
            self.post(self.group(action="ungroup", id=group["id"], revision=stale["revision"]),
                      route="/api/frame-group")
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.dataset.read_bytes(), before)

    def test_selected_units_preserve_legacy_overlap_labels_history_sources_and_diagrams(self):
        self.post(self.label()).close()
        self.post(self.label(label="same_state")).close()
        self.post(self.label(frame=2)).close()
        self.post(self.label(frame=5, end_frame=7, label="animation")).close()
        self.post(self.group(frames=[2, 3, 8]), route="/api/frame-group").close()
        legacy = self.store.state()["frame_groups"][0]
        self.assertNotIn("selected_unit", legacy)
        before, _ = self.store.read()
        sources = {i: self.store.frame(0, i).read_bytes() for i in range(1, 11)}
        diagram = self.store.diagram(0)
        payload = self.group(frames=[8, 2], type="same_scene", selected_unit=True)
        with self.post(payload, route="/api/frame-group") as response:
            result = json.load(response)
        unit = result["frame_groups"][-1]
        self.assertIs(unit["selected_unit"], True)
        self.assertEqual(unit["frames"], [2, 8], "Exact subset, never the gap or all Keep points")
        restored = review.Store(self.archive, self.dataset, self.root / "no-preview")
        self.assertEqual(restored.state()["frame_groups"], [legacy, unit])
        after, _ = self.store.read()
        self.assertEqual({k: v for k, v in after.items() if k != "frame_groups"},
                         {k: v for k, v in before.items() if k != "frame_groups"})
        self.assertEqual(self.store.diagram(0)["nodes"], diagram["nodes"])
        self.assertEqual(self.store.diagram(0)["edges"], diagram["edges"])
        self.assertEqual([node["frame"] for node in diagram["nodes"]], [2])
        with urlopen(self.base + "/api/export") as response:
            self.assertEqual(json.load(response)["dataset"]["frame_groups"], [legacy, unit])
        unchanged = self.dataset.read_bytes()
        for overrides in ({"frames": [2, 9], "selected_unit": True},
                          {"revision": payload["revision"], "selected_unit": True}):
            with self.assertRaises(HTTPError) as error:
                self.post(self.group(**overrides), route="/api/frame-group")
            self.assertIn(error.exception.code, (400, 409))
            error.exception.close()
            self.assertEqual(self.dataset.read_bytes(), unchanged)
        self.post(self.label(frame=8, action="trash")).close()
        self.assertEqual(self.store.state()["frame_groups"], [legacy, unit])
        self.post(self.group(action="ungroup", id=unit["id"]), route="/api/frame-group").close()
        self.assertEqual(self.store.state()["frame_groups"], [legacy])
        unchanged = self.dataset.read_bytes()
        with self.assertRaises(HTTPError) as error:
            self.post(self.group(selected_unit=True), route="/api/frame-group")
        self.assertEqual(error.exception.code, 400, "Cannot silently group a trashed member")
        error.exception.close()
        self.assertEqual(self.dataset.read_bytes(), unchanged)
        self.post(self.label(frame=8, action="restore")).close()
        self.assertEqual(self.store.state()["labels"], result["labels"])
        self.assertEqual(self.store.state()["intervals"], result["intervals"])
        for i, original in sources.items():
            self.assertEqual(self.store.frame(0, i).read_bytes(), original)

    def test_group_membership_survives_later_labels_trash_and_intervals(self):
        self.post(self.group(), route="/api/frame-group").close()
        groups = self.store.state()["frame_groups"]
        self.post(self.label(frame=2)).close()
        self.post(self.label(frame=2, action="trash")).close()
        self.assertEqual(self.store.state()["frame_groups"], groups)
        self.post(self.label(frame=2, action="restore")).close()
        self.post(self.label(frame=4, end_frame=6, label="animation")).close()
        self.assertEqual(self.store.state()["frame_groups"], groups)
        self.assertEqual([node["frame"] for node in self.store.diagram(0)["nodes"]], [2])
        self.assertEqual(self.store.diagram(0)["edges"], [], "The group adds no diagram relationships")

    def test_groups_are_recording_scoped_and_allow_retained_metadata_only(self):
        # A second recording with the same frame numbers is still a different source.
        manifest_path = self.archive / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        second = self.archive / "other-recording"
        second.mkdir()
        (second / "frame_timestamps.csv").write_bytes(
            (self.archive / "fixture-recording/frame_timestamps.csv").read_bytes())
        manifest["videos"].append({**manifest["videos"][0], "output_directory": "other-recording"})
        manifest["videos"][0]["retained_frames"] = [2, 8]
        manifest_path.write_text(json.dumps(manifest))
        self.store = review.Store(self.archive, self.dataset, self.root / "none")
        self.server.store = self.store
        self.post(self.group(), route="/api/frame-group").close()
        group = self.store.state()["frame_groups"][0]
        before = self.dataset.read_bytes()
        for overrides in ({"video": 1, "action": "ungroup", "id": group["id"]},
                          {"frames": [2, 3]}, {"video": 1}):
            with self.assertRaises(HTTPError) as error:
                self.post(self.group(**overrides), route="/api/frame-group")
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(self.dataset.read_bytes(), before)
        self.post(self.group(action="ungroup", id=group["id"]), route="/api/frame-group").close()
        self.assertEqual(self.store.state()["labels"], [])
        self.assertEqual(self.store.state()["trashed"], [])

    def test_legacy_group_numbers_are_read_only_deterministic_and_exported(self):
        data, _ = self.store.read()
        data["frame_groups"] = [
            {"id": "legacy-a", "video": "fixture-recording", "frames": [2, 8],
             "type": "animation", "name": "first"},
            {"id": "legacy-b", "video": "fixture-recording", "frames": [3, 9],
             "type": "same_scene", "name": "second"},
            {"id": "legacy-other", "video": "other-recording", "frames": [2, 8],
             "type": "same_scene", "name": "other"},
        ]
        data["split_by_video"]["other-recording"] = "holdout"
        self.dataset.write_text(json.dumps(data))
        before = self.dataset.read_bytes()
        first = self.store.state()["frame_groups"]
        second = self.store.state()["frame_groups"]
        self.assertEqual([group["display_number"] for group in first], [1, 2, 1])
        self.assertEqual(first, second, "Legacy display numbers are deterministic")
        self.assertNotIn("display_number", json.loads(before)["frame_groups"][0])
        with urlopen(self.base + "/api/export") as response:
            exported = json.load(response)["dataset"]["frame_groups"]
        self.assertEqual(exported, first)
        self.assertEqual(self.dataset.read_bytes(), before, "Read and export never migrate legacy data")

    def test_merge_adds_only_exact_frame_and_preserves_group_decisions_and_provenance(self):
        self.post(self.label(frame=3)).close()
        self.post(self.group(frames=[8, 2], type="animation", name="Original"),
                  route="/api/frame-group").close()
        data, _ = self.store.read()
        group = data["frame_groups"][0]
        group.update({"provenance": {"review": "keep"}, "custom_metadata": ["preserved"]})
        self.dataset.write_text(json.dumps(data))
        self.store.state()  # Refresh revision after fixture metadata setup.
        original_labels = self.store.state()["labels"]
        source_bytes = {frame: self.store.frame(0, frame).read_bytes() for frame in range(1, 11)}
        before = json.loads(self.dataset.read_text())
        payload = {"video": 0, "action": "merge", "id": group["id"], "frame": 5,
                   "revision": self.store.state()["revision"]}
        with self.post(payload, route="/api/frame-group") as response:
            state = json.load(response)
        merged = state["frame_groups"][0]
        self.assertEqual(merged["frames"], [2, 5, 8])
        self.assertEqual(merged["id"], group["id"])
        self.assertEqual(merged["type"], "animation")
        self.assertEqual(merged["name"], "Original")
        self.assertEqual(merged["provenance"], {"review": "keep"})
        self.assertEqual(merged["custom_metadata"], ["preserved"])
        self.assertEqual(state["labels"], original_labels)
        after = json.loads(self.dataset.read_text())
        self.assertEqual({key: value for key, value in after.items() if key != "frame_groups"},
                         {key: value for key, value in before.items() if key != "frame_groups"})
        self.assertEqual(after["frame_groups"][0]["frames"], [2, 5, 8])
        with self.post(payload | {"revision": state["revision"]}, route="/api/frame-group") as response:
            duplicate = json.load(response)
        self.assertIn("already in Group", duplicate["operation_message"])
        self.assertEqual(duplicate["revision"], state["revision"])
        self.assertEqual(self.dataset.read_bytes(), json.dumps(after, indent=2).encode())
        before_stale = self.dataset.read_bytes()
        with self.assertRaises(HTTPError) as error:
            self.post(payload | {"frame": 7}, route="/api/frame-group")
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.dataset.read_bytes(), before_stale)
        for frame, content in source_bytes.items():
            self.assertEqual(self.store.frame(0, frame).read_bytes(), content)

    def test_group_membership_move_and_remove_preserve_other_metadata_and_reload(self):
        self.post(self.label(frame=2)).close()
        self.post(self.label(frame=7, action="trash")).close()
        self.post(self.group(frames=[1, 2, 3, 8], type="animation", name="Original source"),
                  route="/api/frame-group").close()
        self.post(self.group(frames=[2, 4, 5], type="same_scene", name="Destination"),
                  route="/api/frame-group").close()
        self.post(self.group(frames=[1, 2, 9], type="same_scene", name="Legacy overlap"),
                  route="/api/frame-group").close()
        data, _ = self.store.read()
        source, destination, overlap = data["frame_groups"]
        source.update({"reviewer": "Copilot", "created_by": "proposal",
                       "provenance": {"original": "proposal"}, "custom_metadata": {"keep": True}})
        destination.update({"reviewer": "Human", "provenance": {"destination": "keep"}})
        self.dataset.write_text(json.dumps(data))
        before, _ = self.store.read()
        self.store.state()
        source_id, destination_id, overlap_id = source["id"], destination["id"], overlap["id"]
        with self.post({"video": 0, "action": "membership", "member_action": "move",
                        "source_id": source_id, "destination_id": destination_id, "frame": 1,
                        "revision": self.store.state()["revision"]},
                       route="/api/frame-group") as response:
            moved = json.load(response)
        groups = moved["frame_groups"]
        source_after = next(group for group in groups if group["id"] == source_id)
        destination_after = next(group for group in groups if group["id"] == destination_id)
        overlap_after = next(group for group in groups if group["id"] == overlap_id)
        self.assertEqual(source_after["frames"], [2, 3, 8])
        self.assertEqual(destination_after["frames"], [1, 2, 4, 5])
        self.assertEqual(overlap_after["frames"], [1, 2, 9], "Other overlapping memberships remain unchanged")
        self.assertEqual(source_after["type"], "animation")
        self.assertEqual(source_after["name"], "Original source")
        self.assertEqual(source_after["display_number"], source["display_number"])
        self.assertEqual(source_after["reviewer"], "Copilot", "Correction does not rewrite original authorship")
        self.assertEqual(source_after["created_by"], "proposal")
        self.assertEqual(source_after["provenance"], {"original": "proposal"})
        self.assertEqual(source_after["custom_metadata"], {"keep": True})
        self.assertEqual(source_after["membership_history"][-1]["reviewer"], "Synthetic test")
        correction_log = self.store.read()[0]["frame_group_membership_history"][-1]
        self.assertEqual(correction_log["source_group_id"], source_id)
        self.assertEqual(correction_log["destination_group_id"], destination_id)
        self.assertEqual(correction_log["reviewer"], "Synthetic test")
        self.assertFalse(correction_log["source_dissolved"])
        self.assertEqual(destination_after["reviewer"], "Human")
        self.assertEqual(destination_after["provenance"], {"destination": "keep"})
        self.assertEqual(moved["labels"], self.store.state()["labels"])
        self.assertEqual(moved["trashed"], [{"video": "fixture-recording", "frame": 7}])
        persisted, _ = self.store.read()
        self.assertEqual({key: value for key, value in persisted.items()
                          if key not in ("frame_groups", "frame_group_membership_history")},
                         {key: value for key, value in before.items()
                          if key not in ("frame_groups", "frame_group_membership_history")})

        # A destination that already contains the member never receives a duplicate.
        with self.post({"video": 0, "action": "membership", "member_action": "move",
                        "source_id": source_id, "destination_id": destination_id, "frame": 2,
                        "revision": moved["revision"]},
                       route="/api/frame-group") as response:
            deduplicated = json.load(response)
        correction_history = self.store.read()[0]["frame_group_membership_history"]
        source_after = next(group for group in deduplicated["frame_groups"] if group["id"] == source_id)
        destination_after = next(group for group in deduplicated["frame_groups"] if group["id"] == destination_id)
        self.assertEqual(source_after["frames"], [3, 8])
        self.assertEqual(destination_after["frames"], [1, 2, 4, 5])
        self.assertIn("already contained", deduplicated["operation_message"])
        restored = review.Store(self.archive, self.dataset, self.root / "none")
        self.assertEqual(restored.state()["frame_groups"], deduplicated["frame_groups"])
        self.assertEqual(restored.read()[0]["frame_group_membership_history"], correction_history)
        with urlopen(self.base + "/api/export") as response:
            exported = json.load(response)["dataset"]
        self.assertEqual(exported["frame_groups"], deduplicated["frame_groups"])
        self.assertEqual(exported["frame_group_membership_history"], correction_history)

    def test_small_group_membership_requires_acknowledged_dissolution_and_preserves_remainder(self):
        self.post(self.group(frames=[2, 3], type="same_scene", selected_unit=True,
                             name="Selected unit"), route="/api/frame-group").close()
        self.post(self.group(frames=[4, 5], type="animation", name="Destination"),
                  route="/api/frame-group").close()
        self.post(self.group(frames=[3, 8], type="animation", name="Other membership"),
                  route="/api/frame-group").close()
        source, destination, unrelated = self.store.state()["frame_groups"]
        before = self.dataset.read_bytes()
        move = {"video": 0, "action": "membership", "member_action": "move",
                "source_id": source["id"], "destination_id": destination["id"], "frame": 2,
                "revision": self.store.state()["revision"]}
        with self.assertRaises(HTTPError) as error:
            self.post(move, route="/api/frame-group")
        self.assertEqual(error.exception.code, 400)
        self.assertIn("Confirm dissolving", error.exception.read().decode())
        self.assertEqual(self.dataset.read_bytes(), before, "Missing acknowledgement cannot partially move a member")
        with self.assertRaises(HTTPError):
            self.post(move | {"dissolve_ack": False}, route="/api/frame-group")
        self.assertEqual(self.dataset.read_bytes(), before, "Cancellation is a no-write client decision")

        with self.post(move | {"dissolve_ack": True}, route="/api/frame-group") as response:
            result = json.load(response)
        groups = result["frame_groups"]
        self.assertFalse(any(group["id"] == source["id"] for group in groups))
        self.assertEqual(next(group for group in groups if group["id"] == destination["id"])["frames"],
                         [2, 4, 5])
        self.assertEqual(next(group for group in groups if group["id"] == unrelated["id"])["frames"],
                         [3, 8], "The one remaining source member keeps unrelated memberships")
        self.assertIn("dissolved as confirmed", result["operation_message"])
        self.assertTrue(self.store.read()[0]["frame_group_membership_history"][-1]["source_dissolved"])

        # Explicit removal of a two-member metadata group dissolves only that group.
        removable = self.group(frames=[6, 7], type="animation")
        self.post(removable, route="/api/frame-group").close()
        small = self.store.state()["frame_groups"][-1]
        with self.assertRaises(HTTPError):
            self.post({"video": 0, "action": "membership", "member_action": "remove",
                       "source_id": small["id"], "frame": 6,
                       "revision": self.store.state()["revision"]}, route="/api/frame-group")
        with self.post({"video": 0, "action": "membership", "member_action": "remove",
                        "source_id": small["id"], "frame": 6, "dissolve_ack": True,
                        "revision": self.store.state()["revision"]}, route="/api/frame-group") as response:
            removed = json.load(response)
        self.assertFalse(any(group["id"] == small["id"] for group in removed["frame_groups"]))
        self.assertEqual(self.store.read()[0]["frame_group_membership_history"][-1]["reviewer"], "Synthetic test")

    def test_group_membership_rejects_invalid_stale_trashed_foreign_and_selected_overlap(self):
        self.post(self.group(frames=[2, 3, 8]), route="/api/frame-group").close()
        source = self.store.state()["frame_groups"][0]
        self.post(self.group(frames=[4, 5], selected_unit=True), route="/api/frame-group").close()
        destination = self.store.state()["frame_groups"][1]
        self.post(self.group(frames=[2, 9], selected_unit=True), route="/api/frame-group").close()
        before = self.dataset.read_bytes()
        base = {"video": 0, "action": "membership", "member_action": "move",
                "source_id": source["id"], "destination_id": destination["id"], "frame": 2,
                "revision": self.store.state()["revision"]}
        with self.assertRaises(HTTPError) as error:
            self.post(base, route="/api/frame-group")
        self.assertIn("another selected unit", error.exception.read().decode())
        self.assertEqual(self.dataset.read_bytes(), before)
        invalid = [
            {"source_id": []}, {"source_id": "missing"}, {"destination_id": []},
            {"destination_id": source["id"]}, {"member_action": "copy"}, {"frame": True},
            {"frame": "2"}, {"frame": 10}, {"dissolve_ack": "true"}, {"member_action": "remove",
             "destination_id": destination["id"]},
        ]
        for overrides in invalid:
            payload = base | overrides
            with self.subTest(overrides=overrides), self.assertRaises(HTTPError) as error:
                self.post(payload, route="/api/frame-group")
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(self.dataset.read_bytes(), before)
        for options in ({"origin": False}, {"token": "wrong-token"}):
            with self.assertRaises(HTTPError) as error:
                self.post(base, route="/api/frame-group", **options)
            self.assertEqual(error.exception.code, 403)
            self.assertEqual(self.dataset.read_bytes(), before)
        stale = base.copy()
        self.post(self.label(frame=6)).close()
        before = self.dataset.read_bytes()
        with self.assertRaises(HTTPError) as error:
            self.post(stale, route="/api/frame-group")
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.dataset.read_bytes(), before)
        self.post(self.label(frame=2, action="trash")).close()
        before = self.dataset.read_bytes()
        with self.assertRaises(HTTPError) as error:
            self.post(base | {"revision": self.store.state()["revision"]}, route="/api/frame-group")
        self.assertEqual(error.exception.code, 400)
        self.assertIn("trashed", error.exception.read().decode())
        self.assertEqual(self.dataset.read_bytes(), before)

        manifest_path = self.archive / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        second = self.archive / "other-recording"
        second.mkdir()
        (second / "frame_timestamps.csv").write_bytes(
            (self.archive / "fixture-recording/frame_timestamps.csv").read_bytes())
        for frame in range(1, 11):
            (second / f"frame_{frame:06d}.jpg").write_bytes(
                (self.archive / "fixture-recording" / f"frame_{frame:06d}.jpg").read_bytes())
        manifest["videos"].append({**manifest["videos"][0], "output_directory": "other-recording"})
        manifest_path.write_text(json.dumps(manifest))
        self.store = review.Store(self.archive, self.dataset, self.root / "none")
        self.server.store = self.store
        before = self.dataset.read_bytes()
        with self.assertRaises(HTTPError) as error:
            self.post(base | {"video": 1, "frame": 6, "revision": self.store.state()["revision"]},
                      route="/api/frame-group")
        self.assertIn("another recording", error.exception.read().decode())
        self.assertEqual(self.dataset.read_bytes(), before)

    def test_group_numbers_are_editable_unique_positive_and_revision_guarded(self):
        self.post(self.group(frames=[2, 3], name="first"), route="/api/frame-group").close()
        first = self.store.state()["frame_groups"][0]
        self.post(self.group(frames=[4, 5], name="second"), route="/api/frame-group").close()
        second = self.store.state()["frame_groups"][1]
        self.assertEqual((first["display_number"], second["display_number"]), (1, 2))
        old_revision = self.store.state()["revision"]
        with self.post({"video": 0, "action": "renumber", "id": first["id"],
                        "display_number": 9, "revision": old_revision},
                       route="/api/frame-group") as response:
            updated = json.load(response)
        self.assertEqual([(group["id"], group["display_number"]) for group in updated["frame_groups"]],
                         [(first["id"], 9), (second["id"], 2)])
        self.assertEqual(updated["frame_groups"][0]["id"], first["id"], "Numbers never replace stable ids")
        before = self.dataset.read_bytes()
        for invalid in (0, -1, True, 1.5, "3", None):
            with self.assertRaises(HTTPError) as error:
                self.post({"video": 0, "action": "renumber", "id": first["id"],
                           "display_number": invalid, "revision": self.store.state()["revision"]},
                          route="/api/frame-group")
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(self.dataset.read_bytes(), before)
        with self.assertRaises(HTTPError) as error:
            self.post({"video": 0, "action": "renumber", "id": first["id"],
                       "display_number": 2, "revision": self.store.state()["revision"]},
                      route="/api/frame-group")
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.dataset.read_bytes(), before)
        with self.assertRaises(HTTPError) as error:
            self.post({"video": 0, "action": "renumber", "id": first["id"],
                       "display_number": 10, "revision": old_revision},
                      route="/api/frame-group")
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.dataset.read_bytes(), before)

    def test_group_settings_save_number_and_color_atomically_and_survive_reload(self):
        self.post(self.group(frames=[2, 3], name="first"), route="/api/frame-group").close()
        first = self.store.state()["frame_groups"][0]
        self.post(self.group(frames=[4, 5], name="second"), route="/api/frame-group").close()
        second = self.store.state()["frame_groups"][1]
        before_frames = list(first["frames"])
        revision = self.store.state()["revision"]
        with self.post({"video": 0, "action": "configure", "id": first["id"],
                        "display_number": 8, "color": "#a1B2c3", "revision": revision},
                       route="/api/frame-group") as response:
            configured = json.load(response)
        updated = configured["frame_groups"][0]
        self.assertEqual((updated["display_number"], updated["color"]), (8, "#A1B2C3"))
        self.assertEqual(updated["id"], first["id"])
        self.assertEqual(updated["frames"], before_frames)
        self.assertEqual(configured["frame_groups"][1]["id"], second["id"])
        restored = review.Store(self.archive, self.dataset, self.root / "none")
        self.assertEqual(restored.state()["frame_groups"][0]["color"], "#A1B2C3")

        before = self.dataset.read_bytes()
        current_revision = self.store.state()["revision"]
        invalid_colors = ["red", "#123", "#12345678", " #123456", "#12GG56",
                          "url(javascript:alert(1))", None, 123, ["#123456"]]
        for color in invalid_colors:
            with self.subTest(color=color):
                with self.assertRaises(HTTPError) as error:
                    self.post({"video": 0, "action": "configure", "id": first["id"],
                               "display_number": 9, "color": color, "revision": current_revision},
                              route="/api/frame-group")
                self.assertEqual(error.exception.code, 400)
                self.assertEqual(self.dataset.read_bytes(), before)
        with self.assertRaises(HTTPError) as error:
            self.post({"video": 0, "action": "configure", "id": first["id"],
                       "display_number": second["display_number"], "color": "#123456",
                       "revision": current_revision}, route="/api/frame-group")
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.dataset.read_bytes(), before, "A number conflict cannot partially change color")
        with self.assertRaises(HTTPError) as error:
            self.post({"video": 0, "action": "configure", "id": first["id"],
                       "display_number": 9, "color": "#123456", "revision": revision},
                      route="/api/frame-group")
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.dataset.read_bytes(), before)

    def test_invalid_legacy_group_color_is_safe_on_read_and_explicitly_repairable(self):
        data, _ = self.store.read()
        data["frame_groups"] = [{
            "id": "invalid-color", "video": "fixture-recording", "frames": [2, 3],
            "type": "same_scene", "display_number": 1, "color": "url(javascript:alert(1))",
        }]
        self.dataset.write_text(json.dumps(data))
        original = self.dataset.read_bytes()
        state = self.store.state()
        self.assertNotIn("color", state["frame_groups"][0])
        self.assertIn("invalid", state["frame_groups"][0]["_display_color_issue"])
        self.assertEqual(self.dataset.read_bytes(), original, "Reading invalid color never migrates the dataset")
        with self.post({"video": 0, "action": "configure", "id": "invalid-color",
                        "display_number": 1, "color": "#123ABC", "revision": state["revision"]},
                       route="/api/frame-group") as response:
            repaired = json.load(response)
        self.assertEqual(repaired["frame_groups"][0]["color"], "#123ABC")
        self.assertNotIn("_display_color_issue", repaired["frame_groups"][0])

    def test_duplicate_legacy_ordinals_are_reported_and_explicitly_repaired(self):
        data, _ = self.store.read()
        data["frame_groups"] = [
            {"id": "dup-a", "video": "fixture-recording", "frames": [2, 3],
             "type": "same_scene", "display_number": 1},
            {"id": "dup-b", "video": "fixture-recording", "frames": [4, 5],
             "type": "animation", "display_number": 1},
        ]
        self.dataset.write_text(json.dumps(data))
        original = self.dataset.read_bytes()
        state = self.store.state()
        self.assertEqual(len({group["display_number"] for group in state["frame_groups"]}), 2)
        self.assertTrue(all(group.get("_display_number_issue") for group in state["frame_groups"]))
        self.assertEqual(self.dataset.read_bytes(), original)
        with self.assertRaises(HTTPError) as error:
            self.post({"video": 0, "action": "merge", "id": "dup-a", "frame": 6,
                       "revision": state["revision"]}, route="/api/frame-group")
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(self.dataset.read_bytes(), original)
        with self.post({"video": 0, "action": "renumber", "id": "dup-a",
                        "display_number": 3, "revision": state["revision"]},
                       route="/api/frame-group") as response:
            repaired = json.load(response)
        self.assertEqual([g["display_number"] for g in repaired["frame_groups"]], [3, 1])
        self.assertFalse(any(g.get("_display_number_issue") for g in repaired["frame_groups"]))

    def test_invalid_legacy_group_number_is_repaired_only_on_explicit_save(self):
        data, _ = self.store.read()
        data["frame_groups"] = [
            {"id": "invalid-number", "video": "fixture-recording", "frames": [2, 3],
             "type": "same_scene", "display_number": 0},
            {"id": "valid-number", "video": "fixture-recording", "frames": [4, 5],
             "type": "animation", "display_number": 2},
        ]
        self.dataset.write_text(json.dumps(data))
        before = self.dataset.read_bytes()
        state = self.store.state()
        self.assertTrue(state["frame_groups"][0].get("_display_number_issue"))
        self.assertEqual(self.dataset.read_bytes(), before, "Read never repairs saved records")
        with self.post({"video": 0, "action": "renumber", "id": "invalid-number",
                        "display_number": 3, "revision": state["revision"]},
                       route="/api/frame-group") as response:
            repaired = json.load(response)
        self.assertEqual([group["display_number"] for group in repaired["frame_groups"]], [3, 2])
        self.assertFalse(any(group.get("_display_number_issue") for group in repaired["frame_groups"]))
        raw = json.loads(self.dataset.read_text())
        self.assertEqual(raw["frame_groups"][0]["display_number"], 3)
        self.assertEqual(raw["frame_groups"][1]["display_number"], 2)

    def test_merge_rejects_trash_foreign_destinations_and_selected_unit_overlap(self):
        self.post(self.group(frames=[2, 3], selected_unit=True), route="/api/frame-group").close()
        unit = self.store.state()["frame_groups"][0]
        self.post(self.group(frames=[6, 7], selected_unit=True), route="/api/frame-group").close()
        other_unit = self.store.state()["frame_groups"][1]
        self.post(self.group(frames=[8, 9], selected_unit=False), route="/api/frame-group").close()
        metadata = self.store.state()["frame_groups"][2]

        before = self.dataset.read_bytes()
        with self.assertRaises(HTTPError) as error:
            self.post({"video": 0, "action": "merge", "id": other_unit["id"], "frame": 2,
                       "revision": self.store.state()["revision"]}, route="/api/frame-group")
        self.assertEqual(error.exception.code, 400)
        self.assertIn("Ungroup that unit first", error.exception.read().decode())
        self.assertEqual(self.dataset.read_bytes(), before)

        self.post(self.label(frame=5, action="trash")).close()
        before = self.dataset.read_bytes()
        with self.assertRaises(HTTPError) as error:
            self.post({"video": 0, "action": "merge", "id": metadata["id"], "frame": 5,
                       "revision": self.store.state()["revision"]}, route="/api/frame-group")
        self.assertEqual(error.exception.code, 400)
        self.assertIn("Restore this trashed frame", error.exception.read().decode())
        self.assertEqual(self.dataset.read_bytes(), before)

        self.post(self.group(frames=[2, 3]), route="/api/frame-group").close()
        target = self.store.state()["frame_groups"][-1]
        # A foreign id is identified and rejected rather than being used as a
        # destination merely because its ordinal matches this recording.
        manifest_path = self.archive / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        second = self.archive / "other-recording"
        second.mkdir()
        (second / "frame_timestamps.csv").write_bytes(
            (self.archive / "fixture-recording/frame_timestamps.csv").read_bytes())
        for frame in range(1, 11):
            (second / f"frame_{frame:06d}.jpg").write_bytes(
                (self.archive / "fixture-recording" / f"frame_{frame:06d}.jpg").read_bytes())
        manifest["videos"].append({**manifest["videos"][0], "output_directory": "other-recording"})
        manifest_path.write_text(json.dumps(manifest))
        self.store = review.Store(self.archive, self.dataset, self.root / "none")
        self.server.store = self.store
        before = self.dataset.read_bytes()
        with self.assertRaises(HTTPError) as error:
            self.post({"video": 1, "action": "merge", "id": target["id"], "frame": 4,
                       "revision": self.store.state()["revision"]}, route="/api/frame-group")
        self.assertEqual(error.exception.code, 400)
        self.assertIn("another recording", error.exception.read().decode())
        self.assertEqual(self.dataset.read_bytes(), before)

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
        manifest["videos"][0]["retained_frames"] = [2, 3, 8]
        path.write_text(json.dumps(manifest))
        store = review.Store(self.archive, self.dataset, self.root / "no-preview")
        self.assertTrue(store.video(0)["retention_only"])
        self.assertEqual(store.video(0)["frame_count"], 10)
        self.assertEqual(store.video(0)["times"][2], .2)
        self.assertTrue(store.frame(0, 3).is_file())
        self.assertRaisesRegex(ValueError, "permanently deleted", store.frame, 0, 4)
        self.assertRaisesRegex(ValueError, "read-only", store.annotate, self.label())
        self.assertEqual(len(store.diagram(0)["nodes"]), 1)
        self.store = store
        self.server.store = store
        self.post(self.group(frames=[2, 3]), route="/api/frame-group").close()
        group = self.store.state()["frame_groups"][0]
        with self.post({"video": 0, "action": "merge", "id": group["id"], "frame": 8,
                        "revision": self.store.state()["revision"]},
                       route="/api/frame-group") as response:
            merged = json.load(response)
        self.assertEqual(merged["frame_groups"][0]["frames"], [2, 3, 8])
        self.post(self.group(frames=[3, 8], type="same_scene"),
                  route="/api/frame-group").close()
        source, destination = self.store.state()["frame_groups"]
        with self.post({"video": 0, "action": "membership", "member_action": "move",
                        "source_id": source["id"], "destination_id": destination["id"], "frame": 2,
                        "revision": self.store.state()["revision"]},
                       route="/api/frame-group") as response:
            corrected = json.load(response)
        self.assertEqual(corrected["frame_groups"][0]["frames"], [3, 8])
        self.assertEqual(corrected["frame_groups"][1]["frames"], [2, 3, 8])

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
