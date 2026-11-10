# Version 2 - Exact groups and recoverable review

This checkpoint follows the local-video extraction release. It publishes
application code and documentation only, not recordings, selections or drafts.

## Added

- Separate Frame editor for the full source viewer and decision workspace,
  with exact-frame navigation and gallery context preservation.
- Yellow exact-frame staging with 1; M saves at least two marked frames as one
  selected group. Collapsed/expanded groups preserve their original members.
- Numbered, color-coded groups; explicit Save settings and Merge destinations.
- Exact per-member Move and Remove controls with confirmation before dissolving
  a group that would have fewer than two members.
- Independent Saga map category assignments on frame and group references.
- Per-member recoverable Trash/Restore controls in group inspection.
- Prominent gallery cleanup with whole-recording preview, confirmation and an
  atomic revision-guarded write protecting Keep points and selected-unit members.
- Updated six-page tutorial, supplied as printable HTML and PDF.

## Fixed

- Similarity scan refusal above 300 retained selections: dashboard scans now
  compare all unordered pairs across up to 1,000 individual Keep frames,
  including cross-block comparisons, and retain the closest 40 candidates.
- Keep all / clear proposed discards now reports cleared/no-op status and
  explains that it only clears local discard proposals, not saved Keep labels.

## Boundaries

No source file is removed by Trash. Suggestions and groups do not become
individual Keep decisions automatically. Similarity is a pixel heuristic;
small text/counter/control changes still require human inspection.
No general Undo/Redo, arbitrary custom labels, automatic semantic grouping,
model fitting or Figma upload is included.

Private selection review documents candidate procedural learning in the tool
README; it neither changes labels nor treats omissions as negatives. Private
frame diagrams are delivered separately and are not release assets.

## Tutorial

Open [the HTML guide](video-analysis-tool-tutorial.html), or download
[the PDF](video-analysis-tool-tutorial.pdf). No real recording images are used.
