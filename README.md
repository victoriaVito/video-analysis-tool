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

Version 2 usage is documented in the [six-page tutorial](docs/video-analysis-tool-tutorial.pdf)
([printable HTML](docs/video-analysis-tool-tutorial.html)) and
[release notes](docs/version-2-release-notes.md).

- The complete `tools/soda-frame-review/` app and its regression checks:
  galleries, frame navigation, annotations, recoverable Trash, diagrams,
  collections, local QA drafts, optional Jira connector and selector learning.
- Local review annotations, saved closeouts, approved-frame exports and
  workspace data are not included in this public snapshot.
- Only tool documentation belongs in `docs/`; generated recording reports,
  diagrams and embedded frame previews are local-only and excluded from Git.
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

Historical `docs/soda-*` reports and chat `published-updates/` attachments are
also excluded from Git. Keep source frames and recording-specific exports local;
publish only application code, tests and tool documentation. Removing reports
from the current tree does not erase them from earlier commits or release tags.

Before extraction, the tool asks for the source video path, an existing local
destination parent folder, and a recording name. It creates a timestamped
folder there, copies the original video without changing it, and saves all
extracted frames and timestamps beside that copy. Both source and destination
must be outside this repository; no analyzed video or frame content is stored
in Git. The extractor does not alter the historical archive:

```sh
python3 scripts/extract_new_recording.py
```

For non-interactive use, pass `--source`, `--destination` and `--name`; the
legacy `--archive` option is an alias for the destination parent, not an
existing archive to append to. To review a newly created local archive, pass
its timestamped folder to the dashboard:

```sh
python3 tools/soda-frame-review/server.py --port 8767 \
  --archive "/absolute/local/path/20261109T120000_my-recording"
```

Registration in the human-review dataset remains a separate step. Extraction
preserves the source and exports every decoded frame, not an automatically
approved selection. No downloader or YouTube-thumbnail script is included.

## Checks

```sh
python3 -m unittest discover -s tools/soda-frame-review -p 'test_*.py'
python3 -m unittest discover -s scripts -p 'test_*.py'
node tools/soda-frame-review/test_selection.cjs
node tools/soda-frame-review/test_learning.cjs
```
