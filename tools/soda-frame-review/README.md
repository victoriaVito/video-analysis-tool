# Soda Frame Review

Local-only source-frame selection and chronological diagram viewer. This folder
is the editable dashboard source; it is not a generated canvas or a Git checkout.

## Run

From the tool repository, using the existing Pillow-capable Python:

```sh
/opt/local/bin/python3 tools/soda-frame-review/server.py --port 8767
```

Open `http://127.0.0.1:8767/`. To review a separately extracted local archive,
add `--archive "/path/to/timestamped-recording-folder"`. The archive folder
contains a local copy of the source video, a `frames/` directory, timestamps
and a manifest. Its destination must be outside the repository. Review labels
remain in the local calibration dataset and are not included in the media
archive.

## Source and data

- `server.py`: local HTTP API, revision-guarded atomic annotation storage,
  source images, graph generation and rotating logs.
- `app.js`: gallery, approval, trash/restore and review navigation.
- `enhancements.js`: stepwise diagram viewer, exports and diagnostics.
- `index.html`, `style.css`: structure and presentation.
- `test_server.py`, `test_selection.cjs`: isolated regression checks.

Human feedback remains in `data/soda-keyframe-calibration/dataset.json`.
Suggestions are not approvals. Trash changes dashboard visibility only.
Frontend edits take effect on reload; Python edits require a server restart.
Reload saved decisions after another window writes; stale revisions are rejected.
The selected recording is kept in the URL so reloading preserves it.
The viewer supports single-frame navigation and jumps of +/-1, +/-2 and
+/-2.5 seconds. Jumps use the nearest real source timestamp and stop at the
review-window boundaries; they do not save or modify annotations.

## Diagram

Click **Create diagram from approved frames**. Each node is a saved `new_state`
point outside Trash, sorted by source frame within the selected recording.
Suggestions, rejected labels and intervals are excluded. All approved points in
the recording are included, even outside the review window.

Reveal steps individually or show all. Click a node to inspect its exact source
frame. The diagram is read-only and does not approve images or infer actions,
decisions, branches or causal links. A saved-feedback revision change marks the
diagram stale and disables exports until rebuilt. Recording changes clear it.
Download JSON for nodes, timestamps, image routes, edges and dataset revision;
download Mermaid for an editable text diagram (without embedded images).

## Diagrams, collections and QA/Jira workspace

The **Diagrams and collections** tab lists saved `docs/soda-*.html` reports
and current approved-frame collections for every recording, plus previously
verified exports from `data/soda-approved-frames/`. Saved collections retain
their export revision rather than silently following current decisions. Reports are
read-only sandboxed previews; live collections use the current dataset and do not
change selection labels. A collection image can be added to a QA draft.
**Request context** saves an explicit local request and prepares a prompt to
copy into the Copilot conversation. There is no hidden AI API or automatic
model invocation. Report navigation remains provisional unless verified.

The **QA - Jira tickets** tab accepts gallery-frame drags, collection images,
the current source frame and dropped/attached JPEG, PNG or WebP files. Drag a
gallery card onto the QA tab, then into its drop area. Attachments are limited
to 20 images, 8 MB each, 24 MB total and 25 megapixels per uploaded image.
Source frames are referenced without moving or changing originals.
**Prepare description from evidence** adds an editable QA template and frame
references, not an invented diagnosis. Export the draft to request further
Copilot assistance. Review personal information and redact externally before
attaching when required.

Drafts, context requests and submission receipts persist under
`data/soda-review-workspace/`. Uploaded image bytes are stored there in base64;
this is local project data, not a credential store. Draft writes use a server
lock, version checks, atomic replacement and readback. A server crash after a
remote send may leave a `sending`/unconfirmed receipt: inspect Jira before any
manual retry. Submitted or uncertain drafts cannot be sent again or overwritten.
Issue creation and attachment upload are separate operations; a failed attachment
step retains the created issue link and reports the partial state.

### LLM learning: reusable recording closeout

Open **LLM learning**, choose a recording and refresh its selection summary.
Every run preserves all retained selections and copies their full-resolution
originals to a verified export after any explicitly reviewed similar-frame
discards. Choose whether to hide non-selections in
recoverable dashboard-only Trash, publish HTML/JSON/Mermaid diagrams and compare
existing analyst suggestions. The confirmation names the recording and exact
counts. This operates on the **whole source recording**, not only the current
gallery page or review-end window. Source files are never deleted.

Use **Find similar frames** to compare selected images side by side. Tick
**Discard frame N** for the image to remove; leave both unchecked to retain both.
Repeated appearances of the same frame share one discard decision. **Keep all /
clear proposed discards** resets these choices. Finding candidates never changes
feedback; the final closeout confirmation lists exact discarded frame numbers,
remaining selections, and the total non-selected-plus-reviewed-discard count.
Recoverable cleanup must be enabled to apply discards.

The scan considers up to 300 retained frames and displays the 40 closest
candidate pairs. Candidates have matching image dimensions and mean RGB
difference at most 5/255 on 96x192 previews, after a coarse 16x32 prefilter at
10/255. This is a pixel heuristic, not exhaustive semantic duplicate detection:
small text, counter and button changes may still matter. Full-resolution links
are available for inspection. No candidate is automatically marked for removal.
Feedback changes clear the scan and its discard proposals; source hashes are
rechecked before applying cleanup.

The routine validates the dataset revision again after copying images, checks
source hashes and the complete filename inventory, then applies curation under
the same file lock as manual feedback. It preserves label histories, other
recordings and original splits. Concurrent feedback changes cause an explicit
refusal rather than overwriting decisions. The new collection and all three
diagram formats exclude explicitly discarded frames; existing snapshots remain
unchanged. Discard provenance is included in the receipt, analysis and curation
window. Use the existing **Trash** view to restore a discarded frame and recover
its original selected label. A pre-write backup is retained under
`.ai-work/temporary/soda-learning/<run>/dataset-before.json`.

`data/soda-review-learning/<run>/` contains the result receipt, descriptive
analysis, diagram JSON and an evidence-linked Copilot request. Full-resolution
copies are under `data/soda-approved-frames/<run>/`; published diagrams appear
in the existing library. Receipts distinguish complete, building and incomplete
runs, including whether curation already happened. A lost response must not be
treated as failure proof: refresh **Saved closeouts** before manually retrying.
There is no automatic retry.

**Learning is not automatic LLM training.** Comparisons use the existing greedy
one-to-one temporal agreement helper; they do not assign negative labels or
prove semantic equivalence. The routine does not call an AI model, interpret
images or modify weights. Add optional notes, then use **Copy review request**
in the Copilot chat for visual reasoning and source-grounded procedural guidance.
Canonical selector guidance remains in this README; saved reports are evidence
snapshots, not competing instruction sources.

Tabs support direct URLs, for example `/?video=5&tab=learning` opens recording 6.
Existing `?video=` values remain zero-based. The separate learning recording
selector does not change the active frame-review recording or QA draft.

```sh
python3 tools/soda-frame-review/test_learning.py
node tools/soda-frame-review/test_learning.cjs
node --check tools/soda-frame-review/learning.js
```

### Connect Jira Cloud

Paste your HTTPS `tenant.atlassian.net` site or board URL, Jira email and API
token in **Connect Jira Cloud**. Use an API token with access to the named
site, project creation permission and attachment permission. The token must
not be pasted into chat. Credentials stay in server memory only, are not
returned by the API, and are cleared by Disconnect or server restart. No
connector is installed automatically. Data Center and scoped-token gateway
URLs are not supported by this direct-site connector.

The first 100 accessible projects and first 100 issue types can be loaded.
Choose the **project** and **issue type**: Jira issues do not belong directly to
a board; its filter controls visibility. Additional mandatory custom fields
are not inferred. Jira will explicitly reject an incomplete create request;
use the native Jira form if your project requires unsupported fields.
The confirmation dialog names the site, project, issue type and attachment
count. Nothing is transmitted until **Send this ticket and its images** is
clicked. HTTP redirects are refused, destinations are restricted to HTTPS
Atlassian Cloud tenants, and no create/upload is retried automatically.

The connector follows Atlassian's
[issue API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/)
and [attachment API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-attachments/).
Local mocked checks cover create, upload and uncertain/partial states; they
do not establish live authentication or real Jira persistence.

```sh
python3 tools/soda-frame-review/test_workspace.py
node --check tools/soda-frame-review/workspace.js
```

## Errors and diagnostics

**Code and error log** displays source location and the latest 100 log lines.
Logs are local under `.ai-work/temporary/soda-frame-review-logs/dashboard.log`,
rotated at 1 MB with three backups. API errors and server I/O tracebacks are
logged. Handled browser failures and uncaught errors attributable to the dashboard's
own scripts are sent to the same local server with session/origin
checks, bounded messages and at most 30 reports per page load.
No external telemetry, request bodies, credentials or annotation notes are
intentionally collected. Browser exception messages can contain contextual
details; review logs before sharing. If the server is unreachable, the browser
shows that logging failed; it cannot persist errors while disconnected.

## Checks

```sh
python3 tools/soda-frame-review/test_server.py
node --check tools/soda-frame-review/app.js
node --check tools/soda-frame-review/enhancements.js
node tools/soda-frame-review/test_selection.cjs
```

Tests use isolated data under the project's temporary directory, not real labels.

## Selection calibration

### Permanent archive closeout

On 2026-10-27 Victoria explicitly authorized physical deletion of all
non-selected source frames and any remaining exported videos after completing
the seven recordings. `retained_frames` in the archive manifest now distinguishes
the retained-only inventory from historical `extracted_frame_count` and full
timestamp rows. Frame numbers and source timestamps are not renumbered.
The viewer browses retained originals only and refuses annotation, restoration
of deleted files or another learning closeout on these completed recordings.
Previously saved diagrams, packages, labels and context hashes remain historical
evidence; links to unselected original images may no longer resolve.
Photos originals were not accessed or deleted.
The cleanup retained 592 originals (22/27/8/55/66/82/332 across recordings 1-7,
193,992,298 bytes) and deleted 255,595 unselected JPEGs plus the derived
recording-1 preview. The registered exported MP4s were already absent.
The verified all-recording backup package and permanent deletion receipt are
`data/soda-approved-frames/20261027T183800531125Z/` and its
`cleanup-receipt.json`. Earlier per-recording exports and diagrams are preserved.

Run `python3 tools/soda-frame-review/compare_selections.py` to compare the latest
retained selections for recordings 1-4 with the original detector candidates
and analyst suggestions (including primary/uncertain groups when present).
Output is a rebuildable report at
`.ai-work/temporary/soda-selection-learning.json`, tied to a dataset revision.
The feedback dataset remains the canonical source of human decisions.
Use `--videos 5 --output .ai-work/temporary/soda-video-5-finalization/selection-learning.json`
for a separate recording-5 descriptive comparison. Video numbers are one-based;
the default remains 1-4. Comparing holdout selections does not fit the model or
establish independent evaluation.

The comparison uses exact frame agreement and greedy one-to-one temporal
matching at 0.25, 1 and 2.5 seconds. These are agreement diagnostics, not
precision, semantic equivalence or measured visual quality. Unselected images
are not negative training labels. Keep the original holdout split unchanged.

Working policy for subsequent Soda preselection:

- Use Victoria's explicit definition: substantial visual state changes, not
  intermediate phases of the same animation. Preserve exact source-frame choice.
- Do not impose a target count or use recording-specific frame IDs as rules.
- Avoid aggressively pruning ambiguous candidates: the recording-2 analyst
  shortlist matched fewer approved moments than the broader detector output.
  Put ambiguous transition neighborhoods in a separate review group instead of
  silently removing them. This is a hypothesis to test, not a validated model.
- Inspect nearby source frames before rejecting a detector candidate. Even
  temporally close suggestions may show a different animation phase.
- Preserve manual additions outside the shortlist and chronological ordering.
- Use the next unreviewed recording as prospective human review, without claiming
  independent holdout performance for recordings already used for calibration.

No model weights, upstream skill or detector thresholds were modified by this
comparison. Acceptance of the revised policy requires future human feedback.

Recordings 4-7 now have conservative, thumbnail-reviewed suggestions. The
`Analyst preselection` gallery shows the primary group; `Uncertain candidates`
retains all other original detector candidates, including possible transitions,
repeats and unclear changes. Neither group is human approval or rejection.
Legacy suggestions without a group still appear in the primary gallery. Both
views include exact manually approved frames and respect Trash/review boundaries.
`Inspect -0.5s` and `Inspect +0.5s` open nearby source timestamps in the viewer
without changing annotations. They are sampling aids, not proof of stability.

The initial primary/secondary counts are 101/96, 50/33, 64/38 and 268/122 for
recordings 4, 5, 6 and 7 respectively. All 772 original candidates were retained;
source hashes and timestamps were checked. Nineteen contact sheets and sampled
before/center/after sheets are in
`.ai-work/temporary/soda-preselection-4-7/`, alongside the persistence report
and pre-write backup. Only 18 sampled candidate neighborhoods were inspected;
this is not continuous or full-resolution visual review. Detector gaps remain
unreviewed. Holdout feedback was not used for selecting suggestions or tuning
thresholds, and the original split remains unchanged.

### Recording-4 feedback (2026-10-27)

The finalized selection retains 55 frames from 57,147 source frames. The other
57,092 are in recoverable dashboard-only Trash; this visibility decision does
not assign negative class labels. Originals, prior labels and the original
`calibration` split are preserved. One earlier duplicate was deselected by
Copilot at Victoria's request, so the current set is not an untouched human
evaluation set.

The 197 detector candidates match only one selected frame exactly, with 3, 11
and 20 one-to-one temporal agreements within 0.25, 1 and 2.5 seconds. The
101 primary suggestions match 1 exactly and 2/10/15 temporally; the 96 uncertain
candidates match 0 exactly and 1/3/7 temporally. Group matches are computed
independently and must not be added together. These are agreement diagnostics,
not precision or evidence of equivalent content. See the revision-bound
[`soda-selection-learning.json`](../../.ai-work/temporary/soda-selection-learning.json).

Source-grounded working guidance from the selected-frame visual review:

- Preserve meaningful intermediate flow states, not just settled endpoints.
  Selected frames 6241/6257 (106.177/106.443s) show the same event introduction
  before and after the Start button appears. Nearness alone is not duplication.
- Do not blanket-exclude loading, celebration or transition screens: this
  selection retains examples at frames 151, 6188 and 16153. Their selection is
  observed; the reason Victoria chose each remains an interpretation.
- Compare text, available actions, reward/offer details and progress before
  judging similarity. Frames 6690/6870 show different booster descriptions
  despite very low whole-image pixel distance.
- Preserve before/after prompts: frames 20038/20045 (338.565/338.682s) differ
  by an extra-moves offer. A global minimum time gap would lose such a step.
- Prefer readable counters when the state is otherwise repeated: the explicitly
  reviewed pair 23230/23238 retained 23230 and deselected the bouncing-icon
  version. This is a local decision, not permission to remove every animation.

These are reviewed procedural hypotheses for future suggestions, not trained
weights, tuned detector thresholds or a demonstrated quality improvement.
Keep manual exact-frame additions and ambiguous neighborhoods available.
Validate on a later recording before claiming generalization. Visual evidence
is in [the duplicate-review sheets](../../.ai-work/temporary/video-4-duplicate-review/);
curation receipts and the pre-write backup are in
[`soda-video-4-finalization`](../../.ai-work/temporary/soda-video-4-finalization/).

### Recording-5 feedback (2026-10-27)

The finalized snapshot retains all 66 selected frames out of 17,804 source
frames; 17,738 unselected frames are recoverably hidden in dashboard Trash.
Label histories, source originals, other recordings and the `holdout` split
are unchanged. Full-resolution copies and hashes are in
`data/soda-approved-frames/20261027T173500826227Z/`. The saved
[selection diagram](../../docs/soda-video-5-selection-diagram.html) contains
all 66 frames, eight interpreted groups and 65 chronological edges.

The existing 83 candidates match three selected frames exactly and 11/23/28
within 0.25/1/2.5 seconds using the existing greedy one-to-one diagnostic.
Primary candidates (50) match 3 exactly and 10/21/25 temporally; uncertain
candidates (33) match 0 exactly and 1/4/7 temporally. Group matches are
independent and not additive. Closeness in time is not equivalent content;
no precision, recall or quality improvement follows from these counts.

Working observations and interpretations from this selection:

- Frame 6579 (111.143333s) visibly contains repeated English placeholder text;
  6580 (111.16s) shows the localized Spanish message. Full-resolution inspection
  confirms the placeholder content. Preserve this as a possible transient
  content/localization defect; duration, root cause and reproducibility remain
  unknown. Removing all intermediate screens would discard this QA lead.
- Frames 6978/6979/6985 preserve event-entry and target-panel phases, ending
  with a readable completed target. Frames 15128/15188 preserve the life
  message before and after the reward artwork appears. Selection alone does
  not establish why Victoria wanted each phase.
- Map frames 6700/6758/6805/6814 retain changing counters, floating indicators
  and collect availability. Prefer local text/control/counter comparison to
  whole-image deduplication when reviewing such clusters.
- Activities frames 14347/14587/14941 are visually very similar. They remain
  selected: this task did not authorize deselecting duplicates. Similarity
  is a review aid, not a deletion decision or a negative label.
- Shared gameplay visuals must retain mode context: mastery, event panels,
  map and social/store windows are not interchangeable journey steps.

These are source-grounded procedural hypotheses, not a retrained selector.
No fitting, threshold change or model deployment occurred. Because recording 5
has now been qualitatively inspected and discussed, its future use must not be
presented as untouched independent evaluation. Keep the original split and
record the exposure rather than silently relabeling it.

### Recording-6 feedback (2026-10-27)

The first confirmed learning-tab run retained all 82 selected frames from
18,423 source frames and recoverably hid the other 18,341. All source images,
label histories, prior curations, other recordings and the original `holdout`
split were preserved. The 82 original-resolution copies total 26,176,980 bytes;
each copied hash matches its original. Saved results:
`data/soda-review-learning/20261027T175457593408Z/`; package:
`data/soda-approved-frames/20261027T175457593408Z/`.
The [saved diagram](../../docs/soda-video-6-selection-20261027T175457593408Z.html)
contains 82 image nodes and its JSON/Mermaid exports retain 81 chronological
edges. Chronology does not establish a tap, purchase or causal transition.

All 82 selections were inspected on six contact sheets; selected similar pairs
and treasure-introduction text were also inspected at source resolution.
Source-grounded working observations, not inferred user intent:

- Reward and control changes matter: frames 2152/2272 retain the reward reveal
  and Collect availability; 10736/10789 retain rocket-event entry before and
  after Start appears. Do not retain only the last settled screen.
- Frames 3353/3533 show cake progress changing from 7/8 to 8/8; 3710/3768 retain
  layer completion and subsequent cake progress. Similarity must consider
  counters, reward state and action availability.
- Store frames 4295-4826 retain different offer contents and scroll positions.
  An offer image is not evidence that a purchase happened. Similar mission
  frames 2381/2561 remain selected; no duplicate removal was requested here.
- Treasure introduction frames 17271/17331 show the same sentence at different
  font scales. Source-resolution inspection does not support a localization
  defect claim for this pair; preserve the phases without diagnosing a bug.
- Frame 18264 includes an operating-system notification overlay. Keep the
  selected original locally; inspect/redact before any external publication.

The 102 analyst candidates have 1 exact match and 11/34/48 greedy temporal
matches within 0.25/1/2.5 seconds. The 64 primary suggestions have 1 exact and
8/30/41 temporal matches; 38 uncertain suggestions have 0 exact and 3/8/15.
Groups are independently matched and not additive. These counts describe
agreement, not precision, recall or quality improvement. No fitting, threshold
change or model deployment occurred. Preserve the split but record qualitative
exposure: this recording is no longer untouched independent evaluation.

## Experimental trained ranker

`train_selector.py` fits a positive-only visual-context model locally using
NumPy and Pillow. Use the existing Pillow-capable `python3`; the project
`.venv` lacks Pillow. No packages, remote calls or model downloads are required.

```sh
python3 tools/soda-frame-review/test_train_selector.py
python3 tools/soda-frame-review/train_selector.py --score-video 5
```

Only latest, retained `new_state` point labels explicitly attributed to Victoria
in the existing `calibration` split enter fitting. Uncertain labels, trash,
omissions, agent labels and all holdout labels are excluded. This does not train
a binary keep/discard classifier. The model learns standardization and four
k-means prototypes from RGB grids and before/after differences at +/-1 second
(nearest real timestamps). Its distance measures appearance/context familiarity,
not a probability of usefulness, OCR or semantic flow understanding.

The first persisted run is
`data/soda-keyframe-calibration/models/20261027T170316566483Z/`.
It fitted 112 positives from recordings 1-4 (22/27/8/55), with 108 features and
zero negative examples, and ranked the 83 existing recording-5 detector
candidates. Recording-5 labels did not enter fitting or parameter selection.
Candidate coverage remains bounded to the old detector; the model cannot
recover omitted states. The original split and canonical feedback are unchanged.

`model.json` contains fitted parameters, exact training-label lineage, source
context hashes, dataset revision, feature/script/tool versions and four
leave-one-recording-out positive-distance diagnostics. Those diagnostics are
not accuracy, precision, recall or independent quality evidence. `ranking.json`
preserves every scored candidate as unreviewed. `top-ranked-review.jpg` provides
a bounded visual review of the first 16 ranked frames.

Visual review of that sheet found repeated gameplay boards dominating the top
positions. The run is **trained but not accepted or deployed**; there is no
demonstrated reduction in review work or improvement over the existing detector.
No automatic selections, discards or dashboard ranking changes are made.
Explicit human keep/repeat/animation examples and a held-out evaluation are
needed before a discriminative selector can be trusted. Do not silently convert
unselected frames or the agent's duplicate decision into human negative labels.
