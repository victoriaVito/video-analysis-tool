import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PIL import Image


SCRIPT = Path(__file__).with_name("extract_new_recording.py")
SPEC = importlib.util.spec_from_file_location("extract_new_recording", SCRIPT)
extractor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extractor)
SERVER_SPEC = importlib.util.spec_from_file_location(
    "review_server", SCRIPT.parents[1] / "tools/soda-frame-review/server.py"
)
review = importlib.util.module_from_spec(SERVER_SPEC)
SERVER_SPEC.loader.exec_module(review)


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        temporary = extractor.PROJECT / ".ai-work/temporary"
        temporary.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="extract-test-", dir=temporary)
        self.root = Path(self.temp.name)
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.media = self.root / "media"
        self.media.mkdir()
        self.destination = self.root / "output"
        self.destination.mkdir()
        self.source = self.media / "fixture.mp4"
        self.source.write_bytes(b"synthetic-video-data")
        self.source_before = self.source.read_bytes()

    def tearDown(self):
        self.temp.cleanup()

    def test_extracts_into_named_timestamped_folder_and_keeps_source_copy(self):
        metadata = {
            "streams": [{
                "codec_name": "fixture",
                "width": 10,
                "height": 20,
                "nb_frames": "2",
                "avg_frame_rate": "10/1",
            }],
            "format": {"duration": "0.2"},
        }

        def fake_run(command, check):
            self.assertTrue(check)
            output_pattern = Path(command[-1])
            for number in range(1, 3):
                Image.new("RGB", (10, 20), (number, 20, 30)).save(
                    output_pattern.parent / f"frame_{number:06d}.jpg"
                )

        class FakeProbe:
            stdout = iter(("0.0\n", "0.1\n"))

            def wait(self):
                return 0

        def fake_check_output(command, **kwargs):
            if command[0] == "ffprobe":
                return json.dumps(metadata).encode()
            return "ffmpeg version test"

        fixed_time = datetime(2026, 11, 9, 12, 34, 56)
        with (
            patch.object(extractor, "PROJECT", self.repository),
            patch.object(extractor.subprocess, "check_output", side_effect=fake_check_output),
            patch.object(extractor.subprocess, "run", side_effect=fake_run),
            patch.object(extractor.subprocess, "Popen", return_value=FakeProbe()),
        ):
            folder = extractor.extract_recording(
                self.source, self.destination, "My Recording", now=fixed_time
            )

        self.assertEqual(folder, self.destination / "20261109T123456_My-Recording")
        self.assertEqual((folder / "source.mp4").read_bytes(), self.source_before)
        self.assertEqual(self.source.read_bytes(), self.source_before)
        self.assertEqual(
            sorted(path.name for path in (folder / "frames").iterdir()),
            ["frame_000001.jpg", "frame_000002.jpg", "frame_timestamps.csv"],
        )
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(manifest["total_frames"], 2)
        self.assertEqual(manifest["videos"][0]["output_directory"], "frames")
        self.assertEqual(manifest["videos"][0]["extracted_frame_count"], 2)
        dataset = self.root / "dataset.json"
        dataset.write_text(json.dumps({"cases": [], "split_by_video": {"frames": "calibration"}}))
        store = review.Store(folder, dataset, self.root / "no-preview.mp4")
        self.assertEqual(store.video(0)["frame_count"], 2)
        self.assertTrue(store.frame(0, 1).is_file())

    def test_refuses_source_or_destination_inside_repository(self):
        repository_video = self.repository / "private.mp4"
        repository_video.write_bytes(b"fixture")
        with patch.object(extractor, "PROJECT", self.repository):
            with self.assertRaisesRegex(ValueError, "source videos outside"):
                extractor.extract_recording(repository_video, self.destination, "test")
            with self.assertRaisesRegex(ValueError, "destination outside"):
                extractor.extract_recording(self.source, self.repository, "test")

    def test_rejects_names_without_safe_path_characters(self):
        with patch.object(extractor, "PROJECT", self.repository):
            with self.assertRaisesRegex(ValueError, "at least one letter or number"):
                extractor.extract_recording(self.source, self.destination, "../")

    def test_prompts_for_source_destination_and_name_when_omitted(self):
        args = type("Args", (), {"source": None, "destination": None, "name": None})()
        with patch.object(extractor, "PROJECT", self.repository), patch(
            "builtins.input",
            side_effect=[str(self.source), str(self.destination), "fixture name"],
        ) as prompt:
            source, destination, name = extractor._collect_inputs(args)
        self.assertEqual(source, self.source.resolve())
        self.assertEqual(destination, self.destination.resolve())
        self.assertEqual(name, "fixture-name")
        self.assertEqual(prompt.call_count, 3)


if __name__ == "__main__":
    unittest.main()
