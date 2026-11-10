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
recording boundaries in Frame editor; they do not save or modify annotations.

## Gallery, Frame editor and standalone cleanup

The first **Frame review** tab owns recording/window selection, the gallery,
pagination, filters, groups and yellow staging drafts. The separate **Frame
editor** tab owns the entire viewer workspace: image/video stage, source
transport, categories, decisions, intervals and saved-decision sidebar.
Gallery previews, nearby-frame buttons and exact group member thumbnails
(including Groups settings) open the exact frame there. Editor navigation spans
the whole recording, including group members outside the gallery window, without
changing that window, gallery page/filter, expanded units or recording.
**Back to gallery** restores that view. The editor visibly names the shared
recording; changing it in Frame review or Groups changes both contexts.
Switching workspace tabs pauses playback. Direct URLs support `tab=editor`
and `tab=groups` as well as existing tabs.

Use the prominent **Move unselected frames to Trash** button above the gallery
for standalone, recoverable cleanup. The summary and confirmation cover the
**whole source recording**, not the current gallery filter, page or review end.
Selected means the union of non-trashed individual Keep points and non-trashed
members of `selected_unit` groups, deduplicated. Metadata-only groups, intervals,
categories and unsaved yellow staging are not approvals. Save or clear staging
and finish any manual draft before cleanup. Existing Trash stays in Trash.
The preview names the recording and exact source/selected/eligible/existing-Trash
counts. Cancel (including Escape) changes no dataset or originals. Zero eligible
frames disables confirmation.

Confirmation makes one compact curated-window write under the shared dataset
lock, revision and session-bound preview proof. It preserves all existing cases,
labels, histories, groups, numbers, colors, categories and other recordings.
It does **not** export images, generate diagrams, invoke learning/model/Jira or
perform a recording closeout. No source file is removed. Use **Trash** to restore
individual frames. After an unconfirmed response, saves are blocked: reload
decisions and inspect Trash before any manual retry. Stale previews require a new
preview; nothing retries automatically. Retained-only archives prohibit cleanup
and individual Trash/restore consistently with their existing read-only policy.

Exact members in **Groups** and expanded gallery units have a separate **Move to
Trash** control. In Groups, already trashed members show **Restore from Trash**
and an explicit status. These use the existing single-frame recoverable Trash
operation, never remove group membership or change categories/Keep histories.
Normal gallery expansions continue to exclude Trash; Groups retains all exact
members so they can be restored. All surfaces refresh after the confirmed save.
Membership Move/Remove remains a distinct operation.

## Manual frame groups

### Keyboard-selected units

Open an exact frame in **Frame editor** and press **1** (or **Stage current frame**) to toggle
yellow staging. This is separate from Keep: staging a green frame displays
yellow without changing its decision. Navigate and repeat for each desired
member; there is no automatic choice of adjacent, intervening or kept frames.
The count and button pressed state describe staging without relying on color.
**Clear yellow staging** cancels it without a write.

Press **M** or **Group yellow frames** with at least two staged members. Only
those exact members are saved as **one green selected Same scene unit**.
The gallery places it at its earliest eligible member, collapsed by default;
**Expand members** reveals navigable exact thumbnails. **Ungroup — restore
individual view** removes only that unit, restoring individual visibility under
the current filter and original decisions. Group selection is not a new Keep
decision: labels, notes, intervals, histories, learning and individual-point
diagrams remain independent. No animation is inferred.

Selected units appear alongside individual cards in all normal gallery filters,
including Approved-only and suggestions, before pagination (16 display units
per page). Member cards are not duplicated on another page. Expanded children
do not count as extra page slots. Only non-trashed members inside the review
window are shown, with visible/total counts; a fully hidden unit stays saved but
has no normal card. Trash shows individual frames with their usual restoration
controls. Sorting never changes membership. Dragging a unit to QA references
its first visible exact frame, not all its members.

Staging survives navigation/filter/page changes and successful independent
annotation saves (newly trashed members are removed from the draft). Changing
recording, reloading decisions or receiving an external revision refresh clears
it. A successful group save clears yellow marks. Save errors or stale revisions
clear unsafe drafts, display an explicit error and block further saves: reload
decisions and inspect saved groups before manually retrying, because a lost
response may mean the write succeeded. There is no automatic retry.

Shortcuts ignore editable fields (including nested contenteditable/textbox
regions), modifiers, held-key repeats, IME composition, busy saves and other
tabs. Staging/grouping is also paused for playback, Trash and a manual membership
draft. Keyboard staging requires the exact source image to have loaded. A member of an
existing selected unit must be explicitly ungrouped before regrouping; the
server rejects overlapping selected units and trashed new members atomically.
Retained-only archives permit grouping their retained originals without enabling
annotation/deletion. No real frames are automatically selected.

### Merge destinations and numbered groups

Every individual gallery frame has a **Merge destination** dropdown and **Merge**
button beside its existing Trash action. The open-frame viewer has the same
controls. Changing a dropdown is only a local choice and never saves.

Choose **New group (yellow staging)** to add only that exact frame to the
existing yellow draft. Navigate or use other exact-frame dropdowns to stage
additional members, then press **M** or **Group yellow frames**. At least two
exact members are required before a write; the same selected-unit, collapsed
green-card workflow is used. No neighboring frames are inferred.
The gallery control stages the exact numbered frame represented by that card;
the keyboard **1** shortcut still requires the current source image to finish
loading in Frame editor before it stages the open frame. **M** works in Frame
review and Frame editor; **1** and decision/transport shortcuts are editor-only.
There is a Group yellow frames button in each tab, sharing the same draft.

Choose an existing **Group N** to append exactly that frame to the group. The
group keeps its stable internal id, type, name, provenance and other members;
membership is deduplicated and sorted by source frame. An already-present frame
is an explicit no-op. A merge never removes a frame from another group. If the
destination is a selected unit and the frame already belongs to another
selected unit, the save is refused with instructions to ungroup first or use a
metadata-only group.

Each recording has unique positive integer display numbers. New groups receive
the next available number. Existing legacy groups without numbers get
deterministic display-only numbers on read; opening, reloading and exporting
does not migrate the dataset. Configure saved groups in the separate **Groups**
workspace tab. Choose a recording, edit the number and color, then press
**Save group settings** to persist both fields together. Typing or choosing a
color alone never writes. Colors must be six-digit `#RRGGBB` values; older
groups without a saved color display neutral gray until explicitly saved.
Duplicate, invalid, foreign-recording or stale destinations are rejected
without partial membership changes. Internal group ids do not change when
display numbers or colors do. The same number and a color chip appear in cards
and member badges; merge dropdowns include the color value and use colored
options where the browser supports it. Dropdowns refresh after group changes
and reload.

To add a group, open **Frame editor** and press **1** on each of at least two
exact frames, then press **M**. Or choose **New group (yellow staging)** beside
a frame, stage additional exact frames, and press **M**. These paths preserve
the existing yellow staging and green selected-unit workflow. Configure the
saved group's number and color afterward in **Groups**. Empty groups and color
templates are not created.

### Rich metadata groups (backward compatible)

Use **Group kept frames — review members** above the gallery. The initial draft
contains the current recording's saved, non-trashed Keep points, including those
outside the review window. This is only a starting set, not an automatic group.
Review the thumbnails, deselect unrelated frames, and use **Add to group draft**
on gallery cards to include other exact frames without approving them.
At least two members are required. The count and chronological frame numbers /
source times describe the exact set; gaps between members are never included.

Choose **Same scene** (default) or **Animation**, optionally enter a name (120 characters,
no control characters), then tick **I reviewed this exact membership, type and
name** and click **Save group**. Names are rendered as literal text. Membership
changes clear that confirmation. Nothing is saved on opening or cancelling a
draft. Saving decisions, changing recording, or reloading decisions clears the
draft; concurrent changes reject a stale group save and require a reload.

Group badges appear on member cards. Click a badge to open that group in the
**Groups** tab, where you can inspect exact thumbnails and times (including
members outside the review window), configure its number/color, or ungroup it.
**Ungroup — metadata only** removes that group, not its frames or decisions.
Overlapping groups are allowed; ungroup one to replace its membership, type or
name. Groups remain exact references even if a member is later relabeled or
moved to Trash; that does not change Trash visibility.

In **Groups**, each exact member has a destination dropdown listing other
existing groups by number, type/name and color, plus explicit **Move** and
**Remove from group** actions. The same controls appear beside members when a
selected unit is expanded in the gallery. Choosing a destination is only a
local choice and never writes. **Move** removes exactly one frame from its
source and adds it to the chosen destination atomically. If the destination
already contains that frame, only the source membership is removed.
**Remove from group** changes only that source membership; other overlapping
memberships remain unchanged.

Groups must retain at least two members. If a correction would leave fewer than
two, the app previews that only the source group will be dissolved and asks for
confirmation. Cancel makes no request or change; the server independently
requires the explicit dissolution acknowledgement. After confirmation, the
remaining frame is individual unless it belongs to another group. Original
group ids, type, name, number, color, authorship and creation provenance are
preserved for surviving groups; membership corrections record separate
provenance. Trash, Keep decisions, histories, source media and other recordings
are not edited. Trashed or unavailable frames, stale revisions, invalid or
foreign group ids, same-group destinations and third-party selected-unit
overlaps are rejected without a partial write.

Groups live in the dataset's optional `frame_groups` list, separately from
point labels, interval labels and histories. Entries contain a generated id,
one recording name, sorted exact frame numbers, type, optional name, a unique
positive per-recording `display_number` and manual review provenance. Legacy
entries without a number receive a deterministic read-only display number.
Membership corrections are recorded separately in the dataset-level
`frame_group_membership_history`; they do not rewrite proposal authorship or
Keep/Trash histories.
Keyboard units additionally carry `selected_unit: true`;
absence (or false) retains the old metadata-only behavior. Legacy overlapping
groups remain untouched, can overlap new units and do not collapse or select
their members. Old datasets without the list remain valid. Group writes
use the same local origin/token checks, dataset lock, revision check, atomic
replacement and readback as feedback. JSON dataset exports include them.
Retained-only archives permit these metadata operations on retained originals,
but their annotation/deletion restrictions remain unchanged.

Metadata-only grouping never relabels, approves, hides or deletes a frame, changes original
media, or modifies another recording's data. It does not infer an animation,
continuous interval, temporal dependency or causal relationship. Diagrams,
learning and selections still use existing individual approvals; groups are
not additional learning labels and do not alter saved report snapshots.

## Saga map category

**Saga map** is available by default with the stable id `saga_map`. It is not a
Keep decision, group type, selection rule or inferred description. There is no
automatic tagging, category creation or category filter.

Use the category checkbox and **Save category** in the exact-frame viewer,
individual gallery cards, group cards or **Groups** settings/member editor.
Checking alone makes no request. To clear, uncheck and press **Save category**.
Repeat for as many exact frames and group references as needed. The saved badge
says **Direct frame category** or **Group reference category**; a changed
checkbox is only an unsaved choice until Save succeeds. Other saves, navigation
or rerendering may discard unsaved choices; there is no automatic retry.

A group category applies only to its reference. It never labels members,
approves Keep, merges groups or changes individual categories. Moving/removing
a member preserves its direct category; it inherits nothing from either group.
Ungrouping or confirmed dissolution clears only that group's assignments, with
removal provenance. Surviving group categories, exact frame categories, decisions,
histories and other recordings remain untouched. Retained-only originals allow
category metadata edits without enabling annotation or source deletion.

The optional dataset `category_assignments` list stores active exact references
by `category_id`, source recording name, `target_type` and either `frame` or
`group_id`, with reviewer/time/manual-method provenance. `category_history`
records explicit assignment/clear events and group-removal cleanup. These fields
are separate from `cases`, `frame_groups` and Keep/Trash histories. Legacy
datasets read as uncategorized without a migration or write. Dataset JSON
exports and reloads retain both metadata lists; the built-in definition is
provided by the app, not inferred from exported labels.

`POST /api/category` requires the local origin/session token, current revision,
recording index, `category_id: "saga_map"`, `target_type: "frame" | "group"`,
exact `frame` or `group_id`, and boolean `assigned`. Invalid, unavailable,
foreign-recording, mixed-target and stale requests make no partial write.
Category saves use the shared file lock, atomic replacement and readback.
An error blocks further saves until decisions are reloaded; inspect saved state
first if delivery is uncertain. Only synthetic fixtures exercise these writes
in regression checks.

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
clear proposed discards** resets every local discard choice across all pairs in
the current scan, with explicit cleared/no-op feedback. It does not bulk-select
unreviewed source frames or save any Keep decisions. Finding candidates never changes
feedback; the final closeout confirmation lists exact discarded frame numbers,
remaining selections, and the total non-selected-plus-reviewed-discard count.
Recoverable cleanup must be enabled to apply discards.

The dashboard scan supports up to 1,000 individual retained Keep frames and
displays the 40 closest candidate pairs. It compares every unordered pair,
including across 64-frame processing blocks; no first-300 truncation or sampling
is used. The result states the exact scanned selection and comparison counts.
Larger sets are refused explicitly without changing decisions. The server
keeps only the best 40 pairs in memory while counting every qualifying pair.
Candidates have matching image dimensions and mean RGB
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

Working policy for subsequent Soda preselection (the scoped saved-package review
below takes precedence for that package, not as a global detector change):

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

### Saved selection review: recording 1, package 20261110T174250822822Z

**Review status: candidate procedural learning, pending owner acceptance.**
This recording is identified as `frames`, not historical recording 1 in the
retained-only archive. Evidence is the private, immutable selection package
`data/soda-approved-frames/20261110T174250822822Z/manifest.json` and its
`frames/frame_NNNNNN.jpg` originals, plus
`data/soda-review-learning/20261110T174250822822Z/analysis.json`.
References below are exact source frame numbers and seconds from that manifest.
No source images, names from leaderboards or private notes belong in a public
release. These references document observations, not a published image archive.

**Coverage and preservation.** All 440 exported images were hash-checked against
the manifest and visually inspected in chronological contact sheets, covering
2.000-1928.043 seconds. The text/control/counter examples below were also
reopened at full resolution. Thumbnail inspection alone cannot establish
character-perfect text or equivalence for every pair. Every selected image
remains retained; no relabeling, group edits, Trash operation, similarity
discard, threshold change or model fitting was performed for this review.

**Observations (direct visual evidence):**

| Evidence: frame / seconds | What is visible | Comparison boundary |
| --- | --- | --- |
| 1513 / 25.952; 108111 / 1826.602; 108531 / 1833.602 | The first daily-mission view reads "Crea 90 peces de gelatina", shows `0/90`, and locks the following missions. The later views show a check on the first row and "Juega 9 niveles" with `0/9`; the third row remains locked. | Same panel and reward-track thresholds `175`, `455`, `700`, but different task text, progress and unlock state. |
| 38207 / 647.677; 38327 / 649.677 | The bear activity shows coin counts `30` then `20`, progress `1/3` then `2/3`, and a feed control priced `10`; the character pose also changes. | Preserve counter values and the animation phase independently. These two stills do not establish which exact input caused the change. |
| 58980 / 998.108; 59458 / 1006.108 | An overlay offers `+10` moves and one striped candy, with a continue control priced `19`. A later board shows `9` moves, objective `4`, and the first bottom booster highlighted. | Offer presentation is observable; a purchase or button press is not proved by the later board. |
| 75616 / 1277.517; 75676 / 1278.517; 75971 / 1283.518 | All three boards show `28` moves and one bear objective. The intermediate frame 75676 adds a multicolor lollipop over the board, absent at 75616. The last board has substantially fewer blockers; the last bottom booster counter is `37`, `37`, then `36`, while the first is `109` throughout. | An unchanged moves counter does not imply the same board or booster state. |

Across the complete contact-sheet pass, selections also include reward reveals,
panels before their controls appear, loading overlays, repeated home-map views,
event/league screens, board effects and an operating-system screenshot overlay
(36574 / 619.665). Their presence is evidence of this saved selection, not a
reason to silently remove them or an assertion that the overlay is intended
game UI. No new semantic group boundaries were approved in this review.

**Interpretations (candidate, not accepted rules):**

- Compare the screen title, task copy, button presence/label, objective/move
  counts, reward quantities, timers and board/booster state before calling two
  frames equivalent. Small UI regions can matter despite high pixel similarity.
  The daily-mission and bear examples support this procedure.
- Keep intermediate states as exact evidence when documenting an animation or
  important event. A group can summarize the sequence without replacing its
  selected originals. This follows the explicit review request and the
  animation/event focus recorded in `analysis.json:notes`; it does not globally
  reverse earlier substantial-state selection guidance.
- Review gameplay with attention to in-game boosters for this task, but do not
  infer that omitted level gameplay is unimportant or rejected. The supplied
  notes describe the review focus; they do not supply negative labels.
- Draw chronological links only. Use separate annotations for visible facts,
  possible explanations and missing evidence instead of naming a tap, purchase,
  cause or player intention from neighboring stills.

**Unknowns and limits.** The automated analysis explicitly reports no semantic
review and zero existing candidate suggestions; its zero agreement counts are
not accuracy or recall measurements. No full-video continuity, touch log,
transaction log or prospective validation was inspected here. Exact booster
causality, unchanged-looking frames' equivalence, reasons for every omission
and whether this policy generalizes remain unknown. User-created categories
such as Pop-up are a proposed UI extension, not implemented or assigned by
this review. Reports and local diagrams remain evidence snapshots; this README
is the single procedural-learning location.

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
