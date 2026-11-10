"""Local, source-read-only frame reviewer. Run with the project's Pillow-capable Python."""

import argparse
import bisect
import csv
import fcntl
import hashlib
import heapq
import hmac
import io
import importlib.util
import json
import logging
import os
import secrets
import threading
import unicodedata
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageChops, ImageStat


PROJECT = Path(__file__).resolve().parents[2]
ASSETS = Path(__file__).resolve().parent
ARCHIVE = Path("/Volumes/Extreme Pro/6_niveles_outsourcing/eoc-level-captures/soda-video-frames")
DATASET = PROJECT / "data/soda-keyframe-calibration/dataset.json"
PREVIEW = PROJECT / ".ai-work/temporary/soda-continuous-review/complete-recording-preview.mp4"
LABELS = {"new_state", "animation", "same_state", "uncertain"}
CATEGORIES = [{"id": "saga_map", "name": "Saga map"}]
LOG = logging.getLogger("soda-review")
LOG_PATH = PROJECT / ".ai-work/temporary/soda-frame-review-logs/dashboard.log"
WORKSPACE_SPEC = importlib.util.spec_from_file_location("soda_workspace", ASSETS / "workspace.py")
workspace_module = importlib.util.module_from_spec(WORKSPACE_SPEC)
WORKSPACE_SPEC.loader.exec_module(workspace_module)
LEARNING_SPEC = importlib.util.spec_from_file_location("soda_learning", ASSETS / "learning.py")
learning_module = importlib.util.module_from_spec(LEARNING_SPEC)
LEARNING_SPEC.loader.exec_module(learning_module)


def configure_logging(path=LOG_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    return handler


class ConflictError(Exception):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Store:
    def __init__(self, archive=ARCHIVE, dataset=DATASET, preview=PREVIEW, reviewer="Victoria"):
        self.archive, self.dataset, self.preview = archive, dataset, preview
        self.reviewer = reviewer
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        manifest = json.loads((archive / "manifest.json").read_text())
        self.videos = []
        for index, video in enumerate(manifest["videos"]):
            name = video["output_directory"]
            source = (archive / name).resolve()
            if source.parent != archive.resolve():
                raise ValueError("Invalid archive directory")
            with (source / video["timestamps_file"]).open() as handle:
                rows = list(csv.DictReader(handle))
            times = [float(r["timestamp_seconds"]) for r in rows]
            if not times or len(times) != video["extracted_frame_count"]:
                raise ValueError(f"Source timestamp count mismatch: {name}")
            if [int(r["frame_number"]) for r in rows] != list(range(1, len(rows) + 1)):
                raise ValueError(f"Source frame order mismatch: {name}")
            if times[0] < 0 or any(b <= a for a, b in zip(times, times[1:])):
                raise ValueError(f"Invalid source timestamps: {name}")
            retained = video.get("retained_frames")
            if retained is not None and (not isinstance(retained, list) or not retained
                    or any(type(n) is not int or not 1 <= n <= len(times) for n in retained)
                    or retained != sorted(set(retained))):
                raise ValueError(f"Invalid retained frame inventory: {name}")
            self.videos.append({
                "id": index, "name": name, "times": times, "frame_count": len(times),
                "default_end": min(86.0, times[-1]) if index == 0 else times[-1],
                "preview": index == 0 and preview.is_file(),
                "retained_frames": video.get("retained_frames"),
                "retention_only": "retained_frames" in video,
            })
        self.read()

    def video(self, index):
        if type(index) is not int or not 0 <= index < len(self.videos):
            raise ValueError("Unknown recording")
        return self.videos[index]

    def frame(self, video, number):
        v = self.video(video)
        if type(number) is not int or not 1 <= number <= v["frame_count"]:
            raise ValueError("Frame is outside the source recording")
        path = self.archive / v["name"] / f"frame_{number:06d}.jpg"
        if path.resolve().parent != (self.archive / v["name"]).resolve():
            raise ValueError("Frame must remain inside its recording")
        if v["retention_only"] and number not in v["retained_frames"]:
            raise ValueError("This unselected frame was permanently deleted at the user's request")
        return path

    def read(self):
        raw = self.dataset.read_bytes()
        data = json.loads(raw)
        if not isinstance(data.get("cases"), list) or not isinstance(data.get("split_by_video"), dict):
            raise ValueError("Invalid canonical calibration dataset")
        return data, sha(raw)

    def state(self):
        data, revision = self.read()
        labels = []
        trash = {}
        for window in data.get("curated_windows", []):
            kept = set(window["keep_frames"])
            for number in range(window["first_frame"], window["last_frame"] + 1):
                trash[(window["video"], number)] = (
                    window["recorded_at"], number not in kept)
        for case in data["cases"]:
            if case.get("trash_history"):
                target = next(c for c in case["context"] if c["role"] == "target")
                key = (case["video"], target["source_frame"])
                event = case["trash_history"][-1]
                if key not in trash or event["recorded_at"] >= trash[key][0]:
                    trash[key] = (event["recorded_at"], event["trashed"])
            if case.get("label_history"):
                target = next(c for c in case["context"] if c["role"] == "target")
                event = case["label_history"][-1]
                labels.append({
                    "id": case["id"], "video": case["video"],
                    "frame": target["source_frame"], "time": target["source_time"],
                    "label": event["label"], "note": event.get("statement", ""),
                    "recorded_at": event["recorded_at"],
                })
        trashed = [{"video": name, "frame": frame}
                   for (name, frame), (_, removed) in trash.items() if removed]
        return {"revision": revision, "labels": labels, "trashed": trashed,
                "curated_windows": data.get("curated_windows", []),
                "preselections": data.get("preselections", []),
                "intervals": data.get("intervals", []),
                "frame_groups": self.numbered_groups(data.get("frame_groups", [])),
                "categories": CATEGORIES,
                "category_assignments": data.get("category_assignments", []),
                "split_by_video": data["split_by_video"],
                "dataset_path": str(self.dataset), "token": self.token}

    @staticmethod
    def numbered_groups(groups, legacy_ordinals=None):
        """Return display-numbered copies without mutating legacy dataset records."""
        copied = [dict(group) for group in groups]
        for group in copied:
            color = group.get("color")
            if color is not None and not Store.valid_group_color(color):
                group.pop("color", None)
                group["_display_color_issue"] = "Saved group color is invalid; choose a valid #RRGGBB color."
        counts = {}
        ordinals = {}
        for group in copied:
            video = group.get("video")
            if legacy_ordinals and group.get("id") in legacy_ordinals:
                ordinal = legacy_ordinals[group.get("id")]
            else:
                ordinals[video] = ordinals.get(video, 0) + 1
                ordinal = ordinals[video]
            group["_legacy_display_number"] = ordinal
            number = group.get("display_number")
            if type(number) is int and number > 0:
                key = (group.get("video"), number)
                counts[key] = counts.get(key, 0) + 1
        used = {(group.get("video"), group["display_number"])
                for group in copied
                if type(group.get("display_number")) is int and group["display_number"] > 0
                and counts[(group.get("video"), group["display_number"])] == 1}
        for group in copied:
            number = group.get("display_number")
            key = (group.get("video"), number)
            if type(number) is int and number > 0 and counts.get(key) == 1:
                group.pop("_display_number_issue", None)
                group.pop("_legacy_display_number", None)
                continue
            if "display_number" in group:
                group["_display_number_issue"] = "Saved group numbers are invalid or duplicated; save a unique positive number."
            else:
                preferred = group.pop("_legacy_display_number")
                if (group.get("video"), preferred) not in used:
                    group["display_number"] = preferred
                    used.add((group.get("video"), preferred))
                    continue
                group["_display_number_issue"] = (
                    "This legacy group number conflicts with a saved number; save a unique positive number."
                )
            candidate = 1
            while (group.get("video"), candidate) in used:
                candidate += 1
            group["display_number"] = candidate
            used.add((group.get("video"), candidate))
            group.pop("_legacy_display_number", None)
        return copied

    @staticmethod
    def valid_group_color(value):
        return (isinstance(value, str) and len(value) == 7 and value[0] == "#"
                and all(char in "0123456789abcdefABCDEF" for char in value[1:]))

    @staticmethod
    def _assert_group_numbers_valid(groups, video, exclude_id=None):
        ordinals = {}
        ordinal = 0
        for group in groups:
            if group.get("video") == video:
                ordinal += 1
                ordinals[group.get("id")] = ordinal
        relevant = [dict(group) for group in groups
                    if group.get("video") == video and group.get("id") != exclude_id]
        numbered = Store.numbered_groups(relevant, ordinals)
        if any(group.get("_display_number_issue") for group in numbered):
            raise ValueError(
                "Saved group numbers are invalid or duplicated. Edit the conflicting groups "
                "to unique positive numbers before continuing."
            )
        return {group["display_number"] for group in numbered}

    @staticmethod
    def _group_for_recording(groups, group_id, video):
        group = next((item for item in groups
                      if item.get("id") == group_id and item.get("video") == video), None)
        if group is not None:
            return group
        if any(item.get("id") == group_id for item in groups):
            raise ValueError("That group belongs to another recording. Choose a group in this recording.")
        raise ValueError("That group is no longer available. Reload saved decisions and choose again.")

    def context(self, index, number):
        v = self.video(index)
        times = v["times"]
        target_time = times[number - 1]
        result = []
        for role, t in (("before", max(0, target_time - 1)),
                        ("target", target_time), ("after", target_time + 1)):
            right = bisect.bisect_left(times, t)
            j = min((j for j in (right - 1, right) if 0 <= j < len(times)),
                    key=lambda j: abs(times[j] - t))
            path = self.frame(index, j + 1)
            result.append({"role": role, "source": str(path), "source_frame": j + 1,
                           "source_time": times[j], "sha256": sha(path.read_bytes())})
        return result

    def diagram(self, index):
        video = self.video(index)
        state = self.state()
        trash = {(x["video"], x["frame"]) for x in state["trashed"]}
        latest = {}
        for label in state["labels"]:
            if label["video"] != video["name"]:
                continue
            previous = latest.get(label["frame"])
            if previous is None or label["recorded_at"] >= previous["recorded_at"]:
                latest[label["frame"]] = label
        nodes = []
        for frame, label in sorted(latest.items()):
            if label["label"] != "new_state" or (video["name"], frame) in trash:
                continue
            self.frame(index, frame)
            nodes.append({
                "id": f"f{frame}", "frame": frame, "time": video["times"][frame - 1],
                "title": f"Frame {frame}", "note": label["note"],
                "image": f"/api/frame?video={index}&frame={frame}&width=160",
            })
        edges = [{"from": a["id"], "to": b["id"], "kind": "chronological",
                  "elapsed_seconds": b["time"] - a["time"]}
                 for a, b in zip(nodes, nodes[1:])]
        mermaid = ["flowchart TD"]
        for node in nodes:
            mermaid.append(f'  {node["id"]}["Frame {node["frame"]} | {node["time"]:.3f}s"]')
        for edge in edges:
            mermaid.append(f'  {edge["from"]} -->|later in recording| {edge["to"]}')
        return {"video": video["name"], "revision": state["revision"],
                "scope": "All approved, non-trashed frames in this recording, independent of review window.",
                "meaning": "Chronological sequence only; arrows do not assert actions, causality or branches.",
                "nodes": nodes, "edges": edges, "mermaid": "\n".join(mermaid)}

    @staticmethod
    def category_records(data):
        assignments = data.get("category_assignments", [])
        history = data.get("category_history", [])
        if not isinstance(assignments, list) or not all(isinstance(item, dict) for item in assignments):
            raise ValueError("Saved category assignments are invalid. Inspect the dataset before saving.")
        if not isinstance(history, list):
            raise ValueError("Saved category history is invalid. Inspect the dataset before saving.")
        return assignments, history

    def category_event(self, **fields):
        return {**fields, "reviewer": self.reviewer,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "method": "explicit_dashboard_category"}

    def clear_group_categories(self, data, group):
        assignments, _ = self.category_records(data)
        removed = [item for item in assignments
                   if item.get("target_type") == "group" and item.get("group_id") == group["id"]
                   and item.get("video") == group["video"]]
        if not removed:
            return
        data["category_assignments"] = [item for item in assignments if item not in removed]
        for item in removed:
            data.setdefault("category_history", []).append(self.category_event(
                category_id=item["category_id"], target_type="group",
                group_id=group["id"], video=group["video"], assigned=False,
                reason="group_removed"))

    def save_category(self, payload):
        index = payload.get("video")
        video = self.video(index)
        if payload.get("category_id") != "saga_map":
            raise ValueError("Choose the built-in Saga map category (saga_map).")
        target_type = payload.get("target_type")
        if target_type not in ("frame", "group"):
            raise ValueError("Choose an exact frame or group category target.")
        if type(payload.get("assigned")) is not bool:
            raise ValueError("Category assigned must be an explicit boolean.")
        if target_type == "frame":
            if "group_id" in payload:
                raise ValueError("A frame category cannot include a group target.")
            if not self.frame(index, payload.get("frame")).is_file():
                raise ValueError("The exact category source frame is unavailable.")
            target = {"frame": payload["frame"]}
        else:
            if "frame" in payload or not isinstance(payload.get("group_id"), str) or not payload["group_id"]:
                raise ValueError("Choose one existing group id, without a frame target.")
            target = {"group_id": payload["group_id"]}
        with self.lock, self.dataset.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data, revision = self.read()
            if payload.get("revision") != revision:
                raise ConflictError("Feedback changed in another window. Reload saved decisions before saving categories.")
            if target_type == "group":
                groups = data.get("frame_groups", [])
                self._group_for_recording(groups, target["group_id"], video["name"])
                if sum(group.get("id") == target["group_id"] for group in groups) != 1:
                    raise ValueError("That group id is ambiguous. Inspect saved groups before saving categories.")
            elif not self.frame(index, target["frame"]).is_file():
                raise ValueError("The exact category source frame is unavailable.")
            assignments, _ = self.category_records(data)
            identity = {"category_id": "saga_map", "video": video["name"],
                        "target_type": target_type, **target}
            matches = [item for item in assignments
                       if all(item.get(key) == value for key, value in identity.items())]
            if bool(matches) == payload["assigned"]:
                result = self.state()
                result["operation_message"] = "Category already matches this exact target; nothing changed."
                return result
            event = self.category_event(**identity, assigned=payload["assigned"])
            data["category_assignments"] = [item for item in assignments if item not in matches]
            if payload["assigned"]:
                data["category_assignments"].append(dict(event))
            data.setdefault("category_history", []).append(event)
            return self.write_feedback(data)

    def save_frame_group(self, payload):
        index = payload.get("video")
        video = self.video(index)
        action = payload.get("action")
        if action not in ("create", "ungroup", "merge", "renumber", "configure", "membership"):
            raise ValueError("Unknown frame group action")
        if action == "create":
            frames = payload.get("frames")
            if not isinstance(frames, list) or len(frames) < 2:
                raise ValueError("A group needs at least two exact frames")
            for frame in frames:
                if not self.frame(index, frame).is_file():
                    raise ValueError("Group source frame is unavailable")
            if len(set(frames)) != len(frames):
                raise ValueError("Duplicate group members are not allowed")
            kind, name = payload.get("type"), payload.get("name", "")
            if kind not in ("animation", "same_scene"):
                raise ValueError("Choose Animation or Same scene")
            if (not isinstance(name, str) or len(name) > 120
                    or any(unicodedata.category(char) == "Cc" for char in name)):
                raise ValueError("Group name must be at most 120 characters without control characters")
            selected_unit = payload.get("selected_unit", False)
            if not isinstance(selected_unit, bool):
                raise ValueError("Expected a boolean selected_unit")
        elif action == "merge":
            frame = payload.get("frame")
            if type(frame) is not int or not self.frame(index, frame).is_file():
                raise ValueError("The exact source frame is unavailable")
            if not isinstance(payload.get("id"), str):
                raise ValueError("Choose an existing group in this recording")
        elif action == "membership":
            operation = payload.get("member_action")
            frame = payload.get("frame")
            if operation not in ("move", "remove"):
                raise ValueError("Choose Move or Remove from group")
            if type(frame) is not int or not self.frame(index, frame).is_file():
                raise ValueError("The exact source frame is unavailable")
            if not isinstance(payload.get("source_id"), str) or not payload["source_id"]:
                raise ValueError("Choose the existing source group")
            if operation == "move":
                if not isinstance(payload.get("destination_id"), str) or not payload["destination_id"]:
                    raise ValueError("Choose an existing destination group")
            elif "destination_id" in payload:
                raise ValueError("Remove from group does not accept a destination")
            if type(payload.get("dissolve_ack", False)) is not bool:
                raise ValueError("Dissolution acknowledgement must be an explicit boolean")
        elif action in ("renumber", "configure"):
            if not isinstance(payload.get("id"), str):
                raise ValueError("Expected a group id")
            number = payload.get("display_number")
            if type(number) is not int or number < 1:
                raise ValueError("Group number must be a positive whole number")
            if action == "configure":
                color = payload.get("color")
                if not self.valid_group_color(color):
                    raise ValueError("Group color must be a six-digit #RRGGBB value")
        else:
            if not isinstance(payload.get("id"), str):
                raise ValueError("Expected a group id")
        # Groups are metadata, including on retained-only archives. Never route
        # through annotate(): neither membership nor ungrouping changes labels.
        with self.lock, self.dataset.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data, revision = self.read()
            if payload.get("revision") != revision:
                raise ConflictError("Feedback changed in another window. Reload saved decisions, then retry.")
            groups = data.setdefault("frame_groups", [])
            numbered = self.numbered_groups(groups)
            # Number ambiguity must be repaired explicitly before another group
            # operation can rely on a destination number.
            if action in ("create", "merge"):
                self._assert_group_numbers_valid(groups, video["name"])
            if action == "create":
                if any(not self.frame(index, frame).is_file() for frame in frames):
                    raise ValueError("Group source frame is unavailable")
                numbered_for_recording = [g for g in numbered if g["video"] == video["name"]]
                used = {g["display_number"] for g in numbered_for_recording}
                display_number = 1
                while display_number in used:
                    display_number += 1
                trashed = {entry["frame"] for entry in self.state()["trashed"]
                           if entry["video"] == video["name"]}
                if trashed.intersection(frames):
                    raise ValueError("Restore trashed members before creating a group")
                if selected_unit:
                    if any(g.get("selected_unit") is True and g["video"] == video["name"]
                           and set(g["frames"]).intersection(frames) for g in groups):
                        raise ValueError("Ungroup existing selected units before regrouping their members")
                groups.append({
                    "id": secrets.token_hex(16), "video": video["name"],
                    "frames": sorted(frames), "type": kind, "name": name.strip(),
                    "display_number": display_number,
                    "reviewer": self.reviewer,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "method": "explicit_dashboard_group",
                    **({"selected_unit": True} if selected_unit else {}),
                })
            elif action == "merge":
                group = self._group_for_recording(groups, payload["id"], video["name"])
                frame = payload["frame"]
                if not self.frame(index, frame).is_file():
                    raise ValueError("The exact source frame is unavailable")
                if frame in {entry["frame"] for entry in self.state()["trashed"]
                             if entry["video"] == video["name"]}:
                    raise ValueError("Restore this trashed frame before merging it; no group changed.")
                if frame in group["frames"]:
                    group_number = next(item["display_number"] for item in numbered
                                        if item.get("id") == group.get("id"))
                    result = self.state()
                    result["operation_message"] = (
                        f"Frame {frame} is already in Group {group_number}; nothing changed."
                    )
                    return result
                if group.get("selected_unit") is True:
                    other_unit = next((g for g in groups
                                       if g is not group and g.get("video") == video["name"]
                                       and g.get("selected_unit") is True and frame in g["frames"]), None)
                    if other_unit:
                        other_number = next((item["display_number"] for item in numbered
                                             if item.get("id") == other_unit.get("id")), "?")
                        raise ValueError(
                            f"Frame {frame} belongs to selected Group {other_number}. "
                            "Ungroup that unit first or choose a metadata group; no membership changed."
                        )
                group["frames"] = sorted(set(group["frames"]) | {frame})
            elif action == "membership":
                source = self._group_for_recording(groups, payload["source_id"], video["name"])
                frame = payload["frame"]
                source_frames = source.get("frames")
                if (not isinstance(source_frames, list)
                        or any(type(member) is not int for member in source_frames)):
                    raise ValueError("The source group membership is invalid. Reload saved decisions.")
                if frame not in source_frames:
                    raise ValueError(
                        f"Frame {frame} is no longer a member of that source group. "
                        "Reload saved decisions before correcting membership."
                    )
                if not self.frame(index, frame).is_file():
                    raise ValueError("The exact source frame is unavailable")
                if frame in {entry["frame"] for entry in self.state()["trashed"]
                             if entry["video"] == video["name"]}:
                    raise ValueError("Restore this trashed frame before changing group membership.")
                destination = None
                already_in_destination = False
                if payload["member_action"] == "move":
                    destination = self._group_for_recording(
                        groups, payload["destination_id"], video["name"])
                    if destination is source:
                        raise ValueError("Choose a different destination group from the source group.")
                    destination_frames = destination.get("frames")
                    if (not isinstance(destination_frames, list)
                            or any(type(member) is not int for member in destination_frames)):
                        raise ValueError("The destination group membership is invalid. Reload saved decisions.")
                    already_in_destination = frame in destination_frames
                    if destination.get("selected_unit") is True:
                        third_party_unit = next((
                            group for group in groups
                            if group is not source and group is not destination
                            and group.get("video") == video["name"]
                            and group.get("selected_unit") is True
                            and isinstance(group.get("frames"), list)
                            and frame in group["frames"]
                        ), None)
                        if third_party_unit:
                            raise ValueError(
                                f"Frame {frame} also belongs to another selected unit. "
                                "Resolve that overlap first; no membership changed."
                            )
                needs_dissolution = len(set(source_frames) - {frame}) < 2
                source_number = next(
                    (item["display_number"] for item in numbered
                     if item.get("id") == source.get("id")), "?")
                if needs_dissolution and payload.get("dissolve_ack") is not True:
                    raise ValueError(
                        f"Removing frame {frame} would leave fewer than two members in "
                        f"Group {source_number}. "
                        "Confirm dissolving only that source group, or cancel; no membership changed."
                    )
                if not needs_dissolution and payload.get("dissolve_ack") is True:
                    raise ValueError("This change does not dissolve the source group; reload and try again.")
                if (not needs_dissolution
                        and not isinstance(source.get("membership_history", []), list)):
                    raise ValueError("The source group correction history is invalid. Reload saved decisions.")
                if (destination is not None and not already_in_destination
                        and not isinstance(destination.get("membership_history", []), list)):
                    raise ValueError("The destination group correction history is invalid. Reload saved decisions.")
                correction_log = data.get("frame_group_membership_history", [])
                if not isinstance(correction_log, list):
                    raise ValueError("The membership correction history is invalid. Reload saved decisions.")
                recorded_at = datetime.now(timezone.utc).isoformat()
                correction = {
                    "action": payload["member_action"],
                    "frame": frame,
                    "reviewer": self.reviewer,
                    "recorded_at": recorded_at,
                    "method": "explicit_dashboard_membership_correction",
                }
                if destination is not None:
                    correction["destination_group_id"] = destination["id"]
                    correction["destination_frame_already_present"] = already_in_destination
                data.setdefault("frame_group_membership_history", []).append({
                    "action": payload["member_action"],
                    "frame": frame,
                    "source_group_id": source["id"],
                    "destination_group_id": destination["id"] if destination is not None else None,
                    "source_dissolved": needs_dissolution,
                    "reviewer": self.reviewer,
                    "recorded_at": recorded_at,
                    "method": "explicit_dashboard_membership_correction",
                })
                if needs_dissolution:
                    self.clear_group_categories(data, source)
                    groups.remove(source)
                    operation_message = (
                        f"Group {source_number} dissolved as confirmed; "
                        f"frame {frame} was {'moved' if destination is not None else 'removed'} "
                        "from that group. Other memberships and frame decisions are unchanged."
                    )
                else:
                    source["frames"] = sorted(set(source_frames) - {frame})
                    source.setdefault("membership_history", []).append(correction)
                    operation_message = (
                        f"Frame {frame} was {'moved' if destination is not None else 'removed'} "
                        "from the source group."
                    )
                if destination is not None and not already_in_destination:
                    destination["frames"] = sorted(set(destination_frames) | {frame})
                    destination.setdefault("membership_history", []).append(correction)
                elif destination is not None:
                    operation_message += " The destination already contained it, so no duplicate was added."
            elif action in ("renumber", "configure"):
                group = self._group_for_recording(groups, payload["id"], video["name"])
                other_numbers = self._assert_group_numbers_valid(
                    groups, video["name"], exclude_id=group["id"])
                if payload["display_number"] in other_numbers:
                    raise ValueError(
                        f"Group {payload['display_number']} already exists in this recording. "
                        "Choose an unused positive whole number."
                    )
                group["display_number"] = payload["display_number"]
                if action == "configure":
                    group["color"] = color.upper()
            else:
                group = self._group_for_recording(groups, payload["id"], video["name"])
                remaining = [item for item in groups if item is not group]
                remaining_numbered = self.numbered_groups(remaining)
                recording_numbers = [item for item in remaining_numbered
                                     if item.get("video") == video["name"]]
                if not any(item.get("_display_number_issue") for item in recording_numbers):
                    by_id = {item.get("id"): item["display_number"] for item in recording_numbers}
                    for item in remaining:
                        if item.get("video") == video["name"] and "display_number" not in item:
                            item["display_number"] = by_id[item.get("id")]
                self.clear_group_categories(data, group)
                groups.remove(group)
            result = self.write_feedback(data)
            if action == "membership":
                result["operation_message"] = operation_message
            return result

    def write_feedback(self, data):
        staging = self.dataset.with_suffix(".writing")
        with staging.open("w") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        staging.replace(self.dataset)
        saved, _ = self.read()
        if saved != data:
            raise OSError("Feedback readback did not match")
        return self.state()

    def cleanup_preview(self, index):
        video = self.video(index)
        if video["retention_only"]:
            raise ValueError("Retained-only archives do not allow visibility changes")
        state = self.state()
        name = video["name"]
        trashed = {item["frame"] for item in state["trashed"] if item["video"] == name}
        latest = {}
        for label in sorted(state["labels"], key=lambda item: item["recorded_at"]):
            if label["video"] == name:
                latest[label["frame"]] = label["label"]
        selected = {frame for frame, label in latest.items() if label == "new_state"}
        for group in state["frame_groups"]:
            if group["video"] == name and group.get("selected_unit") is True:
                selected.update(group["frames"])
        if any(type(frame) is not int or not 1 <= frame <= video["frame_count"]
               for frame in selected | trashed):
            raise ValueError("Saved selection/Trash references are outside this recording. Inspect saved decisions before cleanup.")
        selected.difference_update(trashed)
        result = {
            "video": index, "name": name, "revision": state["revision"],
            "source_count": video["frame_count"], "selected_count": len(selected),
            "existing_trash_count": len(trashed),
            "eligible_count": video["frame_count"] - len(selected) - len(trashed),
        }
        # A session-bound proof of the exact recording/revision/count preview.
        result["preview_token"] = hmac.new(
            self.token.encode(), json.dumps(result, sort_keys=True).encode(),
            hashlib.sha256).hexdigest()
        return result, selected

    def cleanup(self, payload):
        if payload.get("action") not in ("preview", "apply"):
            raise ValueError("Choose preview or apply for standalone cleanup")
        with self.lock, self.dataset.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            preview, selected = self.cleanup_preview(payload.get("video"))
            if payload.get("revision") != preview["revision"]:
                raise ConflictError("Decisions changed. Preview the cleanup again before confirming.")
            if payload["action"] == "preview":
                return preview
            if payload.get("confirm") is not True:
                raise ValueError("Confirm the exact recording and cleanup preview first")
            token = payload.get("preview_token")
            if not isinstance(token, str) or not hmac.compare_digest(token, preview["preview_token"]):
                raise ConflictError("Cleanup preview is invalid or stale. Preview again.")
            moved = preview["eligible_count"]
            if moved:
                data, revision = self.read()
                if revision != preview["revision"]:
                    raise ConflictError("Decisions changed. Preview again.")
                # Retain old Trash in the new window by omitting it from keep_frames.
                # No per-frame cases, labels, groups or original media are edited.
                data.setdefault("curated_windows", []).append({
                    "video": preview["name"], "first_frame": 1,
                    "last_frame": preview["source_count"],
                    "end_time_seconds": self.video(payload["video"])["times"][-1],
                    "keep_frames": sorted(selected),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "reviewer": self.reviewer, "method": "explicit_gallery_cleanup",
                    "policy": "Recoverable visibility only; protect nontrashed Keep points and selected-unit members. No label changes or original deletion.",
                    "moved_count": moved,
                })
                result = self.write_feedback(data)
            else:
                result = self.state()
            result["cleanup_result"] = {**preview, "moved_count": moved}
            result["operation_message"] = (
                f"Moved {moved} unselected frames to recoverable Trash in {preview['name']}. "
                f"Preserved {preview['selected_count']} selected frames and "
                f"{preview['existing_trash_count']} existing Trash frames. Originals unchanged."
            )
            return result

    def similar_frames(self, index):
        """All-pairs pixel scan, bounded memory and an explicit whole-set limit."""
        graph = self.diagram(index)
        nodes = graph["nodes"]
        if len(nodes) > 1000:
            raise ValueError("Similarity review supports up to 1,000 individual Keep frames per recording; no frames were sampled or changed.")
        images, hashes = {}, {}
        for node in nodes:
            raw = self.frame(index, node["frame"]).read_bytes()
            hashes[str(node["frame"])] = sha(raw)
            with Image.open(io.BytesIO(raw)) as image:
                rgb = image.convert("RGB")
                images[node["frame"]] = (rgb.resize((16, 32)), rgb.resize((96, 192)), rgb.size)
        best, candidates, compared = [], 0, 0
        # Compare across block boundaries too; never truncate to the first block.
        for block in range(0, len(nodes), 64):
            for position in range(block, min(block + 64, len(nodes))):
                left = nodes[position]
                a = images[left["frame"]]
                for right in nodes[position + 1:]:
                    compared += 1
                    b = images[right["frame"]]
                    if a[2] != b[2]:
                        continue
                    coarse = sum(ImageStat.Stat(ImageChops.difference(a[0], b[0])).mean) / 3
                    if coarse > 10:
                        continue
                    difference = sum(ImageStat.Stat(ImageChops.difference(a[1], b[1])).mean) / 3
                    if difference > 5:
                        continue
                    pair = {"left": left, "right": right, "difference": round(difference, 6),
                            "identical_file": hashes[str(left["frame"])] == hashes[str(right["frame"])]}
                    candidates += 1
                    entry = (-pair["difference"], -compared, pair)
                    if len(best) < 40:
                        heapq.heappush(best, entry)
                    elif entry[:2] > best[0][:2]:
                        heapq.heapreplace(best, entry)
        if self.read()[1] != graph["revision"]:
            raise ConflictError("Decisions changed during similarity scan. Refresh and scan again.")
        pairs = [entry[2] for entry in sorted(best, key=lambda entry: (-entry[0], -entry[1]))]
        return {"video": index, "revision": graph["revision"], "hashes": hashes,
                "pairs": pairs, "candidate_pairs": candidates, "shown": len(pairs),
                "selected_count": len(nodes), "compared_pairs": compared,
                "method": f"Compared all {compared} pairs across {len(nodes)} individual Keep frames. Mean RGB difference <= 5/255 on 96x192 previews (16x32 prefilter <= 10/255). Pixel candidates only, not semantic duplicates; no decisions changed."}

    def annotate(self, payload):
        index, first = payload.get("video"), payload.get("frame")
        v = self.video(index)
        if v["retention_only"]:
            raise ValueError("This completed recording is a retained-only, read-only archive after permanent cleanup")
        self.frame(index, first)
        last = payload.get("end_frame", first)
        self.frame(index, last)
        label, note = payload.get("label"), payload.get("note", "")
        action = payload.get("action", "label")
        if action not in ("label", "trash", "restore"):
            raise ValueError("Unknown dashboard action")
        if action != "label" and first != last:
            raise ValueError("Trash actions apply to individual frames")
        if action == "label" and (last < first or label not in LABELS or
                                  (last != first and label == "new_state")):
            raise ValueError("Invalid label or interval; keep labels apply to individual frames")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("Note must contain at most 2,000 characters")
        with self.lock, self.dataset.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data, revision = self.read()
            if payload.get("revision") != revision:
                raise ConflictError("Feedback changed in another window. Reload saved decisions, then retry.")
            event = {
                "label": label, "reviewer": self.reviewer, "statement": note,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "method": "explicit_dashboard_action",
            }
            if first == last:
                case_id = f"dashboard-v{index + 1}-frame-{first:06d}"
                case = next((c for c in data["cases"] if c["id"] == case_id), None)
                if case is None:
                    case = {"id": case_id, "video": v["name"],
                            "split": data["split_by_video"][v["name"]],
                            "context": self.context(index, first), "label_history": []}
                    data["cases"].append(case)
                if action == "label":
                    if any(t["video"] == v["name"] and t["frame"] == first
                           for t in self.state()["trashed"]):
                        raise ValueError("Restore this frame from Trash before labeling it")
                    case["label_history"].append(event)
                else:
                    case.setdefault("trash_history", []).append({
                        "trashed": action == "trash", "reviewer": self.reviewer,
                        "recorded_at": event["recorded_at"],
                        "method": "explicit_dashboard_action",
                    })
            else:
                interval_id = f"dashboard-v{index + 1}-range-{first:06d}-{last:06d}"
                intervals = data.setdefault("intervals", [])
                interval = next((r for r in intervals if r["id"] == interval_id), None)
                if interval is None:
                    interval = {"id": interval_id, "video": v["name"],
                                "split": data["split_by_video"][v["name"]],
                                "first_frame": first, "last_frame": last,
                                "start_time": v["times"][first - 1], "end_time": v["times"][last - 1],
                                "endpoint_sha256": [sha(self.frame(index, n).read_bytes())
                                                    for n in (first, last)],
                                "point_override_policy": "Individual frame decisions take priority",
                                "label_history": []}
                    intervals.append(interval)
                interval["label_history"].append(event)
            data["status"] = "human_labeling_in_progress"
            return self.write_feedback(data)

    @lru_cache(maxsize=160)
    def image(self, index, frame, width):
        path = self.frame(index, frame)
        if width == 0:
            return path.read_bytes()
        with Image.open(path) as image:
            image.thumbnail((width, width * 3))
            output = io.BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=85)
            return output.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if args and str(args[1]) not in ("200", "206", "304"):
            LOG.info(fmt, *args)

    @property
    def store(self):
        return self.server.store

    def allowed(self):
        return self.headers.get("Host") == self.server.authority

    def send(self, status, body, mime="application/json"):
        if status >= 400:
            LOG.warning("http_error %s", json.dumps({
                "status": status, "method": self.command,
                "route": urlsplit(self.path).path,
                "error": body.get("error") if isinstance(body, dict) else None,
            }))
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if mime != "image/jpeg" else "private, max-age=300")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        artifact = urlsplit(self.path).path == "/workspace-artifact"
        self.send_header("Content-Security-Policy",
                         ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
                          "frame-ancestors 'self'; base-uri 'none'; form-action 'none'") if artifact else
                         "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' blob: data:; media-src 'self'; connect-src 'self'; frame-src 'self'; "
                         "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def preview(self):
        path = self.store.preview
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        value = self.headers.get("Range")
        if value:
            import re
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", value)
            if not match:
                raise ValueError("Unsupported media range")
            start = int(match[1])
            end = min(int(match[2]) if match[2] else end, size - 1)
            if start > end:
                self.send(416, {"error": "Media range outside preview"})
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self):
        if not self.allowed():
            self.send(403, {"error": "Local host mismatch"})
            return
        try:
            route = urlsplit(self.path)
            if route.path in ("/", "/app.js", "/enhancements.js", "/workspace.js", "/learning.js", "/style.css"):
                file, mime = {"/": ("index.html", "text/html; charset=utf-8"),
                              "/app.js": ("app.js", "text/javascript"),
                              "/enhancements.js": ("enhancements.js", "text/javascript"),
                              "/workspace.js": ("workspace.js", "text/javascript"),
                              "/learning.js": ("learning.js", "text/javascript"),
                              "/style.css": ("style.css", "text/css")}[route.path]
                self.send(200, (ASSETS / file).read_bytes(), mime)
            elif route.path == "/api/workspace":
                self.send(200, self.server.workspace.library())
            elif route.path == "/api/learning":
                query = parse_qs(route.query, strict_parsing=True)
                self.send(200, {"preview": learning_module.preview(self.store, int(query["video"][0])),
                                "history": learning_module.history(self.server.workspace.project)})
            elif route.path == "/api/learning/similar":
                query = parse_qs(route.query, strict_parsing=True)
                self.send(200, self.store.similar_frames(int(query["video"][0])))
            elif route.path == "/api/workspace/collection":
                query = parse_qs(route.query, strict_parsing=True)
                self.send(200, self.server.workspace.collection(query["run"][0], int(query["video"][0])))
            elif route.path == "/workspace-artifact":
                query = parse_qs(route.query, strict_parsing=True)
                self.send(200, self.server.workspace.artifact(query["name"][0]), "text/html; charset=utf-8")
            elif route.path == "/workspace-file":
                query = parse_qs(route.query, strict_parsing=True)
                path, mime = self.server.workspace.file(query["name"][0])
                if path.suffix == ".html":
                    raise ValueError("Open diagrams through the library")
                self.send(200, path.read_bytes(), mime)
            elif route.path == "/api/jira/types":
                query = parse_qs(route.query, strict_parsing=True)
                self.send(200, self.server.workspace.types(query["project"][0]))
            elif route.path == "/api/state":
                self.send(200, self.store.state())
            elif route.path == "/api/videos":
                self.send(200, self.store.videos)
            elif route.path == "/api/diagram":
                query = parse_qs(route.query, strict_parsing=True)
                self.send(200, self.store.diagram(int(query["video"][0])))
            elif route.path == "/api/diagnostics":
                path = self.server.log_path
                entries = path.read_text().splitlines()[-100:] if path and path.exists() else []
                self.send(200, {"log_path": str(path) if path else None, "entries": entries,
                                "source_directory": str(ASSETS)})
            elif route.path == "/api/export":
                data, revision = self.store.read()
                exported = dict(data)
                if "frame_groups" in data:
                    exported["frame_groups"] = self.store.numbered_groups(data["frame_groups"])
                self.send(200, {"revision": revision, "dataset": exported})
            elif route.path == "/api/frame":
                query = parse_qs(route.query, strict_parsing=True)
                width = int(query.get("width", ["660"])[0])
                if width not in (0, 160, 660):
                    raise ValueError("Unsupported preview size")
                self.send(200, self.store.image(int(query["video"][0]), int(query["frame"][0]), width),
                          "image/jpeg")
            elif route.path == "/preview":
                self.preview()
            else:
                self.send(404, {"error": "Not found"})
        except ConflictError as error:
            self.send(409, {"error": str(error)})
        except (ValueError, KeyError, IndexError) as error:
            self.send(400, {"error": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError:
            LOG.exception("Cannot read local source or dataset")
            self.send(503, {"error": "Cannot read local source or dataset. Check the external drive and server log."})

    def do_POST(self):
        if not self.allowed() or self.headers.get("Origin") != f"http://{self.server.authority}":
            self.send(403, {"error": "Only the local dashboard can save feedback"})
            return
        if self.headers.get("X-Review-Token") != self.store.token:
            self.send(403, {"error": "Invalid review session; reload the dashboard"})
            return
        if self.path not in ("/api/annotation", "/api/frame-group", "/api/category", "/api/cleanup", "/api/client-error", "/api/workspace/save",
                             "/api/jira/connect", "/api/jira/disconnect", "/api/jira/submit", "/api/learning/run"):
            self.send(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            limit = 34_000_000 if self.path == "/api/workspace/save" else (65536 if self.path in ("/api/learning/run", "/api/frame-group") else 8192)
            if not 0 < length <= limit:
                raise ValueError("Invalid feedback size")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Expected JSON feedback")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Expected a feedback object")
            if self.path == "/api/frame-group":
                self.send(200, self.store.save_frame_group(payload))
            elif self.path == "/api/cleanup":
                self.send(200, self.store.cleanup(payload))
            elif self.path == "/api/category":
                self.send(200, self.store.save_category(payload))
            elif self.path == "/api/workspace/save":
                self.send(200, self.server.workspace.save(payload))
            elif self.path == "/api/learning/run":
                with self.server.workspace.lock:
                    result = learning_module.run(self.store, self.server.workspace.project, payload)
                self.send(200, result)
            elif self.path == "/api/jira/connect":
                self.send(200, self.server.workspace.connect(payload))
            elif self.path == "/api/jira/disconnect":
                with self.server.workspace.lock:
                    self.server.workspace.connection = None
                self.send(200, {"connected": False})
            elif self.path == "/api/jira/submit":
                self.send(200, self.server.workspace.submit(payload))
            elif self.path == "/api/client-error":
                message = payload.get("message")
                if not isinstance(message, str) or not 0 < len(message) <= 2000:
                    raise ValueError("Expected a bounded error message")
                # Record messages only, never headers, tokens, notes or request bodies.
                LOG.error("browser_error %s", json.dumps({"message": message}))
                self.send(200, {"logged": True})
            else:
                self.send(200, self.store.annotate(payload))
        except ConflictError as error:
            self.send(409, {"error": str(error)})
        except (ValueError, KeyError, TypeError) as error:
            self.send(400, {"error": str(error)})
        except OSError:
            LOG.exception("Cannot persist human feedback")
            self.send(503, {"error": "Feedback was not confirmed saved. Check disk access, then reload saved decisions."})


class ReviewServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        LOG.exception("Unhandled local dashboard request error")


def create_server(store, port, log_path=None):
    server = ReviewServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.store = store
    server.workspace = workspace_module.Workspace(store, PROJECT)
    server.log_path = log_path
    server.authority = f"127.0.0.1:{server.server_port}"
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--archive", type=Path, default=ARCHIVE,
        help="Local frame archive containing manifest.json (defaults to the configured archive)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    configure_logging()
    try:
        server = create_server(Store(archive=args.archive), args.port, LOG_PATH)
    except (OSError, ValueError):
        LOG.exception("Dashboard startup failed; check source drive, dataset and port")
        raise
    print(f"REVIEW_URL=http://{server.authority}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
