"""Reversible selection closeout and evidence packages, without model fitting."""
import base64
import copy
import fcntl
import hashlib
import html
import io
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

COMPARE_SPEC = importlib.util.spec_from_file_location("soda_comparison", Path(__file__).with_name("compare_selections.py"))
comparison = importlib.util.module_from_spec(COMPARE_SPEC)
COMPARE_SPEC.loader.exec_module(comparison)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".writing")
    with temporary.open("w") as handle:
        json.dump(data, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    if json.loads(path.read_text()) != data:
        raise OSError("Saved learning artifact failed readback")


def preview(store, index):
    video = store.video(index)
    graph = store.diagram(index)
    return {"video": index, "name": video["name"], "revision": graph["revision"],
            "selected": len(graph["nodes"]), "source_count": video["frame_count"],
            "nonselected": video["frame_count"] - len(graph["nodes"]),
            "retention_only": video.get("retention_only", False),
            "split": store.read()[0]["split_by_video"][video["name"]]}


def history(project):
    return [json.loads(p.read_text()) for p in sorted(
        (project / "data/soda-review-learning").glob("*/result.json"), reverse=True)]


def similar(store, index):
    """Propose bounded pixel-similar pairs; never infer semantic duplication."""
    graph = store.diagram(index)
    if len(graph["nodes"]) > 300:
        raise ValueError("Similarity review supports up to 300 retained frames per recording.")
    images, hashes = {}, {}
    for node in graph["nodes"]:
        raw = store.frame(index, node["frame"]).read_bytes()
        hashes[str(node["frame"])] = digest(raw)
        with Image.open(io.BytesIO(raw)) as image:
            rgb = image.convert("RGB")
            images[node["frame"]] = (rgb.resize((16, 32)), rgb.resize((96, 192)), rgb.size)
    pairs = []
    for position, left in enumerate(graph["nodes"]):
        a = images[left["frame"]]
        for right in graph["nodes"][position + 1:]:
            b = images[right["frame"]]
            if a[2] != b[2]:
                continue
            coarse = sum(ImageStat.Stat(ImageChops.difference(a[0], b[0])).mean) / 3
            if coarse > 10:
                continue
            difference = sum(ImageStat.Stat(ImageChops.difference(a[1], b[1])).mean) / 3
            if difference <= 5:
                pairs.append({"left": left, "right": right, "difference": round(difference, 6),
                              "identical_file": hashes[str(left["frame"])] == hashes[str(right["frame"])]})
    if store.read()[1] != graph["revision"]:
        raise ValueError("Decisions changed during similarity scan. Refresh and scan again.")
    pairs.sort(key=lambda pair: pair["difference"])
    return {"video": index, "revision": graph["revision"], "hashes": hashes,
            "pairs": pairs[:40], "candidate_pairs": len(pairs), "shown": min(40, len(pairs)),
            "method": "Mean RGB difference <= 5/255 on 96x192 previews; pixel candidates only, not semantic duplicates."}


def retained_graph(graph, discards):
    graph = copy.deepcopy(graph)
    graph["nodes"] = [node for node in graph["nodes"] if node["frame"] not in discards]
    graph["edges"] = [{"from": a["id"], "to": b["id"], "kind": "chronological",
                      "elapsed_seconds": b["time"] - a["time"]}
                     for a, b in zip(graph["nodes"], graph["nodes"][1:])]
    lines = ["flowchart TD"]
    for node in graph["nodes"]:
        lines.append(f'  {node["id"]}["Frame {node["frame"]} | {node["time"]:.3f}s"]')
    for edge in graph["edges"]:
        lines.append(f'  {edge["from"]} -->|later in recording| {edge["to"]}')
    graph["mermaid"] = "\n".join(lines)
    return graph


def run(store, project, payload):
    index = payload.get("video")
    video = store.video(index)
    if video.get("retention_only"):
        raise ValueError("Recording already finalized and permanently cleaned; use its saved collection and diagrams")
    if payload.get("confirm") is not True:
        raise ValueError("Confirm the recording and recoverable curation options first")
    for key in ("hide_nonselected", "save_diagram", "compare"):
        if type(payload.get(key)) is not bool:
            raise ValueError("Choose explicit learning options")
    notes = payload.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 12000:
        raise ValueError("Learning notes must be at most 12,000 characters")
    graph = store.diagram(index)
    if graph["revision"] != payload.get("revision"):
        raise ValueError("Decisions changed. Refresh the selection summary and confirm again.")
    if not graph["nodes"]:
        raise ValueError("No selected frames; review this recording before finalizing")
    discards = payload.get("discard_frames", [])
    if (not isinstance(discards, list) or any(type(n) is not int for n in discards)
            or len(set(discards)) != len(discards)):
        raise ValueError("Choose unique selected frame numbers to discard")
    selected = {n["frame"] for n in graph["nodes"]}
    if not set(discards).issubset(selected) or len(discards) >= len(selected):
        raise ValueError("Discard only selected frames and retain at least one")
    if discards and not payload["hide_nonselected"]:
        raise ValueError("Enable recoverable cleanup to discard reviewed similar frames")
    hashes = payload.get("similarity_hashes", {})
    if not isinstance(hashes, dict):
        raise ValueError("Invalid similarity evidence hashes")
    discarded = []
    for number in discards:
        raw = store.frame(index, number).read_bytes()
        if hashes.get(str(number)) != digest(raw):
            raise ValueError("Similarity evidence changed or was not reviewed. Scan again.")
        discarded.append({"frame": number, "timestamp_seconds": video["times"][number-1],
                          "sha256": digest(raw), "reason": "Explicit similar-frame discard confirmed in learning closeout"})
    selected_before = len(selected)
    graph = retained_graph(graph, set(discards))
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    root = project / "data/soda-review-learning" / run_id
    package = project / "data/soda-approved-frames" / run_id
    folder = package / video["name"]
    backup = project / ".ai-work/temporary/soda-learning" / run_id
    root.mkdir(parents=True)
    folder.mkdir(parents=True)
    backup.mkdir(parents=True)
    result = {"id": run_id, "status": "building", "video": index, "name": video["name"],
              "selected": len(graph["nodes"]), "source_count": video["frame_count"],
              "selected_before_cleanup": selected_before, "discarded_similar": discarded,
              "selection_revision": graph["revision"], "options": {
                  key: payload[key] for key in ("hide_nonselected", "save_diagram", "compare")},
              "notes": notes, "model_fitted": False, "originals_deleted": False,
              "package": str(package.relative_to(project))}
    write_json(root / "result.json", result)
    try:
        entries = []
        previews = []
        for node in graph["nodes"]:
            source = store.frame(index, node["frame"])
            raw = source.read_bytes()
            path = folder / source.name
            path.write_bytes(raw)
            if digest(path.read_bytes()) != digest(raw):
                raise OSError("Selected-frame copy failed readback")
            with Image.open(io.BytesIO(raw)) as image:
                image.thumbnail((220, 480))
                output = io.BytesIO()
                image.convert("RGB").save(output, "JPEG", quality=88)
            previews.append(base64.b64encode(output.getvalue()).decode())
            entries.append({"frame": node["frame"], "timestamp_seconds": node["time"],
                            "file": source.name, "source": str(source), "sha256": digest(raw),
                            "rationale": "Explicit retained selection; intent not inferred."})
        record = {"video": video["name"], "count": len(entries), "frames": entries}
        write_json(folder / "manifest.json", record)
        report = {"scope": "Descriptive agreement only; omitted frames are not negative labels.",
                  "semantic_review": "Not performed by this automated routine. Review the saved images with Copilot.",
                  "notes": notes, "selected_count": len(entries),
                  "discarded_similar": discarded,
                  "limitations": ["No model fitting; similar-frame discards require explicit review.",
                                  "Temporal proximity does not prove equivalent content.",
                                  "Chronological arrows do not establish actions, purchases or causality."]}
        if payload["compare"]:
            data, _ = store.read()
            predictions = [f for p in data.get("preselections", []) if p["video"] == video["name"] for f in p["frames"]]
            groups = {"all": predictions,
                      "primary": [f for f in predictions if f.get("review_group") != "uncertain"],
                      "uncertain": [f for f in predictions if f.get("review_group") == "uncertain"]}
            report["comparisons"] = {}
            for name, candidates in groups.items():
                matches = {}
                for tolerance in (.25, 1, 2.5):
                    matches[str(tolerance)] = comparison.match_times(
                        [n["time"] for n in graph["nodes"]],
                        [c["source_time"] for c in candidates], tolerance)
                report["comparisons"][name] = {
                    "candidate_count": len(candidates),
                    "exact_matches": len({n["frame"] for n in graph["nodes"]} &
                                         {c["source_frame"] for c in candidates}),
                    "greedy_one_to_one_temporal_matches": matches}
        write_json(root / "analysis.json", report)
        # Source validation/copying happens before touching human decisions.
        with store.lock, store.dataset.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            before, revision = store.read()
            if revision != graph["revision"]:
                raise ValueError("Decisions changed during export; feedback was not curated. Refresh and retry.")
            if any(digest(store.frame(index, e["frame"]).read_bytes()) != e["sha256"] for e in entries):
                raise ValueError("Source evidence changed during export")
            if any(digest(store.frame(index, e["frame"]).read_bytes()) != e["sha256"] for e in discarded):
                raise ValueError("Reviewed similar-frame evidence changed during export")
            keep = [e["frame"] for e in entries]
            inventory = sorted(p.name for p in (store.archive / video["name"]).glob("frame_*.jpg"))
            if inventory != [f"frame_{n:06d}.jpg" for n in range(1, video["frame_count"] + 1)]:
                raise ValueError("Source inventory differs from manifest; no curation applied")
            result["before_revision"] = revision
            if payload["hide_nonselected"]:
                (backup / "dataset-before.json").write_bytes(store.dataset.read_bytes())
                data = copy.deepcopy(before)
                data.setdefault("curated_windows", []).append({
                    "video": video["name"], "first_frame": 1, "last_frame": video["frame_count"],
                    "end_time_seconds": video["times"][-1], "keep_frames": keep,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "reviewer": "Victoria (explicit learning-tab confirmation)",
                    "method": "explicit_dashboard_confirmation",
                    "policy": "Recoverable dashboard-only trash. Originals and label histories unchanged; omission is not rejection. Explicit reviewed similar-frame discards recorded separately.",
                    "discarded_similar": discarded,
                })
                write_json(store.dataset, data)
                persisted, after = store.read()
                if persisted != data or persisted["cases"] != before["cases"]:
                    raise OSError("Curation readback failed")
                result.update(curation_applied=True, recoverably_hidden=video["frame_count"] - len(keep))
            else:
                after = revision
                result["curation_applied"] = False
            result["after_revision"] = after
            if [n["frame"] for n in store.diagram(index)["nodes"]] != keep:
                raise OSError("Retained selection changed after curation")
        manifest = {"status": "verified", "dataset_revision": after,
                    "source_policy": "Complete originals preserved", "videos": [record]}
        write_json(package / "manifest.json", manifest)
        graph["revision"] = after
        graph["snapshot"] = "Read-only chronological export, not verified action edges"
        write_json(root / "diagram.json", graph)
        if payload["save_diagram"]:
            stem = f"soda-video-{index+1}-selection-{run_id}"
            path = project / "docs" / f"{stem}.html"
            path.with_suffix(".mmd").write_text(graph["mermaid"] + "\n")
            write_json(path.with_suffix(".json"), graph)
            cards = []
            for number, (entry, image) in enumerate(zip(entries, previews), 1):
                cards.append(f'<li><h3>{number}. Frame {entry["frame"]} | {entry["timestamp_seconds"]:.3f}s</h3>'
                             f'<img loading="lazy" src="data:image/jpeg;base64,{image}" alt="Selected frame {entry["frame"]}">'
                             '<p>Later in recording, not a verified action.</p></li>')
            page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Recording {index+1} selected-frame diagram</title>
<style>body{{margin:24px;background:#101820;color:#eef3f8;font:15px/1.6 system-ui}}main{{max-width:1180px;margin:auto}}
ol{{list-style:none;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px}}
li{{border:1px solid #536373;border-radius:8px;padding:12px}}img{{width:100%;height:380px;object-fit:contain}}
a{{color:#8edbc3}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}h3{{font-size:15px}}</style></head>
<body><main><h1>Recording {index+1} - selected-frame diagram</h1>
<p>{len(entries)} retained frames. Chronology only: no confirmed actions, purchases or branches.
Images may contain personal information; inspect/redact before external sharing.</p>
<h2>Review notes</h2><pre>{html.escape(notes or 'Semantic review pending. No user intent inferred.')}</pre>
<p><a href="{stem}.json">Diagram JSON</a> | <a href="{stem}.mmd">Mermaid diagram</a> |
<a href="../{result['package']}/manifest.json">Frame manifest and hashes</a></p>
<h2>Selection analysis</h2><pre>{html.escape(json.dumps(report, indent=2))}</pre>
<ol>{''.join(cards)}</ol><p>Selection revision: {graph['revision']}.
No model weights were changed. Preserved original split; reviewed holdout data is not untouched evaluation.</p></main></body></html>"""
            path.write_text(page)
            if path.read_text() != page:
                raise OSError("Diagram readback failed")
            result["diagram"] = path.name
        prompt = (f"Review recording {index+1} ({video['name']}) using the saved selection package "
                  f"{result['package']} and data/soda-review-learning/{run_id}/analysis.json. "
                  "Inspect selected frames; compare text, controls, counters and intermediate states. "
                  "Separate observations, interpretations and unknowns. Keep every selected frame. "
                  "Record source-grounded procedural learning in tools/soda-frame-review/README.md "
                  "and update FORVito.md. Do not fit a model or treat omissions as negative labels.")
        (root / "copilot-request.txt").write_text(prompt)
        result.update(status="complete", request=prompt,
                      split=store.read()[0]["split_by_video"][video["name"]])
        write_json(root / "result.json", result)
        return result
    except (OSError, ValueError, KeyError) as error:
        result.update(status="incomplete", error=str(error))
        write_json(root / "result.json", result)
        raise ValueError(f"Learning run incomplete ({run_id}): {error}. Inspect its saved receipt before retrying.") from error
