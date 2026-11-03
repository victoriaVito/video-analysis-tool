"""Compare saved human positives with earlier candidates without inventing negatives."""
import argparse
import importlib.util
import json
from pathlib import Path

def match_times(human, candidates, tolerance):
    pairs = sorted((abs(a - b), i, j) for i, a in enumerate(human)
                   for j, b in enumerate(candidates) if abs(a - b) <= tolerance)
    used_human, used_candidates = set(), set()
    for _, i, j in pairs:
        if i not in used_human and j not in used_candidates:
            used_human.add(i)
            used_candidates.add(j)
    return len(used_human)


def main():
    spec = importlib.util.spec_from_file_location("review", Path(__file__).with_name("server.py"))
    review = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(review)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--videos", type=int, nargs="+", default=[1, 2, 3, 4])
    parser.add_argument("--output", type=Path,
                        default=review.PROJECT / ".ai-work/temporary/soda-selection-learning.json")
    args = parser.parse_args()
    if not args.videos or len(set(args.videos)) != len(args.videos):
        parser.error("Choose unique one-based video numbers")
    store = review.Store()
    for number in args.videos:
        store.video(number - 1)
    state = store.state()
    report = {
        "dataset_revision": state["revision"],
        "method": "Greedy nearest one-to-one temporal agreement, not semantic correctness or optimal matching.",
        "limitations": [
            "Only approved positives are used; omission is not rejection.",
            "Holdout selection comparisons are descriptive only, not independent evaluation or fitting.",
            "Reviewed videos retain their original split; calibration results are not independent holdout.",
            "Temporal proximity does not prove equivalent visual state.",
            "No weights or detector thresholds have been trained or accepted.",
        ],
        "videos": [],
    }
    for index in (number - 1 for number in args.videos):
        video = store.videos[index]
        human = store.diagram(index)["nodes"]
        package = (review.PROJECT / ".ai-work/temporary/video-context-full-review"
                   / video["name"] / "selected-frames.json")
        candidates = json.loads(package.read_text())
        # Video 1 was explicitly finalized only through 86 seconds.
        end = 86 if index == 0 else video["times"][-1]
        candidates = [c for c in candidates if c["source_time"] <= end]
        suggestions = [f for p in state["preselections"] if p["video"] == video["name"]
                       for f in p["frames"] if f["source_time"] <= end]
        comparisons = {}
        groups = [("detector", candidates), ("analyst", suggestions)]
        if any("review_group" in p for p in suggestions):
            groups.extend([
                ("analyst_primary", [p for p in suggestions if p.get("review_group") != "uncertain"]),
                ("analyst_uncertain", [p for p in suggestions if p.get("review_group") == "uncertain"]),
            ])
        for name, predictions in groups:
            if not predictions:
                continue
            comparisons[name] = {
                "candidate_count": len(predictions),
                "exact_frame_matches": len({h["frame"] for h in human}
                                           & {p["source_frame"] for p in predictions}),
                "one_to_one_matches_by_tolerance_seconds": {
                    str(t): match_times([h["time"] for h in human],
                                       [p["source_time"] for p in predictions], t)
                    for t in (.25, 1, 2.5)
                },
                "nearest_candidate_offsets_seconds": [{
                    "human_frame": h["frame"], "human_time": h["time"],
                    "offset": round(min(abs(h["time"] - p["source_time"])
                                        for p in predictions), 6),
                } for h in human],
            }
        report["videos"].append({
            "video": video["name"], "approved_count": len(human),
            "split": state["split_by_video"][video["name"]],
            "selection_scope": "Current retained positives, including user-authorized review edits; not all original human clicks.",
            "review_end_seconds": end, "comparisons": comparisons,
        })
    assert store.state()["revision"] == state["revision"], "Feedback changed during comparison"
    output = args.output
    output.write_text(json.dumps(report, indent=2))
    assert json.loads(output.read_text()) == report
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
