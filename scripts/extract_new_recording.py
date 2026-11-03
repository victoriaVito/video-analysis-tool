import argparse
import csv
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True,
                        help="Existing destination archive containing manifest.json")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    root = args.archive.resolve(strict=True)
    folder = root / source.stem.removesuffix("_1").replace(" ", "_")
    source_stat = source.stat()
    started_at = datetime.now(timezone.utc).isoformat()
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if any(video["source_filename"] == source.name for video in manifest["videos"]):
        raise RuntimeError("Recording is already registered; refusing duplicate extraction.")
    metadata = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,nb_frames,avg_frame_rate:format=duration",
        "-of", "json", str(source),
    ]))
    stream = metadata["streams"][0]
    folder.mkdir(exist_ok=False)
    print(f"Extracting {source.name} into {folder}", flush=True)
    subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-map", "0:v:0", "-fps_mode", "passthrough", "-q:v", "2",
        str(folder / "frame_%06d.jpg"),
    ], check=True)
    probe = subprocess.Popen([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
        "-show_entries", "frame=best_effort_timestamp_time", "-of", "csv=p=0", str(source),
    ], stdout=subprocess.PIPE, text=True)
    count = 0
    with (folder / "frame_timestamps.csv").open("w") as output:
        writer = csv.writer(output)
        writer.writerow(["frame_number", "timestamp_seconds"])
        for line in probe.stdout:
            value = line.strip().split(",")[0]
            try:
                timestamp = float(value)
            except ValueError:
                continue
            count += 1
            writer.writerow([count, f"{timestamp:.6f}"])
    if probe.wait() != 0:
        raise RuntimeError("Timestamp extraction failed.")
    images = sorted(folder.glob("frame_*.jpg"))
    if len(images) != count or not count:
        raise RuntimeError(f"Image/timestamp mismatch: {len(images)} images, {count} timestamps.")
    if any(image.name != f"frame_{number:06d}.jpg" for number, image in enumerate(images, 1)):
        raise RuntimeError("Extracted filenames are not sequential.")
    final_stat = source.stat()
    if (final_stat.st_size, final_stat.st_mtime_ns) != (source_stat.st_size, source_stat.st_mtime_ns):
        raise RuntimeError("Source changed during extraction; refusing registration.")
    entry = {
        "photos_album": "soda", "source_filename": source.name,
        "source_path": str(source),
        "source_size_bytes": source_stat.st_size,
        "duration_seconds": float(metadata["format"]["duration"]),
        "codec": stream["codec_name"], "width": stream["width"], "height": stream["height"],
        "reported_frame_count": int(stream["nb_frames"]) if stream.get("nb_frames", "").isdigit() else None,
        "decoded_frame_count": count, "extracted_frame_count": len(images),
        "average_frame_rate": stream["avg_frame_rate"], "output_directory": folder.name,
        "image_format": "JPEG, ffmpeg q:v 2",
        "output_size_bytes": sum(image.stat().st_size for image in images),
        "timestamps_file": "frame_timestamps.csv",
        "extraction_started_at": started_at,
        "extraction_completed_at": datetime.now(timezone.utc).isoformat(),
        "ffmpeg_version": subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0],
    }
    latest = json.loads(manifest_path.read_text())
    if latest != manifest:
        raise RuntimeError("Manifest changed during extraction; refusing to overwrite it.")
    manifest["videos"].append(entry)
    manifest["total_frames"] = sum(video["extracted_frame_count"] for video in manifest["videos"])
    staging = root / "manifest.pending.json"
    staging.write_text(json.dumps(manifest, indent=2) + "\n")
    staging.replace(manifest_path)
    if json.loads(manifest_path.read_text()) != manifest:
        raise RuntimeError("Manifest readback mismatch.")
    print(json.dumps(entry, indent=2), flush=True)
    print(f"Archive total: {manifest['total_frames']} frames.", flush=True)


if __name__ == "__main__":
    main()
