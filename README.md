# Video Analysis Tool

Local video frame review, screenshot selection, annotations, diagrams and
selection calibration, extracted from the EoC research project on 2026-10-30.

This repository contains an isolated snapshot of the video review tool. It
includes only tool files, with no source media, local review data or original
repository history.

## Run the existing reviewer

From this directory, use the existing Pillow-capable Python:

```sh
python3 tools/soda-frame-review/server.py --port 8767
```

Open `http://127.0.0.1:8767/`. Port 8767 avoids the original reviewer's usual
port 8766. No service is started automatically.

For a visual walkthrough, see the
[illustrated tutorial PDF](docs/video-analysis-tool-tutorial.pdf).

Set `SODA_FRAME_ARCHIVE` to the directory containing the source-frame archive
before starting the reviewer:

```sh
export SODA_FRAME_ARCHIVE="/path/to/soda-video-frames"
```

Install Pillow in the Python environment used to run the reviewer. Node.js,
ffmpeg and ffprobe are needed for the corresponding optional checks and media
workflows; dependencies are not installed automatically.

## Included

- The complete `tools/soda-frame-review/` app and its regression checks:
  galleries, frame navigation, annotations, recoverable Trash, diagrams,
  collections, local QA drafts, optional Jira connector and selector learning.
- Local review annotations, saved closeouts, approved-frame exports and
  workspace data are not included in this public snapshot.
- Existing Soda HTML, JSON and Mermaid reports under `docs/`.
- Local-video extraction entry point: `scripts/extract_new_recording.py`.
- Frame-selection rules, corrections and limitations in the
  [existing tool README](tools/soda-frame-review/README.md).

## Media and data boundaries

The reviewer reads source frames from the directory configured by
`SODA_FRAME_ARCHIVE`. The seven historical recordings have a retained-only
archive: 592 selected original frames. Their other source frames and exported
videos are not included in this repository. Continuous video playback is
therefore unavailable for those recordings.

Source videos and the external frame archive are not included. Do not replace
local datasets with symlinks to another project.

`data/` and `.ai-work/` are local and excluded from Git, including private
drafts, models, snapshots and generated provenance. A fresh checkout of this
repository does not include them.

The extractor requires an explicit `--source` and `--archive` to avoid writing
to the shared archive by default. It appends to an **existing** archive with a
`manifest.json` and refuses recordings already registered:

```sh
python3 scripts/extract_new_recording.py \
  --source "/absolute/path/to/owned-recording.mp4" \
  --archive "/absolute/path/to/separate-frame-archive"
```

This is the existing extraction workflow, not a new import UI. Registering a
new recording in the review dataset remains a separate step. No downloader or
YouTube-thumbnail script is included: those are not local-video screenshots.
Extraction preserves the source and exports every decoded frame, not an
automatically approved selection.

## Checks

```sh
python3 -m unittest discover -s tools/soda-frame-review -p 'test_*.py'
node tools/soda-frame-review/test_selection.cjs
node tools/soda-frame-review/test_learning.cjs
```
