import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


def _inside(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _recording_slug(value):
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", normalized).strip(" ._-")
    if not slug:
        raise ValueError("Recording name must contain at least one letter or number.")
    return slug


def _collect_inputs(args, input_fn=input):
    source_value = args.source or input("Path to the source video: ").strip()
    destination_value = args.destination or input(
        "Path to an existing local parent folder for the video and frames: "
    ).strip()
    name_value = args.name or input("Name for this recording folder: ").strip()
    if not source_value or not destination_value or not name_value:
        raise ValueError("Source path, destination folder, and recording name are required.")

    source = Path(source_value).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError(f"Source video is not a file: {source}")
    destination = Path(destination_value).expanduser().resolve(strict=True)
    if not destination.is_dir():
        raise ValueError(f"Destination is not a directory: {destination}")

    project = PROJECT.resolve()
    if _inside(source, project):
        raise ValueError("Keep source videos outside the repository.")
    if _inside(destination, project):
        raise ValueError("Choose a destination outside the repository; media is never stored in Git.")
    return source, destination, _recording_slug(name_value)


def extract_recording(source, destination, recording_name, now=None):
    source = source.resolve(strict=True)
    destination = destination.resolve(strict=True)
    project = PROJECT.resolve()
    if not source.is_file():
        raise ValueError(f"Source video is not a file: {source}")
    if not destination.is_dir():
        raise ValueError(f"Destination is not a directory: {destination}")
    if _inside(source, project):
        raise ValueError("Keep source videos outside the repository.")
    if _inside(destination, project):
        raise ValueError("Choose a destination outside the repository; media is never stored in Git.")

    slug = _recording_slug(recording_name)
    timestamp = (now or datetime.now().astimezone()).strftime("%Y%m%dT%H%M%S")
    folder = destination / f"{timestamp}_{slug}"
    source_stat = source.stat()
    extraction_started_at = datetime.now().astimezone().isoformat()
    metadata = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,nb_frames,avg_frame_rate:format=duration",
        "-of", "json", str(source),
    ]))
    if not metadata.get("streams"):
        raise ValueError("The selected file has no video stream.")
    stream = metadata["streams"][0]
    ffmpeg_version = subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0]

    folder.mkdir(exist_ok=False)
    frames_folder = folder / "frames"
    frames_folder.mkdir()
    copied_source = folder / f"source{source.suffix}"
    try:
        print(f"Copying {source.name} and extracting frames into {folder}", flush=True)
        shutil.copy2(source, copied_source)
        copied_stat = copied_source.stat()
        if copied_stat.st_size != source_stat.st_size:
            raise RuntimeError("The local video copy does not match the source file size.")
        if (source.stat().st_size, source.stat().st_mtime_ns) != (
            source_stat.st_size, source_stat.st_mtime_ns
        ):
            raise RuntimeError("Source changed while it was being copied; extraction was stopped.")

        subprocess.run([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(source),
            "-map", "0:v:0", "-fps_mode", "passthrough", "-q:v", "2",
            str(frames_folder / "frame_%06d.jpg"),
        ], check=True)
        probe = subprocess.Popen([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
            "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", str(source),
        ], stdout=subprocess.PIPE, text=True)
        count = 0
        with (frames_folder / "frame_timestamps.csv").open("w") as output:
            writer = csv.writer(output)
            writer.writerow(["frame_number", "timestamp_seconds"])
            previous_timestamp = None
            invalid_timestamps = False
            for line in probe.stdout:
                value = line.strip().split(",")[0]
                try:
                    timestamp_seconds = float(value)
                except ValueError:
                    continue
                if (not math.isfinite(timestamp_seconds) or timestamp_seconds < 0
                        or (previous_timestamp is not None
                            and timestamp_seconds <= previous_timestamp)):
                    invalid_timestamps = True
                    continue
                previous_timestamp = timestamp_seconds
                count += 1
                writer.writerow([count, f"{timestamp_seconds:.6f}"])
        if probe.wait() != 0:
            raise RuntimeError("Timestamp extraction failed.")
        if invalid_timestamps:
            raise RuntimeError("Source timestamps are invalid or out of order.")
        images = sorted(frames_folder.glob("frame_*.jpg"))
        if len(images) != count or not count:
            raise RuntimeError(
                f"Image/timestamp mismatch: {len(images)} images, {count} timestamps."
            )
        if any(image.name != f"frame_{number:06d}.jpg"
               for number, image in enumerate(images, 1)):
            raise RuntimeError("Extracted filenames are not sequential.")
        if (source.stat().st_size, source.stat().st_mtime_ns) != (
            source_stat.st_size, source_stat.st_mtime_ns
        ):
            raise RuntimeError("Source changed during extraction; refusing registration.")

        entry = {
            "photos_album": "soda",
            "recording_name": recording_name,
            "source_filename": source.name,
            "copied_source_filename": copied_source.name,
            "source_path": str(source),
            "source_size_bytes": source_stat.st_size,
            "duration_seconds": float(metadata["format"]["duration"]),
            "codec": stream["codec_name"],
            "width": stream["width"],
            "height": stream["height"],
            "reported_frame_count": (
                int(stream["nb_frames"])
                if stream.get("nb_frames", "").isdigit() else None
            ),
            "decoded_frame_count": count,
            "extracted_frame_count": len(images),
            "average_frame_rate": stream["avg_frame_rate"],
            "output_directory": "frames",
            "image_format": "JPEG, ffmpeg q:v 2",
            "output_size_bytes": sum(image.stat().st_size for image in images),
            "timestamps_file": "frame_timestamps.csv",
            "extraction_started_at": extraction_started_at,
            "extraction_completed_at": datetime.now().astimezone().isoformat(),
            "ffmpeg_version": ffmpeg_version,
        }
        manifest = {"videos": [entry], "total_frames": count}
        staging = folder / "manifest.pending.json"
        manifest_path = folder / "manifest.json"
        staging.write_text(json.dumps(manifest, indent=2) + "\n")
        staging.replace(manifest_path)
        if json.loads(manifest_path.read_text()) != manifest:
            raise RuntimeError("Manifest readback mismatch.")
        print(json.dumps(entry, indent=2), flush=True)
        print(f"Archive total: {count} frames at {folder}.", flush=True)
        return folder
    except Exception:
        print(
            f"Extraction failed. Incomplete local output remains at {folder}; "
            "the source video was not modified.",
            flush=True,
        )
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Copy a local video and extract its frames into a new dated folder."
    )
    parser.add_argument("--source", type=Path, help="Source video path; prompted when omitted")
    parser.add_argument(
        "--destination", "--archive", dest="destination", type=Path,
        help="Existing local parent folder; a new recording folder is created inside it",
    )
    parser.add_argument("--name", help="Recording name; prompted when omitted")
    args = parser.parse_args()
    source, destination, recording_name = _collect_inputs(args)
    extract_recording(source, destination, recording_name)


if __name__ == "__main__":
    main()
