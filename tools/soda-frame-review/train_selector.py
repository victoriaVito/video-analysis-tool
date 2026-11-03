"""Fit an experimental positive-only context ranker without changing feedback."""
import argparse
import bisect
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

SPEC = importlib.util.spec_from_file_location("review", Path(__file__).with_name("server.py"))
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


FEATURE_VERSION = "rgb-grid-and-context-delta-v1"


def features(images):
    """Pool appearance and temporal differences; no timestamps or recording IDs."""
    arrays = []
    for image in images:
        arrays.append(np.asarray(image.convert("RGB").resize((24, 48)), dtype=float) / 255)
    before, target, after = arrays

    def pool(array):
        return array.reshape(4, 12, 3, 8, 3).mean(axis=(1, 3)).ravel()

    return np.concatenate([
        pool(target), pool(np.abs(target - before)), pool(np.abs(after - target)),
    ])


def fit(matrix, clusters=4):
    matrix = np.asarray(matrix, dtype=float)
    if matrix.ndim != 2 or len(matrix) < 2 or not np.isfinite(matrix).all():
        raise ValueError("At least two finite feature vectors are required")
    mean = matrix.mean(axis=0)
    scale = np.maximum(matrix.std(axis=0), 0.02)
    normalized = (matrix - mean) / scale
    centers = [normalized[0]]
    for _ in range(1, min(clusters, len(matrix))):
        distances = ((normalized[:, None] - np.array(centers)) ** 2).mean(axis=2).min(axis=1)
        centers.append(normalized[int(distances.argmax())])
    centers = np.array(centers)
    for _ in range(100):
        assignment = ((normalized[:, None] - centers) ** 2).mean(axis=2).argmin(axis=1)
        updated = np.array([
            normalized[assignment == i].mean(axis=0) if np.any(assignment == i) else center
            for i, center in enumerate(centers)
        ])
        if np.allclose(updated, centers, atol=1e-8):
            centers = updated
            break
        centers = updated
    return {"feature_version": FEATURE_VERSION, "mean": mean.tolist(),
            "scale": scale.tolist(), "centers": centers.tolist()}


def distance(model, matrix):
    matrix = np.asarray(matrix, dtype=float)
    if model["feature_version"] != FEATURE_VERSION or not np.isfinite(matrix).all():
        raise ValueError("Unsupported features or nonfinite input")
    normalized = (matrix - np.array(model["mean"])) / np.array(model["scale"])
    return ((normalized[:, None] - np.array(model["centers"])) ** 2).mean(axis=2).min(axis=1)


def positives(data, state):
    trash = {(x["video"], x["frame"]) for x in state["trashed"]}
    latest = {}
    for case in data["cases"]:
        history = case.get("label_history", [])
        if not history:
            continue
        target = next(x for x in case["context"] if x["role"] == "target")
        key = (case["video"], target["source_frame"])
        event = history[-1]
        if key not in latest or event["recorded_at"] >= latest[key][0]["recorded_at"]:
            latest[key] = (event, case["id"])
    return [
        {"video": video, "frame": frame, "case_id": case_id, "label_event": event}
        for (video, frame), (event, case_id) in sorted(latest.items())
        if data["split_by_video"][video] == "calibration"
        and event["label"] == "new_state" and event.get("reviewer") == "Victoria"
        and (video, frame) not in trash
    ]


def extract(store, index, frame):
    video = store.videos[index]
    timestamp = video["times"][frame - 1]
    indices = []
    for offset in (-1, 0, 1):
        right = bisect.bisect_left(video["times"], timestamp + offset)
        nearest = min(
            (i for i in (right - 1, right) if 0 <= i < len(video["times"])),
            key=lambda i: abs(video["times"][i] - timestamp - offset),
        )
        indices.append(nearest + 1)
    paths = [store.frame(index, number) for number in indices]
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    images = []
    for path in paths:
        with Image.open(path) as image:
            images.append(image.convert("RGB"))
    return features(images), [
        {"frame": number, "time": video["times"][number - 1], "sha256": digest}
        for number, digest in zip(indices, hashes)
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score-video", type=int, default=5, choices=(5, 6, 7))
    args = parser.parse_args()
    store = review.Store()
    data, revision = store.read()
    state = store.state()
    if state["revision"] != revision:
        raise RuntimeError("Feedback changed before training")
    samples = positives(data, state)
    indexes = {v["name"]: i for i, v in enumerate(store.videos)}
    vectors = []
    for sample in samples:
        vector, context = extract(store, indexes[sample["video"]], sample["frame"])
        sample["context"] = context
        vectors.append(vector)
    matrix = np.array(vectors)
    model = fit(matrix)
    folds = []
    for video in sorted({s["video"] for s in samples}):
        held = np.array([s["video"] == video for s in samples])
        fold_model = fit(matrix[~held])
        distances = distance(fold_model, matrix[held])
        folds.append({"held_video": video, "held_positives": int(held.sum()),
                      "median_positive_distance": float(np.median(distances)),
                      "meaning": "Positive-only cross-recording diagnostic; not accuracy or recall."})
    root = review.PROJECT / "data/soda-keyframe-calibration/models" / datetime.now(
        timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    root.mkdir(parents=True, exist_ok=False)
    model.update({
        "status": "experimental_not_deployed", "dataset_revision": revision,
        "algorithm": "Standardized four-centroid k-means fitted only on retained human positives",
        "score_meaning": "Lower squared distance means visual-context similarity to positive prototypes, not probability of usefulness.",
        "training_samples": samples, "negative_samples": 0,
        "validation": folds, "feature_count": int(matrix.shape[1]),
        "limitations": [
            "No explicit human negative set: discrimination and quality improvement cannot be established.",
            "Low-resolution RGB features do not read text or understand actions.",
            "Calibration recordings were previously inspected; folds are diagnostic, not independent holdout.",
            "Prototype similarity may prioritize redundant frames and miss novel important states.",
            "Existing detector candidate coverage is incomplete; ranking cannot recover absent frames.",
        ],
        "versions": {"numpy": np.__version__, "pillow": Image.__version__},
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    })
    index = args.score_video - 1
    video = store.videos[index]
    if data["split_by_video"][video["name"]] != "holdout":
        raise RuntimeError("Scoring target must retain its holdout split")
    package = review.PROJECT / ".ai-work/temporary/video-context-full-review" / video["name"] / "selected-frames.json"
    raw = package.read_bytes()
    candidates = json.loads(raw)
    ranked = []
    for candidate in candidates:
        vector, context = extract(store, index, candidate["source_frame"])
        ranked.append({
            "frame": candidate["source_frame"], "time": candidate["source_time"],
            "prototype_distance": float(distance(model, vector[None])[0]),
            "context": context, "status": "unreviewed_not_approved",
        })
    ranked.sort(key=lambda x: (x["prototype_distance"], x["frame"]))
    prediction = {"video": video["name"], "candidate_package_sha256": hashlib.sha256(raw).hexdigest(),
                  "scope": "Existing detector candidates only, not complete source-frame coverage.",
                  "holdout_labels_read_for_fitting_or_tuning": False, "ranked_candidates": ranked}
    if store.read()[1] != revision:
        raise RuntimeError("Feedback changed during training; result not accepted")
    for filename, payload in (("model.json", model), ("ranking.json", prediction)):
        path = root / filename
        path.write_text(json.dumps(payload, indent=2, allow_nan=False))
        if json.loads(path.read_text()) != payload:
            raise RuntimeError(f"Readback mismatch: {path}")
    reloaded = json.loads((root / "model.json").read_text())
    if not np.allclose(distance(model, matrix), distance(reloaded, matrix)):
        raise RuntimeError("Reloaded model predictions differ")
    sheet = Image.new("RGB", (1280, 1500), "white")
    draw = ImageDraw.Draw(sheet)
    for i, row in enumerate(ranked[:16]):
        x, y = i % 8 * 160, i // 8 * 750
        draw.text((x, y + 3), f'Rank {i+1}: #{row["frame"]}', fill="black")
        draw.text((x, y + 18), f'{row["time"]:.3f}s', fill="black")
        with Image.open(store.frame(index, row["frame"])) as source:
            image = source.copy()
            image.thumbnail((154, 700))
            sheet.paste(image, (x, y + 40))
    sheet.save(root / "top-ranked-review.jpg", quality=92)
    print(json.dumps({"output": str(root), "training_positives": len(samples),
                      "feature_count": model["feature_count"], "scored_candidates": len(ranked),
                      "status": model["status"], "feedback_unchanged": store.read()[1] == revision}))


if __name__ == "__main__":
    main()
