import unittest
import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image

SPEC = importlib.util.spec_from_file_location("train", Path(__file__).with_name("train_selector.py"))
train = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(train)


class TrainingTests(unittest.TestCase):
    def test_features_capture_local_temporal_change(self):
        dark = Image.new("RGB", (24, 48), "black")
        light = Image.new("RGB", (24, 48), "white")
        stable = train.features([dark, dark, dark])
        changed = train.features([dark, light, light])
        self.assertEqual(stable.shape, (108,))
        self.assertTrue(np.all(stable == 0))
        self.assertTrue(np.all(changed[36:72] == 1))

    def test_fit_is_deterministic_and_scores_nearby_higher(self):
        matrix = np.array([[0, 0], [.1, .1], [.8, .8], [.9, .9]])
        model = train.fit(matrix, clusters=2)
        self.assertEqual(model, train.fit(matrix, clusters=2))
        scores = train.distance(model, np.array([[.05, .05], [10, 10]]))
        self.assertLess(scores[0], scores[1])
        with self.assertRaises(ValueError):
            train.fit(np.array([[np.nan], [1]]))

    def test_only_latest_retained_human_calibration_positives_fit(self):
        def case(video, frame, label, reviewer="Victoria", when="1"):
            return {"id": f"{video}-{frame}-{when}", "video": video,
                    "context": [{"role": "target", "source_frame": frame}],
                    "label_history": [{"label": label, "reviewer": reviewer, "recorded_at": when}]}
        data = {"split_by_video": {"train": "calibration", "test": "holdout"},
                "cases": [case("train", 1, "new_state"), case("train", 2, "new_state"),
                          case("train", 3, "uncertain"), case("test", 4, "new_state"),
                          case("train", 5, "new_state", "Copilot"),
                          case("train", 6, "new_state"),
                          case("train", 6, "same_state", when="2")]}
        selected = train.positives(data, {"trashed": [{"video": "train", "frame": 2}]})
        self.assertEqual([(x["video"], x["frame"]) for x in selected], [("train", 1)])


if __name__ == "__main__":
    unittest.main()
