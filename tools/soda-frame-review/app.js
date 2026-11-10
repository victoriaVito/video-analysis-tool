"use strict";
const $ = id => document.getElementById(id);
const names = {new_state:"Keep",animation:"Animation",same_state:"Same state",uncertain:"Unsure"};
let videos = [], state, recording = 0, current = 0, last = 0, endTime = 86;
let rangeStart = null, rangeEnd = null, page = 0, galleryFrames = [], busy = false;
let imageReady = false, playing = false, currentImageRequest = 0;
let playbackRequest = 0;
const video = $("video"), perPage = 16;
let saveQueue = Promise.resolve(), pendingSaves = 0, saveBlocked = false;
let trashView = false;
let groupDraft = null, inspectedGroup = null;
let stagedFrames = new Set(), stagedRevision = null;
const mergeDestinations = new Map();
const expandedGroups = new Set();
let pendingMembershipChange = null;
let cleanupPreview = null, cleanupBusy = false;
const groupNames = {animation:"Animation",same_scene:"Same scene"};
const defaultGroupColor = "#808080";
const groupColorPattern = /^#[0-9a-f]{6}$/i;
const pendingSelections = new Map();
const trashIndexes = new WeakMap();
function activateWorkspaceTab(name) {
  const tabs=["review","editor","groups","library","qa","learning"];
  if (!tabs.includes(name)) return;
  if (state) stop();
  for (const tab of tabs) {
    $(`${tab}-tab`).hidden=tab!==name;
    $(`tab-${tab}`).setAttribute("aria-pressed",String(tab===name));
  }
  if (typeof location!=="undefined" && typeof history!=="undefined") {
    const url=new URL(location.href);
    if (name==="review") url.searchParams.delete("tab");
    else url.searchParams.set("tab",name);
    history.replaceState(null,"",url);
  }
  if (state) {
    $("timeline").max=frameBoundary();
    $("timeline").value=current;
    updatePosition();
    setControls();
  }
}
function frameBoundary() { return $("editor-tab").hidden ? last : videos[recording].times.length-1; }
function stagingTabVisible() { return !$("review-tab").hidden || !$("editor-tab").hidden; }
function groupColor(group) {
  return groupColorPattern.test(group.color || "") ? group.color.toUpperCase() : defaultGroupColor;
}
function appendGroupColorChip(parent, group) {
  const color=groupColor(group), chip=document.createElement("span");
  chip.className="group-color-chip";
  chip.style.backgroundColor=color;
  chip.setAttribute("role","img");
  chip.setAttribute("aria-label",`Group color ${color}`);
  parent.append(chip);
}
function isTrashed(frame) {
  if (!trashIndexes.has(state)) {
    trashIndexes.set(state,new Set((state.trashed || []).map(x=>`${x.video}:${x.frame}`)));
  }
  return trashIndexes.get(state).has(`${videos[recording].name}:${frame}`);
}
function visibleGalleryFrames() {
  return trashView
    ? (state.trashed || []).filter(x=>x.video===videos[recording].name && x.frame<=last+1)
        .map(x=>x.frame-1).sort((a,b)=>a-b)
    : galleryFrames.filter(i=>!isTrashed(i+1));
}
function unitGroups() {
  return recordingGroups().filter(group=>group.selected_unit===true);
}
function availableGroupFrames(group) {
  return group.frames.filter(frame=>frame<=last+1 && !isTrashed(frame) &&
    (!videos[recording].retention_only || videos[recording].retained_frames.includes(frame)));
}
function visibleGalleryEntries() {
  const frames=visibleGalleryFrames();
  if (trashView) return frames.map(index=>({index}));
  const units=unitGroups().map(group=>({group,frames:availableGroupFrames(group)}))
    .filter(unit=>unit.frames.length);
  const members=new Set(units.flatMap(unit=>unit.frames));
  return [
    ...frames.filter(index=>!members.has(index+1)).map(index=>({index})),
    ...units.map(unit=>({...unit,index:unit.frames[0]-1}))
  ].sort((a,b)=>a.index-b.index);
}
function clearStaging() {
  stagedFrames.clear(); stagedRevision=null;
  renderStaging();
}
function canStage() {
  return !!state && !busy && !saveBlocked && !playing && imageReady && !trashView &&
    !groupDraft && !$("editor-tab").hidden && !isTrashed(current+1) &&
    !unitGroups().some(group=>group.frames.includes(current+1));
}
function renderStaging() {
  $("stage-count").textContent=`${stagedFrames.size} yellow staged frames (not Keep decisions)`;
  $("editor-stage-count").textContent=$("stage-count").textContent;
  $("stage-current").textContent=stagedFrames.has(current+1) ? "Unstage current frame (1)" : "Stage current frame (1)";
  $("stage-current").setAttribute("aria-pressed",String(stagedFrames.has(current+1)));
  $("frame-image").classList.toggle("staged",stagedFrames.has(current+1));
}
function toggleStaging() {
  if (!canStage()) return;
  if (stagedFrames.has(current+1)) stagedFrames.delete(current+1);
  else stagedFrames.add(current+1);
  stagedRevision=stagedFrames.size ? state.revision : null;
  renderStaging(); renderGallery();
}
function stageExactFrame(frame) {
  if (busy || saveBlocked || playing || trashView || groupDraft || !stagingTabVisible()) return;
  if (!Number.isInteger(frame) || frame<1 || frame>videos[recording].times.length) {
    fail("Choose an exact frame inside the source recording."); return;
  }
  if (isTrashed(frame)) {
    fail("Restore this frame from Trash before staging it for a group."); return;
  }
  if (videos[recording].retention_only &&
      !videos[recording].retained_frames.includes(frame)) {
    fail("This frame is unavailable in the retained-only archive."); return;
  }
  if (unitGroups().some(group=>group.frames.includes(frame))) {
    fail("This frame already belongs to a selected unit. Ungroup it before staging a new unit."); return;
  }
  if (stagedFrames.has(frame)) {
    $("save-status").textContent=`Frame ${frame} is already staged; no change made.`;
    return;
  }
  stagedFrames.add(frame); stagedRevision=state.revision;
  renderStaging(); renderGallery();
}
function saveStagedGroup() {
  if (busy || saveBlocked || playing || trashView || groupDraft || !stagingTabVisible()) return;
  if (stagedFrames.size<2) { fail("Stage at least two exact frames with 1 before grouping."); return; }
  enqueueSave({video:recording,revision:stagedRevision,action:"create",
    frames:[...stagedFrames].sort((a,b)=>a-b),type:"same_scene",name:"",
    selected_unit:true},null,"/api/frame-group");
}

function fail(error) {
  $("error").hidden = false;
  $("error").textContent = error instanceof Error ? error.message : String(error);
  if (typeof reportDashboardError==="function") reportDashboardError($("error").textContent);
}
async function request(url, options) {
  const response = await fetch(url, {signal:AbortSignal.timeout(20000),...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function time(t) {
  return `${Math.floor(t / 60).toString().padStart(2,"0")}:${(t % 60).toFixed(3).padStart(6,"0")}`;
}
function nearest(t) {
  const times = videos[recording].times;
  let lo = 0, hi = frameBoundary();
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] < t) lo = mid + 1; else hi = mid;
  }
  return lo > 0 && Math.abs(times[lo-1]-t) < Math.abs(times[lo]-t) ? lo-1 : lo;
}
function imageURL(index, frame, width = 160) {
  return `/api/frame?video=${index}&frame=${frame}&width=${width}`;
}
function pointLabel(frame) {
  const labels = state.labels.filter(x => x.video === videos[recording].name && x.frame === frame);
  return labels.sort((a,b) => a.recorded_at.localeCompare(b.recorded_at)).at(-1);
}
function individualKeepFrames() {
  const latest=new Map();
  for (const label of state.labels) {
    if (label.video!==videos[recording].name) continue;
    const previous=latest.get(label.frame);
    if (!previous || label.recorded_at>=previous.recorded_at) latest.set(label.frame,label);
  }
  return [...latest.values()].filter(label=>label.label==="new_state" && !isTrashed(label.frame))
    .map(label=>label.frame);
}
function effectiveLabel(frame) {
  const point = pointLabel(frame);
  if (point) return point.label;
  let label;
  for (const interval of state.intervals) {
    if (interval.video !== videos[recording].name || interval.first_frame > frame || interval.last_frame < frame) continue;
    const event = interval.label_history.at(-1);
    if (!label || event.recorded_at > label.recorded_at) label = event;
  }
  return label?.label;
}
function recordingGroups() {
  return (state.frame_groups || []).filter(group=>group.video===videos[recording].name);
}
function displayGroupNumber(group) {
  return Number.isInteger(group.display_number) && group.display_number>0
    ? group.display_number : recordingGroups().indexOf(group)+1;
}
function categoryAssigned(targetType, target) {
  return (state.category_assignments || []).some(item=>
    item.category_id==="saga_map" && item.video===videos[recording].name &&
    item.target_type===targetType &&
    (targetType==="frame" ? item.frame===target : item.group_id===target.id));
}
function categoryControls(targetType, target) {
  const controls=document.createElement("div"); controls.className="category-controls";
  const groupTarget=targetType==="group";
  const title=groupTarget ? `Group ${displayGroupNumber(target)}` : `Frame ${target}`;
  const assigned=categoryAssigned(targetType,target);
  const badge=document.createElement("strong");
  badge.className=`category-badge category-${targetType}`;
  badge.textContent=`${groupTarget ? "Group reference" : "Direct frame"} category: ${assigned ? "Saga map" : "None"}`;
  const label=document.createElement("label"), input=document.createElement("input");
  input.type="checkbox"; input.checked=assigned;
  input.setAttribute("aria-label",`Saga map category for ${title}`);
  const text=document.createElement("span"); text.textContent="Saga map";
  label.append(input,text);
  const save=document.createElement("button");
  save.textContent="Save category";
  save.setAttribute("aria-label",`Save category for ${title}`);
  // Capture the exact rendered recording/revision, not a later viewer position.
  const sourceRecording=recording, revision=state.revision;
  save.onclick=()=>{
    if (busy || saveBlocked || playing) return;
    enqueueSave({video:sourceRecording,revision,category_id:"saga_map",
      target_type:targetType,assigned:input.checked,
      ...(groupTarget ? {group_id:target.id} : {frame:target})},null,"/api/category");
  };
  input.disabled=save.disabled=busy || saveBlocked || playing;
  const scope=document.createElement("small");
  scope.textContent=groupTarget
    ? "Group reference only — never tags members or changes Keep. Uncheck and Save category to clear."
    : "This exact frame only — independent of groups and Keep. Uncheck and Save category to clear.";
  controls.append(badge,label,save,scope);
  return controls;
}
function renderCurrentCategory() {
  $("current-category").replaceChildren(categoryControls("frame",current+1));
}
function mergeDestinationKey(frame) {
  return `${videos[recording].name}:${frame}`;
}
function renderMergeSelect(select, frame) {
  const groups=recordingGroups();
  const key=mergeDestinationKey(frame);
  const saved=mergeDestinations.get(key);
  const selected=saved==="new" || groups.some(group=>group.id===saved) ? saved : "new";
  const options=[];
  const fresh=document.createElement("option");
  fresh.value="new"; fresh.textContent="New group (yellow staging)"; options.push(fresh);
  for (const group of groups) {
    const option=document.createElement("option");
    option.value=group.id;
    const color=groupColor(group);
    const detail=`${groupNames[group.type]}${group.name ? `: ${group.name}` : ""}`;
    option.textContent=`Group ${displayGroupNumber(group)} — ${detail} [${color}]`;
    option.style.color=color;
    option.disabled=!!group._display_number_issue;
    if (group._display_number_issue) option.textContent+=` — ${group._display_number_issue}`;
    options.push(option);
  }
  select.replaceChildren(...options);
  select.value=selected;
  select.setAttribute("aria-label",`Merge destination for frame ${frame}`);
  select.onchange=()=>mergeDestinations.set(key,select.value);
}
function membershipDestinationSelect(source, frame) {
  const select=document.createElement("select");
  select.className="group-member-destination";
  select.setAttribute("aria-label",`Destination group for frame ${frame} from Group ${displayGroupNumber(source)}`);
  const prompt=document.createElement("option");
  prompt.value=""; prompt.textContent="Choose destination group"; prompt.selected=true;
  select.append(prompt);
  for (const group of recordingGroups()) {
    if (group.id===source.id) continue;
    const option=document.createElement("option");
    const color=groupColor(group);
    option.value=group.id;
    option.textContent=`Group ${displayGroupNumber(group)} — ${groupNames[group.type]}${group.name ? `: ${group.name}` : ""} [${color}]`;
    option.style.color=color;
    option.disabled=!!group._display_number_issue;
    select.append(option);
  }
  return select;
}
function groupMembershipControls(source, frame) {
  const actions=document.createElement("div"); actions.className="group-member-actions";
  const destination=membershipDestinationSelect(source,frame);
  const move=document.createElement("button");
  move.className="group-membership-action";
  move.textContent="Move";
  move.setAttribute("aria-label",`Move frame ${frame} from Group ${displayGroupNumber(source)}`);
  const hasDestination=recordingGroups().some(item=>item.id!==source.id && !item._display_number_issue);
  move.disabled=busy || saveBlocked || isTrashed(frame) || !hasDestination;
  destination.disabled=busy || saveBlocked || isTrashed(frame) || !hasDestination;
  destination.onchange=()=>{move.disabled=busy || saveBlocked || isTrashed(frame) || !destination.value;};
  move.onclick=()=>changeGroupMembership(source,frame,"move",destination.value);
  const remove=document.createElement("button");
  remove.className="group-membership-action";
  remove.textContent="Remove from group";
  remove.setAttribute("aria-label",`Remove frame ${frame} from Group ${displayGroupNumber(source)}`);
  remove.disabled=busy || saveBlocked || isTrashed(frame);
  remove.onclick=()=>changeGroupMembership(source,frame,"remove");
  actions.append(destination,move,remove);
  if (isTrashed(frame)) {
    const trashedNote=document.createElement("small");
    trashedNote.textContent="In Trash — restore before changing group membership.";
    actions.append(trashedNote);
  }
  return actions;
}
function changeGroupMembership(source, frame, memberAction, destinationId) {
  if (busy || saveBlocked) return;
  if (!source.frames.includes(frame)) {
    fail("That frame is no longer in this group. Reload saved decisions before changing membership."); return;
  }
  if (memberAction==="move" && (!destinationId || destinationId===source.id)) {
    fail("Choose a different existing destination group."); return;
  }
  const payload={
    video:recording,action:"membership",source_id:source.id,frame,
    member_action:memberAction,revision:state.revision
  };
  if (memberAction==="move") payload.destination_id=destinationId;
  if (new Set(source.frames.filter(member=>member!==frame)).size<2) {
    pendingMembershipChange=payload;
    $("group-dissolve-summary").textContent=
      `${memberAction==="move" ? "Moving" : "Removing"} frame ${frame} would leave Group ${displayGroupNumber(source)} with fewer than two members. Confirm to dissolve only that source group; cancel to leave every membership unchanged.`;
    $("group-dissolve-confirm").showModal();
    return;
  }
  enqueueSave(payload,null,"/api/frame-group");
}
function mergeFrame(frame, select) {
  if (busy || saveBlocked) return;
  if (trashView || isTrashed(frame)) {
    fail("Restore this frame from Trash before merging it; no group changed."); return;
  }
  if (videos[recording].retention_only &&
      !videos[recording].retained_frames.includes(frame)) {
    fail("This frame is unavailable in the retained-only archive."); return;
  }
  const destination=select.value || "new";
  mergeDestinations.set(mergeDestinationKey(frame),destination);
  if (destination==="new") {
    stageExactFrame(frame);
    return;
  }
  const group=recordingGroups().find(item=>item.id===destination);
  if (!group || group._display_number_issue) {
    fail("That group is unavailable or has a numbering conflict. Reload or save a unique group number first."); return;
  }
  enqueueSave({video:recording,action:"merge",id:group.id,frame,revision:state.revision},
    null,"/api/frame-group");
}
function clearGroupDraft() {
  groupDraft=null;
  $("group-draft").hidden=true;
  $("group-reviewed").checked=false;
}
function groupStamp(frame) {
  return `#${frame} | ${time(videos[recording].times[frame-1])}`;
}
function inspectGroupFrame(frame) {
  activateWorkspaceTab("editor");
  showFrame(frame-1,false);
  $("editor-title").scrollIntoView({block:"start",behavior:"instant"});
}
function groupFrameButton(frame) {
  const button=document.createElement("button");
  button.className="group-frame";
  const image=document.createElement("img");
  image.src=imageURL(recording,frame); image.alt=""; image.loading="lazy";
  const caption=document.createElement("span");
  caption.textContent=groupStamp(frame);
  button.append(image,caption);
  button.onclick=()=>inspectGroupFrame(frame);
  return button;
}
function memberTrashControls(frame) {
  const row=document.createElement("div"); row.className="member-trash-controls";
  const status=document.createElement("span");
  status.textContent=isTrashed(frame) ? "In recoverable Trash — membership preserved" : "Not in Trash";
  const button=document.createElement("button");
  button.className="trash-action";
  button.textContent=isTrashed(frame) ? "Restore from Trash" : "Move to Trash";
  button.setAttribute("aria-label",`${button.textContent}: frame ${frame}`);
  const sourceRecording=recording, revision=state.revision, trashed=isTrashed(frame);
  button.disabled=busy || saveBlocked || videos[recording].retention_only;
  button.onclick=()=>{
    if (busy || saveBlocked || videos[sourceRecording].retention_only) return;
    enqueueSave({video:sourceRecording,revision,frame,action:trashed ? "restore" : "trash"});
  };
  row.append(status,button); return row;
}
function renderGroupDraft() {
  $("group-draft").hidden=!groupDraft;
  if (!groupDraft) return;
  const frames=[...groupDraft.members].sort((a,b)=>a-b);
  $("group-count").textContent=`${frames.length} exact members: ${frames.map(groupStamp).join(", ") || "none"}. No frames between these members are implied.`;
  const fragment=document.createDocumentFragment();
  for (const frame of [...groupDraft.candidates].sort((a,b)=>a-b)) {
    const row=document.createElement("div");
    const label=document.createElement("label"), checkbox=document.createElement("input");
    checkbox.type="checkbox"; checkbox.checked=groupDraft.members.has(frame);
    checkbox.setAttribute("aria-label",`Include frame ${frame} in group`);
    checkbox.onchange=()=>toggleGroupMember(frame,checkbox.checked);
    const text=document.createElement("span"); text.textContent=`Include ${groupStamp(frame)}`;
    label.append(checkbox,text);
    row.append(groupFrameButton(frame),label);
    fragment.append(row);
  }
  $("group-members").replaceChildren(fragment);
  setControls();
}
function toggleGroupMember(frame, include) {
  if (!groupDraft || busy || saveBlocked) return;
  groupDraft.candidates.add(frame);
  if (include) groupDraft.members.add(frame); else groupDraft.members.delete(frame);
  $("group-reviewed").checked=false;
  renderGroupDraft(); renderGallery();
}
function startGroupDraft() {
  if (busy || saveBlocked || stagedFrames.size) return;
  const frames=individualKeepFrames().filter(frame=>
    !videos[recording].retention_only || videos[recording].retained_frames.includes(frame));
  groupDraft={video:recording,revision:state.revision,candidates:new Set(frames),members:new Set(frames)};
  $("group-type").value="same_scene"; $("group-name").value=""; $("group-reviewed").checked=false;
  renderGroupDraft(); renderGallery();
}
function renderGroups() {
  const fragment=document.createDocumentFragment(), groups=recordingGroups();
  for (const group of groups) {
    const details=document.createElement("details");
    details.open=inspectedGroup===group.id;
    details.ontoggle=()=>{inspectedGroup=details.open ? group.id : null;};
    const summary=document.createElement("summary");
    summary.textContent=`Group ${displayGroupNumber(group)} · ${groupNames[group.type]}${group.name ? `: ${group.name}` : ""} (${group.frames.length} frames)`;
    appendGroupColorChip(summary,group);
    details.append(summary);
    const numberControls=document.createElement("div"); numberControls.className="group-settings-controls";
    const numberLabel=document.createElement("label");
    numberLabel.textContent="Display group number";
    const numberInput=document.createElement("input");
    numberInput.type="number"; numberInput.min="1"; numberInput.step="1";
    numberInput.value=String(displayGroupNumber(group));
    numberInput.setAttribute("aria-label",`Display number for Group ${displayGroupNumber(group)}`);
    const colorLabel=document.createElement("label");
    colorLabel.textContent="Group color";
    const colorInput=document.createElement("input");
    colorInput.type="color"; colorInput.value=groupColor(group);
    colorInput.setAttribute("aria-label",`Color for Group ${displayGroupNumber(group)}`);
    const saveSettings=document.createElement("button");
    saveSettings.className="group-settings-save";
    saveSettings.textContent="Save group settings";
    saveSettings.setAttribute("aria-label",`Save Group ${displayGroupNumber(group)} settings`);
    saveSettings.onclick=()=>{
      const value=Number(numberInput.value);
      if (!Number.isInteger(value) || value<1) {
        fail("Group number must be a positive whole number."); return;
      }
      if (!groupColorPattern.test(colorInput.value)) {
        fail("Group color must be a six-digit #RRGGBB value."); return;
      }
      enqueueSave({video:recording,action:"configure",id:group.id,
        display_number:value,color:colorInput.value,revision:state.revision},null,"/api/frame-group");
    };
    $("merge-current").onclick=()=>mergeFrame(current+1,$("merge-current-select"));
    numberLabel.append(numberInput);
    colorLabel.append(colorInput);
    numberControls.append(numberLabel,colorLabel,saveSettings);
    numberControls.append(categoryControls("group",group));
    details.append(numberControls);
    if (group._display_number_issue) {
      const issue=document.createElement("p"); issue.className="group-number-error";
      issue.textContent=group._display_number_issue;
      details.append(issue);
    }
    if (group._display_color_issue) {
      const issue=document.createElement("p"); issue.className="group-color-error";
      issue.textContent=group._display_color_issue;
      details.append(issue);
    }
    const members=document.createElement("div"); members.className="group-inspection";
    for (const frame of group.frames) {
      const member=document.createElement("div"); member.className="group-member";
      member.append(groupFrameButton(frame));
      member.append(groupMembershipControls(group,frame),categoryControls("frame",frame),memberTrashControls(frame)); members.append(member);
    }
    const remove=document.createElement("button"); remove.className="group-ungroup";
    remove.textContent="Ungroup — metadata only";
    remove.disabled=busy || saveBlocked;
    remove.onclick=()=>{
      if (!busy && !saveBlocked) enqueueSave(
        {video:recording,action:"ungroup",id:group.id,revision:state.revision},null,"/api/frame-group");
    };
    details.append(members,remove); fragment.append(details);
  }
  if (!groups.length) {
    const empty=document.createElement("p"); empty.textContent="No manual groups in this recording.";
    fragment.append(empty);
  }
  $("group-list").replaceChildren(fragment);
}
function setControls() {
  document.querySelectorAll("[data-label]").forEach(button => button.disabled = busy || saveBlocked || !imageReady || playing || isTrashed(current+1));
  $("trash-current").disabled=busy || saveBlocked || !imageReady || playing;
  $("merge-current").disabled=busy || saveBlocked || !imageReady || playing || trashView || isTrashed(current+1);
  document.querySelectorAll(".trash-action").forEach(button=>button.disabled=busy || saveBlocked);
  for (const id of ["range-animation","range-repeat","range-unsure"]) {
    $(id).disabled = busy || saveBlocked || !imageReady || rangeStart === null || rangeEnd === null || rangeEnd <= rangeStart || playing;
  }
  for (const id of ["recording","apply-end","reload"]) $(id).disabled = busy;
  $("play").disabled = !videos[recording]?.preview || busy;
  document.querySelectorAll(".frame-select input").forEach(input => input.disabled = saveBlocked || cleanupBusy);
  $("group-start").disabled=busy || saveBlocked || stagedFrames.size>0;
  $("stage-current").disabled=!canStage();
  $("stage-save").disabled=busy || saveBlocked || playing || trashView || !!groupDraft || stagedFrames.size<2;
  $("editor-stage-save").disabled=$("stage-save").disabled;
  $("stage-clear").disabled=busy || saveBlocked || !stagedFrames.size;
  $("group-save").disabled=busy || saveBlocked || !groupDraft || groupDraft.members.size<2 || !$("group-reviewed").checked;
  for (const id of ["group-type","group-name","group-reviewed","group-cancel"]) $(id).disabled=busy || saveBlocked;
  document.querySelectorAll(".group-ungroup,.group-toggle,.group-settings-save,.group-membership-action,.group-member-destination,#group-members input,.group-settings-controls input").forEach(input=>input.disabled=busy || saveBlocked || input.disabled);
  document.querySelectorAll(".category-controls input,.category-controls button").forEach(input=>input.disabled=busy || saveBlocked || playing);
  $("groups-recording").disabled=busy;
  $("cleanup-preview").disabled=busy || saveBlocked || cleanupBusy || playing || !!groupDraft ||
    stagedFrames.size>0 || videos[recording]?.retention_only;
  if(videos[recording]?.retention_only) {
    document.querySelectorAll("[data-label],.trash-action,.frame-select input").forEach(e=>e.disabled=true);
    for(const id of ["trash-current","trash-view","range-animation","range-repeat","range-unsure"])$(id).disabled=true;
  }
}
function stop() {
  playbackRequest++;
  video.pause(); playing = false; video.hidden = true;
  $("frame-image").hidden = !$("frame-image").getAttribute("src");
  $("play").textContent = "Play"; setControls();
}
function updatePosition() {
  $("editor-recording").textContent=`Recording ${recording+1}: ${videos[recording].name} — shared with gallery`;
  renderCurrentCategory();
  renderStaging();
  renderMergeSelect($("merge-current-select"),current+1);
  $("position").textContent = `Frame ${(current+1).toLocaleString()} / ${(frameBoundary()+1).toLocaleString()} | ${time(videos[recording].times[current])}`;
  $("timeline").value = current;
  const label = effectiveLabel(current+1);
  $("current-label").textContent = isTrashed(current+1) ? "In Trash" : label ? names[label] : "Unlabeled";
  $("trash-current").textContent = isTrashed(current+1) ? "Restore frame" : "Move to Trash";
  $("original").href = imageURL(recording,current+1,0);
}
function showFrame(index, syncGallery = $("editor-tab").hidden) {
  const previous=current;
  const boundary=frameBoundary();
  stop(); current = Math.max(0, Math.min(boundary, index));
  if(videos[recording].retention_only) {
    const retained=videos[recording].retained_frames.map(n=>n-1).filter(n=>n<=boundary);
    const directional=current>previous ? retained.find(n=>n>=current) : [...retained].reverse().find(n=>n<=current);
    current=directional ?? retained.reduce((a,b)=>Math.abs(a-current)<=Math.abs(b-current)?a:b);
  }
  imageReady = false; setControls(); updatePosition();
  $("note").value = pointLabel(current+1)?.note || "";
  $("image-status").hidden = false; $("image-status").textContent = "Loading exact source frame...";
  const requestId = ++currentImageRequest;
  const image = new Image();
  image.onload = () => {
    if (requestId !== currentImageRequest) return;
    $("frame-image").src = image.src;
    $("frame-image").hidden = false;
    $("frame-image").alt = `Source frame ${current+1} at ${time(videos[recording].times[current])}`;
    imageReady = true; $("image-status").hidden = true; setControls();
  };
  image.onerror = () => {
    if (requestId !== currentImageRequest) return;
    $("image-status").textContent = "Source image unavailable. Check the external drive.";
    fail("Could not load this exact frame. No label was saved."); setControls();
  };
  image.src = imageURL(recording,current+1,660);
  if (syncGallery) {
    const frames=visibleGalleryEntries();
    let at = frames.findIndex(x => x.index >= current || x.frames?.includes(current+1));
    if (at < 0) at = frames.length-1;
    page = Math.floor(at/perPage);
  }
  renderGallery();
}
function buildGallery() {
  galleryFrames = [];
  if(videos[recording].retention_only) {
    galleryFrames=videos[recording].retained_frames.filter(n=>n<=last+1).map(n=>n-1);
    trashView=false;
    return;
  }
  const approved=individualKeepFrames().filter(frame=>frame<=last+1).map(frame=>frame-1);
  if ($("spacing").value === "approved") {
    galleryFrames=[...new Set(approved)].sort((a,b)=>a-b);
    return;
  } else if (["suggested","uncertain"].includes($("spacing").value)) {
    galleryFrames=(state.preselections || []).filter(p=>p.video===videos[recording].name)
      .flatMap(p=>p.frames)
      .filter(f=>$("spacing").value==="uncertain" ? f.review_group==="uncertain" : f.review_group!=="uncertain")
      .map(f=>f.source_frame-1).filter(i=>i<=last);
    galleryFrames=[...new Set([...galleryFrames,...approved])].sort((a,b)=>a-b);
    return;
  } else if ($("spacing").value === "all") {
    galleryFrames = Array.from({length:last+1}, (_,i) => i);
  } else {
    for (let t = 0; t <= endTime; t++) galleryFrames.push(nearest(t));
    galleryFrames.push(last);
    galleryFrames = [...new Set(galleryFrames)];
  }
  for (const window of state.curated_windows || []) {
    if (window.video===videos[recording].name) {
      galleryFrames.push(...window.keep_frames.filter(frame=>frame<=last+1).map(frame=>frame-1));
    }
  }
  galleryFrames=[...new Set([...galleryFrames,...approved])].sort((a,b)=>a-b);
}
function renderGallery() {
  const visibleFrames = visibleGalleryEntries();
  const total = Math.max(1,Math.ceil(visibleFrames.length/perPage));
  page = Math.max(0,Math.min(total-1,page));
  const all = $("spacing").value === "all";
  const curated=(state.curated_windows || []).some(w=>w.video===videos[recording].name);
  const suggested=["suggested","uncertain"].includes($("spacing").value);
  $("gallery-info").textContent = `${trashView ? "Trash in this review window. Restore any frame. Originals are unchanged." : $("spacing").value==="approved" ? `${visibleFrames.length} approved frames in this review window.` : suggested ? `${visibleFrames.length} analyst suggestions, not confirmed selections. Tick to keep; all source frames remain available in Every source frame.` : all ? "Every source frame except those in Trash." : curated ? "Curated frames at exact source positions, plus any unreviewed overview frames outside the curated window." : "Overview: one image per second, excluding Trash. Choose Every source frame to inspect between them."} Page ${page+1} of ${total}.`;
  const trashCount=(state.trashed || []).filter(x=>x.video===videos[recording].name).length;
  if(suggested && !trashView) {
    const uncertain=$("spacing").value==="uncertain";
    const secondary=(state.preselections || []).filter(p=>p.video===videos[recording].name)
      .flatMap(p=>p.frames).filter(f=>f.review_group==="uncertain" && f.source_frame<=last+1 && !isTrashed(f.source_frame)).length;
    $("gallery-info").textContent=`${visibleFrames.length} frames: ${uncertain ? "uncertain candidates (not rejected)" : "analyst suggestions"} plus your exact approved frames. Only checked frames are approved.${secondary && !uncertain ? ` ${secondary} secondary candidates remain in Uncertain candidates.` : ""} Page ${page+1} of ${total}.`;
  }
  $("trash-view").textContent=trashView ? "Back to frames" : `Trash (${trashCount})`;
  $("trash-view").setAttribute("aria-pressed",String(trashView));
  if(videos[recording].retention_only) {
    $("gallery-info").textContent=`Retained-only archive: ${visibleFrames.length} selected originals. Non-selections were permanently deleted. Decisions are read-only; group/category metadata is editable. Page ${page+1} of ${total}.`;
    $("trash-view").textContent="Permanently cleaned";
  }
  if (!trashView && unitGroups().length) {
    $("gallery-info").textContent=`${visibleFrames.length} display units in this window (individual frames and selected groups). Groups count once per page; expanded members exclude Trash and frames outside this window. Only individual Keep points feed diagrams. Page ${page+1} of ${total}.`;
  }
  $("page-back").disabled = page === 0; $("page-next").disabled = page === total-1;
  $("page-number").value=page+1;
  $("page-number").max=total;
  const fragment = document.createDocumentFragment();
  for (const entry of visibleFrames.slice(page*perPage,(page+1)*perPage)) {
    const {index,group,frames}=entry;
    if (group) {
      const card=document.createElement("article");
      card.className=`frame-card selected group-card${frames.includes(current+1) ? " current" : ""}`;
      card.dataset.group=group.id;
      // Existing QA drag handling expects one exact source frame, not a group.
      card.dataset.frame=frames[0];
      const title=document.createElement("strong");
      title.textContent=`Group ${displayGroupNumber(group)} · ${groupNames[group.type]}${group.name ? `: ${group.name}` : ""} · ${frames.length}/${group.frames.length} members in view`;
      appendGroupColorChip(title,group);
      const preview=groupFrameButton(frames[0]);
      const expand=document.createElement("button");
      expand.id=`expand-${group.id}`;
      const expanded=expandedGroups.has(group.id);
      expand.textContent=expanded ? "Collapse members" : "Expand members";
      expand.setAttribute("aria-expanded",String(expanded));
      expand.onclick=()=>{
        if (expanded) expandedGroups.delete(group.id); else expandedGroups.add(group.id);
        renderGallery();
        $(expand.id)?.focus();
      };
      const remove=document.createElement("button");
      remove.className="group-ungroup"; remove.textContent="Ungroup — restore individual view";
      remove.onclick=()=>{
        if (!busy && !saveBlocked) enqueueSave(
          {video:recording,action:"ungroup",id:group.id,revision:state.revision},null,"/api/frame-group");
      };
      card.append(title,preview,expand,remove);
      if (expanded) {
        const children=document.createElement("div"); children.className="group-inspection";
        for (const frame of frames) {
          const member=document.createElement("div"); member.className="group-member";
          member.append(groupFrameButton(frame),groupMembershipControls(group,frame),categoryControls("frame",frame),memberTrashControls(frame));
          children.append(member);
        }
        card.append(children);
      }
      card.append(categoryControls("group",group));
      fragment.append(card);
      continue;
    }
    const card = document.createElement("article");
    const button = document.createElement("button");
    const label = effectiveLabel(index+1);
    const selected = pendingSelections.has(index+1) ? pendingSelections.get(index+1) : label === "new_state";
    card.className = `frame-card ${index === current ? "current" : ""} ${selected ? "selected" : ""} ${stagedFrames.has(index+1) ? "staged" : ""}`;
    card.dataset.frame = index+1;
    button.className = "frame-preview";
    button.setAttribute("aria-label",`Open frame ${index+1} at ${time(videos[recording].times[index])}`);
    const image = document.createElement("img");
    image.src = imageURL(recording,index+1); image.alt = ""; image.loading = "lazy";
    image.onerror = () => { image.alt = "Image unavailable"; };
    const caption = document.createElement("span");
    caption.textContent = `#${index+1} | ${time(videos[recording].times[index])}`;
    button.append(image,caption);
    if (suggested) {
      const entry=(state.preselections || []).filter(p=>p.video===videos[recording].name)
        .flatMap(p=>p.frames).find(f=>f.source_frame===index+1);
      if (entry) {
        const reason=document.createElement("span");
        reason.textContent=entry.claim;
        button.append(reason);
      }
    }
    const previewText = document.createElement("span");
    previewText.textContent = "Enlarge";
    button.append(previewText);
    if (label && label !== "new_state") {
      const status = document.createElement("strong"); status.textContent = names[label]; button.append(status);
    }
    button.onclick = () => {
      inspectGroupFrame(index+1);
    };
    const selection = document.createElement("label");
    selection.className = "frame-select";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox"; checkbox.checked = selected; checkbox.disabled = saveBlocked || cleanupBusy || videos[recording].retention_only;
    checkbox.setAttribute("aria-label",`Keep frame ${index+1}`);
    const selectionText = document.createElement("span");
    selectionText.textContent = selected ? "Selected" : "Select frame";
    checkbox.onchange = () => {
      if (cleanupBusy) { checkbox.checked=selected; return; }
      const checked = checkbox.checked;
      pendingSelections.set(index+1,checked);
      card.classList.toggle("selected",checked);
      selectionText.textContent = checked ? "Selected" : "Select frame";
      enqueueSave({
        video:recording,frame:index+1,end_frame:index+1,
        label:checked ? "new_state" : "uncertain",
        note:checked ? "" : "Selection cleared using the gallery checkbox; not a rejection."
      }, {frame:index+1,checked});
    };
    selection.append(checkbox,selectionText);
    const trash = document.createElement("button");
    trash.className="trash-action";
    trash.textContent=trashView ? "Restore" : "Move to Trash";
    trash.disabled=busy || saveBlocked;
    trash.onclick=()=>enqueueSave({video:recording,frame:index+1,action:trashView ? "restore" : "trash"});
    const mergeControls=document.createElement("div"); mergeControls.className="frame-merge-controls";
    const mergeLabel=document.createElement("label");
    mergeLabel.textContent="Merge destination";
    const mergeSelect=document.createElement("select");
    mergeSelect.className="frame-merge-select"; mergeSelect.dataset.frame=String(index+1);
    renderMergeSelect(mergeSelect,index+1);
    const mergeButton=document.createElement("button");
    mergeButton.textContent="Merge";
    mergeButton.setAttribute("aria-label",`Merge frame ${index+1}`);
    mergeButton.disabled=busy || saveBlocked || trashView || isTrashed(index+1);
    mergeButton.title=trashView || isTrashed(index+1)
      ? "Restore this frame before merging it." : "";
    mergeButton.onclick=()=>mergeFrame(index+1,mergeSelect);
    mergeLabel.append(mergeSelect); mergeControls.append(mergeLabel,mergeButton);
    card.append(button);
    if (suggested && !trashView) {
      for (const delta of [-0.5,0.5]) {
        const nearby=document.createElement("button");
        nearby.textContent=`Inspect ${delta>0 ? "+" : ""}${delta}s`;
        nearby.setAttribute("aria-label",`Inspect ${delta} seconds from frame ${index+1}`);
        nearby.onclick=()=>{
          activateWorkspaceTab("editor");
          showFrame(nearest(videos[recording].times[index]+delta),false);
        };
        card.append(nearby);
      }
    }
    if (!trashView) card.append(selection);
    card.append(trash,mergeControls);
    card.append(categoryControls("frame",index+1));
    for (const group of recordingGroups().filter(group=>group.frames.includes(index+1))) {
      const badge=document.createElement("button"); badge.className="group-badge";
      badge.textContent=`Group ${displayGroupNumber(group)} · ${groupNames[group.type]}${group.name ? `: ${group.name}` : ""} (${group.frames.length})`;
      appendGroupColorChip(badge,group);
      badge.setAttribute("aria-label",`Open Group ${displayGroupNumber(group)}, ${groupNames[group.type]}${group.name ? `: ${group.name}` : ""}, color ${groupColor(group)}`);
      badge.onclick=()=>{
        inspectedGroup=group.id; renderGroups();
        activateWorkspaceTab("groups");
        $("groups-tab").scrollIntoView({block:"start",behavior:"instant"});
      };
      card.append(badge);
    }
    if (groupDraft) {
      const member=document.createElement("button"); member.className="group-toggle";
      const included=groupDraft.members.has(index+1);
      member.textContent=included ? "Remove from group draft" : "Add to group draft";
      member.setAttribute("aria-pressed",String(included));
      member.disabled=busy || saveBlocked;
      member.onclick=()=>toggleGroupMember(index+1,!included);
      card.append(member);
    }
    if (stagedFrames.has(index+1)) {
      const badge=document.createElement("strong");
      badge.textContent="Yellow · staged for group"; card.append(badge);
    }
    fragment.append(card);
  }
  $("gallery").replaceChildren(fragment);
  if (!visibleFrames.length) {
    const empty=document.createElement("p");
    empty.textContent=trashView ? "Trash is empty in this review window." : suggested ? "No analyst suggestions in this window. Choose another gallery view." : "No frames here. Check Trash or expand the review window.";
    fragment.append(empty);
    $("gallery").replaceChildren(fragment);
  }
  const selectedFrames = new Set(individualKeepFrames());
  for (const [frame,checked] of pendingSelections) {
    if (checked) selectedFrames.add(frame); else selectedFrames.delete(frame);
  }
  $("selection-total").textContent = `${selectedFrames.size} selected in this recording${unitGroups().length ? ` + ${unitGroups().length} selected groups (independent of Keep)` : ""}${pendingSaves ? ` | ${pendingSaves} changes saving...` : ""}`;
  const protectedFrames=new Set(selectedFrames);
  for (const group of unitGroups()) for (const frame of group.frames) {
    if (!isTrashed(frame)) protectedFrames.add(frame);
  }
  $("cleanup-summary").textContent=videos[recording].retention_only
    ? "Retained-only archive — visibility changes are disabled."
    : `${videos[recording].name}: ${protectedFrames.size} selected exact frames protected (Keep + selected group union); ${trashCount} already in Trash; ${videos[recording].times.length-protectedFrames.size-trashCount} unselected frames eligible across the whole recording.${pendingSaves ? " Pending saves — wait for confirmed counts." : ""}`;
  setControls();
}
function renderSaved() {
  if (stagedFrames.size && stagedRevision!==state.revision) clearStaging();
  // Learning closeout also refreshes state through this shared rendering hook.
  if (groupDraft && (groupDraft.video!==recording || groupDraft.revision!==state.revision)) {
    clearGroupDraft();
  }
  const name = videos[recording].name, fragment = document.createDocumentFragment();
  const retained=videos[recording].retained_frames;
  const points = state.labels.filter(x => x.video === name && !isTrashed(x.frame) && (!retained || retained.includes(x.frame)));
  const intervals = videos[recording].retention_only ? [] : state.intervals.filter(x => x.video === name);
  $("counts").textContent = `${points.filter(x=>x.label==="new_state").length} kept / ${points.length+intervals.length} decisions`;
  const entries = [
    ...points.map(x => ({frame:x.frame,label:x.label,note:x.note,time:x.time})),
    ...intervals.map(x => ({frame:x.first_frame,end:x.last_frame,time:x.start_time,
      label:x.label_history.at(-1).label,note:x.label_history.at(-1).statement}))
  ].sort((a,b) => a.frame-b.frame);
  for (const entry of entries) {
    const button = document.createElement("button"); button.className = "saved-item";
    const image = document.createElement("img");
    image.src = imageURL(recording,entry.frame); image.loading = "lazy"; image.alt = "";
    const text = document.createElement("span");
    const label = document.createElement("strong");
    label.textContent = `${names[entry.label]} | ${time(entry.time)}`;
    const detail = document.createElement("small");
    detail.textContent = `Frame ${entry.frame}${entry.end ? `-${entry.end}` : ""}${entry.note ? `: ${entry.note}` : ""}${entry.time > endTime ? " (outside review window)" : ""}`;
    text.append(label,detail); button.append(image,text);
    button.onclick = () => {
      inspectGroupFrame(entry.frame);
      if (entry.end) {
        rangeStart=entry.frame-1; rangeEnd=entry.end-1; renderRange();
      }
    };
    fragment.append(button);
  }
  if (!entries.length) {
    const p=document.createElement("p"); p.textContent="No decisions yet. Choose a frame and click Keep."; fragment.append(p);
  }
  $("saved-list").replaceChildren(fragment);
  renderGroups();
  renderGallery();
  renderMergeSelect($("merge-current-select"),current+1);
  renderCurrentCategory();
  document.dispatchEvent(new Event("review-updated"));
}
function renderRange() {
  const stamp=i=>`#${i+1} (${time(videos[recording].times[i])})`;
  $("range-info").textContent=`Start: ${rangeStart===null?"not set":stamp(rangeStart)}. End: ${rangeEnd===null?"not set":stamp(rangeEnd)}.`;
  setControls();
}
function saveLabel(label, interval = false) {
  if (busy || !imageReady || playing || saveBlocked || (!interval && isTrashed(current+1))) return;
  const first = interval ? rangeStart : current, end = interval ? rangeEnd : current;
  if (first === null || end === null || first > end) { fail("Set a valid interval first."); return; }
  enqueueSave({video:recording,frame:first+1,end_frame:end+1,label,note:$("note").value});
}
function enqueueSave(payload, selection, endpoint = "/api/annotation") {
  if (cleanupBusy) return;
  pendingSaves++; busy=true; setControls();
  $("save-status").textContent=endpoint==="/api/category" ? "Saving category metadata to project..." : "Saving selections to project...";
  saveQueue = saveQueue.then(async () => {
    try {
      if (saveBlocked) return;
      const data = await request(endpoint,{
        method:"POST",headers:{"Content-Type":"application/json","X-Review-Token":state.token},
        body:JSON.stringify({...payload,revision:payload.revision ?? state.revision})
      });
      const operationMessage=data.operation_message;
      state = data;
      clearGroupDraft();
      if (payload.selected_unit) clearStaging();
      else if (stagedFrames.size) {
        stagedFrames=new Set([...stagedFrames].filter(frame=>!isTrashed(frame)));
        stagedRevision=state.revision;
        renderStaging();
      }
      if (selection && pendingSelections.get(selection.frame)===selection.checked) {
        pendingSelections.delete(selection.frame);
      }
      $("save-status").textContent=operationMessage || `Saved to project at ${new Date().toLocaleTimeString()}`;
      buildGallery();
      if (!selection && payload.label==="new_state") {
        trashView=false;
        const at=visibleGalleryEntries().findIndex(entry=>entry.index===payload.frame-1 || entry.frames?.includes(payload.frame));
        if(at>=0)page=Math.floor(at/perPage);
      }
      renderSaved(); updatePosition();
      if (payload.selected_unit) {
        trashView=false;
        const at=visibleGalleryEntries().findIndex(entry=>entry.group?.frames.includes(payload.frames[0]));
        if (at>=0) page=Math.floor(at/perPage);
      }
    } catch (error) {
      saveBlocked=true; pendingSelections.clear(); clearGroupDraft(); clearStaging();
      $("save-status").textContent="Some changes were not confirmed. Yellow draft cleared; reload decisions and inspect saved groups and categories before retrying. No automatic retry.";
      fail(error);
    } finally {
      pendingSaves--; busy=pendingSaves>0;
      if (pendingSaves && !saveBlocked) $("save-status").textContent=`Saving ${pendingSaves} more changes...`;
      renderGroups(); renderGallery(); setControls();
    }
  });
}
function cancelCleanup() {
  cleanupPreview=null; cleanupBusy=false; busy=pendingSaves>0;
  $("cleanup-confirm").close();
  renderGroups(); renderGallery(); setControls();
}
async function previewCleanup() {
  if (busy || saveBlocked || playing || groupDraft || stagedFrames.size ||
      videos[recording].retention_only) return;
  cleanupBusy=true; busy=true; setControls();
  try {
    cleanupPreview=await request("/api/cleanup",{
      method:"POST",headers:{"Content-Type":"application/json","X-Review-Token":state.token},
      body:JSON.stringify({action:"preview",video:recording,revision:state.revision})
    });
    const p=cleanupPreview;
    $("cleanup-confirm-summary").textContent=`Recording ${p.video+1}: ${p.name}. Move exactly ${p.eligible_count} unselected frames to recoverable Trash. Preserve ${p.selected_count} selected exact frames (non-trashed Keep + selected group union) and ${p.existing_trash_count} existing Trash frames out of ${p.source_count} source frames.`;
    $("cleanup-apply").disabled=p.eligible_count===0;
    $("cleanup-confirm").showModal();
  } catch(error) {
    cancelCleanup(); fail(error);
  }
}
async function applyCleanup() {
  if (!cleanupPreview || !cleanupBusy || !cleanupPreview.eligible_count) return;
  const preview=cleanupPreview; cleanupPreview=null;
  $("cleanup-apply").disabled=true;
  $("cleanup-confirm").close();
  $("save-status").textContent="Moving unselected frames to recoverable Trash. No automatic retry.";
  try {
    const result=await request("/api/cleanup",{
      method:"POST",headers:{"Content-Type":"application/json","X-Review-Token":state.token},
      body:JSON.stringify({action:"apply",video:preview.video,revision:preview.revision,
        preview_token:preview.preview_token,confirm:true})
    });
    state=result;
    buildGallery(); renderSaved(); updatePosition();
    $("save-status").textContent=result.operation_message;
  } catch(error) {
    saveBlocked=true;
    $("save-status").textContent="Cleanup was not confirmed. Reload decisions and inspect Trash before any manual retry. No automatic retry.";
    fail(error);
  } finally {
    cleanupBusy=false; busy=false; renderGroups(); renderGallery(); setControls();
  }
}
function applyWindow() {
  const value = Number($("end").value), maximum=videos[recording].times.at(-1);
  if(videos[recording].retention_only && value<videos[recording].times[videos[recording].retained_frames[0]-1]) {
    fail("The review window must include at least one retained frame.");return;
  }
  if (!Number.isFinite(value) || value < 0 || value > maximum) {
    fail(`Review end must be between 0 and ${maximum.toFixed(3)} seconds.`); return;
  }
  endTime=value;
  last=videos[recording].times.findLastIndex(t=>t<=endTime);
  $("timeline").max=last;
  rangeStart=rangeEnd=null; renderRange(); buildGallery();
  $("source-info").textContent=`Review window 00:00-${time(endTime)}. ${(last+1).toLocaleString()} source frames. Originals unchanged.${videos[recording].preview ? "" : " Video preview is available for recording 1 only; use the frame gallery here."}`;
  if(videos[recording].retention_only)$("source-info").textContent=`Completed retained-only archive: ${videos[recording].retained_frames.length} selected original frames preserved. Non-selected images and local preview permanently deleted; source timestamps remain historical provenance.`;
  showFrame(Math.min(current,last)); renderSaved();
}
function selectRecording() {
  clearGroupDraft(); clearStaging(); expandedGroups.clear(); inspectedGroup=null;
  stop(); recording=Number($("recording").value); current=page=0;trashView=false;
  $("groups-recording").value=String(recording);
  const v=videos[recording];
  $("spacing").value=(state.preselections || []).some(p=>p.video===v.name) ? "suggested" : "second";
  $("end").value=v.retention_only ? v.times.at(-1) : v.default_end; $("end").max=v.times.at(-1);
  $("split-warning").hidden=state.split_by_video[v.name]!=="holdout";
  video.removeAttribute("src");
  if (v.preview) video.src="/preview";
  document.dispatchEvent(new Event("recording-changed"));
  applyWindow(); setControls();
}
async function initialize() {
  [videos,state]=await Promise.all([request("/api/videos"),request("/api/state")]);
  videos.forEach((v,i) => {
    const option=document.createElement("option"); option.value=i;
    option.textContent=`${i+1}. ${v.name.replace("ScreenRecording_","")}`;
    $("recording").append(option);
    const groupOption=document.createElement("option"); groupOption.value=i;
    groupOption.textContent=option.textContent; $("groups-recording").append(groupOption);
  });
  $("saved-path").textContent="Saved in data/soda-keyframe-calibration/dataset.json. No model training is triggered.";
  $("save-status").textContent="Saved decisions loaded from project.";
  const requested=new URLSearchParams(location.search).get("video");
  if (requested!==null && /^\d+$/.test(requested) && Number(requested)<videos.length) {
    $("recording").value=requested;
  }
  selectRecording();
  document.dispatchEvent(new Event("review-ready"));
}
$("recording").onchange=selectRecording;
$("cleanup-preview").onclick=previewCleanup;
$("cleanup-apply").onclick=applyCleanup;
$("cleanup-cancel").onclick=cancelCleanup;
$("cleanup-confirm").oncancel=event=>{event.preventDefault();cancelCleanup();};
$("groups-recording").onchange=()=>{
  $("recording").value=$("groups-recording").value;
  selectRecording();
};
$("apply-end").onclick=applyWindow;
$("previous").onclick=()=>showFrame(current-1);
$("next").onclick=()=>showFrame(current+1);
$("back-second").onclick=()=>showFrame(nearest(videos[recording].times[current]-1));
$("next-second").onclick=()=>showFrame(nearest(videos[recording].times[current]+1));
function jumpSeconds(delta) {
  showFrame(nearest(videos[recording].times[current]+delta));
}
$("back-two").onclick=()=>jumpSeconds(-2);
$("next-two").onclick=()=>jumpSeconds(2);
$("back-two-half").onclick=()=>jumpSeconds(-2.5);
$("next-two-half").onclick=()=>jumpSeconds(2.5);
$("timeline").oninput=e=>showFrame(Number(e.target.value));
$("spacing").onchange=()=>{buildGallery();showFrame(current);};
$("page-back").onclick=()=>{page--;renderGallery();};
$("page-next").onclick=()=>{page++;renderGallery();};
$("sort-frames").onclick=()=>{
  if (pendingSaves || saveBlocked) {
    fail("Wait for saves to finish, or reload decisions, before sorting.");return;
  }
  buildGallery();
  page=0;
  renderGallery();renderSaved();
  $("save-status").textContent="Frames sorted by source time. Selections unchanged.";
  $("gallery").scrollIntoView({block:"start",behavior:"instant"});
};
$("page-go").onclick=()=>{
  const value=Number($("page-number").value);
  if (!Number.isInteger(value) || value<1 || value>Number($("page-number").max)) {
    fail("Choose a valid gallery page.");return;
  }
  page=value-1;renderGallery();
};
$("trash-view").onclick=()=>{trashView=!trashView;page=0;renderGallery();};
$("trash-current").onclick=()=>{
  if (!busy && !saveBlocked && imageReady && !playing)
    enqueueSave({video:recording,frame:current+1,action:isTrashed(current+1) ? "restore" : "trash"});
};
$("range-start").onclick=()=>{rangeStart=current;renderRange();};
$("range-end").onclick=()=>{rangeEnd=current;renderRange();};
$("note").oninput=()=>{$("save-status").textContent="Note not saved yet. Choose a label to save it.";};
document.querySelectorAll("[data-label]").forEach(button=>button.onclick=()=>saveLabel(button.dataset.label));
$("range-animation").onclick=()=>saveLabel("animation",true);
$("range-repeat").onclick=()=>saveLabel("same_state",true);
$("range-unsure").onclick=()=>saveLabel("uncertain",true);
$("reload").onclick=async()=>{
  if (busy) return;
  clearGroupDraft(); clearStaging(); expandedGroups.clear();
  try {
    state=await request("/api/state");saveBlocked=false;pendingSelections.clear();
    buildGallery();renderSaved();renderGallery();updatePosition();setControls();
    $("save-status").textContent="Saved decisions reloaded."; $("error").hidden=true;
  } catch(error){fail(error);}
};
$("group-start").onclick=startGroupDraft;
$("stage-current").onclick=toggleStaging;
$("editor-stage-save").onclick=saveStagedGroup;
$("editor-back").onclick=()=>activateWorkspaceTab("review");
$("stage-save").onclick=saveStagedGroup;
$("stage-clear").onclick=()=>{clearStaging();renderGallery();};
$("group-cancel").onclick=()=>{clearGroupDraft();renderGallery();setControls();};
$("group-dissolve-approve").onclick=()=>{
  if (!pendingMembershipChange || busy || saveBlocked) return;
  const payload={...pendingMembershipChange,dissolve_ack:true};
  pendingMembershipChange=null;
  $("group-dissolve-confirm").close();
  enqueueSave(payload,null,"/api/frame-group");
};
$("group-dissolve-cancel").onclick=()=>{
  pendingMembershipChange=null;
  $("group-dissolve-confirm").close();
};
$("group-dissolve-confirm").onclose=()=>{pendingMembershipChange=null;};
$("group-reviewed").onchange=setControls;
for (const id of ["group-name","group-type"]) $(id).oninput=()=>{
  $("group-reviewed").checked=false; setControls();
};
$("group-save").onclick=()=>{
  if (!groupDraft || busy || saveBlocked || groupDraft.members.size<2 || !$("group-reviewed").checked) return;
  enqueueSave({
    video:groupDraft.video,revision:groupDraft.revision,action:"create",
    frames:[...groupDraft.members].sort((a,b)=>a-b),
    type:$("group-type").value,name:$("group-name").value
  },null,"/api/frame-group");
};
$("export").onclick=async()=>{
  try {
    const data=await request("/api/export");
    const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));
    const a=document.createElement("a");a.href=url;a.download="soda-frame-feedback.json";a.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  } catch(error){fail(error);}
};
$("play").onclick=async()=>{
  if (playing) {stop();showFrame(nearest(video.currentTime));return;}
  const requestId=++playbackRequest;
  video.currentTime=videos[recording].times[current>=frameBoundary()?0:current];
  video.playbackRate=Number($("speed").value);
  try {
    await video.play();
    if (requestId!==playbackRequest || $("editor-tab").hidden) return;
    playing=true;video.hidden=false;$("frame-image").hidden=true;
    $("image-status").hidden=true;$("play").textContent="Pause";setControls();
  } catch(error){
    if (requestId!==playbackRequest) return;
    stop();fail(`Preview playback failed: ${error.message}`);
  }
};
video.ontimeupdate=()=>{
  if (!playing) return;
  const boundary=frameBoundary(), limit=videos[recording].times[boundary];
  current=nearest(Math.min(video.currentTime,limit));updatePosition();
  if (video.currentTime>=limit) {stop();showFrame(boundary);}
};
video.onended=()=>{stop();showFrame(current);};
video.onerror=()=>{if(video.getAttribute("src"))fail("Video preview unavailable. Exact-frame navigation is still available.");};
$("speed").onchange=()=>video.playbackRate=Number($("speed").value);
document.addEventListener("keydown",event=>{
  if (!stagingTabVisible() || cleanupPreview || cleanupBusy || pendingMembershipChange) return;
  if (["INPUT","TEXTAREA","SELECT"].includes(event.target.tagName) || event.target.isContentEditable ||
      event.target.closest?.('[contenteditable]:not([contenteditable="false"]),[role="textbox"]') ||
      event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || event.repeat || event.isComposing ||
      busy || !state) return;
  const key=event.key.toLowerCase();
  if (key==="m"){event.preventDefault();saveStagedGroup();}
  else if ($("editor-tab").hidden) return;
  else if (key==="1"){event.preventDefault();toggleStaging();}
  else if (key==="arrowright"){event.preventDefault();showFrame(current+1);}
  else if(key==="arrowleft"){event.preventDefault();showFrame(current-1);}
  else if(key===" " && !["BUTTON","SUMMARY","A"].includes(event.target.tagName)){event.preventDefault();if(!$("play").disabled)$("play").click();}
  else if({k:1,a:1,r:1,u:1}[key]){event.preventDefault();saveLabel({k:"new_state",a:"animation",r:"same_state",u:"uncertain"}[key]);}
});
for (const tab of ["review","editor","groups","library","qa","learning"]) {
  $(`tab-${tab}`).addEventListener("click",event=>{
    event.stopImmediatePropagation();
    if (typeof workspaceTab==="function" && ["review","library","qa","learning"].includes(tab)) workspaceTab(tab);
    activateWorkspaceTab(tab);
  },true);
}
$("tab-groups").onclick=()=>activateWorkspaceTab("groups");
// workspace.js owns library/QA/learning loading. Wrap its navigation once all
// deferred scripts exist so drag-to-QA and direct URL restoration hide editor too.
document.addEventListener("DOMContentLoaded",()=>{
  if (typeof workspaceTab==="function") {
    const originalWorkspaceTab=workspaceTab;
    workspaceTab=name=>{
      originalWorkspaceTab(name);
      activateWorkspaceTab(name);
    };
  }
  $("learning-clear-discards").onclick=()=>{
    if (learningBusy) return;
    const count=learningDiscards.size;
    learningDiscards.clear(); renderDiscardSummary();
    $("learning-status").textContent=count
      ? `Cleared ${count} local discard proposals across all pairs in this scan. All current selections remain kept; no decisions saved.`
      : "No local discard proposals to clear. All current selections remain kept; this does not select unreviewed frames or save decisions.";
  };
  $("learning-scan").onclick=()=>learningAction(async()=>{
    await scanLearningSimilarity();
    $("error").hidden=true;
    $("learning-status").textContent=learningSimilarity?.selected_count===0
      ? "No individual Keep frames to compare. Select frames in Frame review first; no decisions changed."
      : "Similarity scan complete. Review the displayed pairs; nothing is marked for discard or saved automatically.";
  });
  const tab=new URLSearchParams(location.search).get("tab");
  if (["editor","groups"].includes(tab)) activateWorkspaceTab(tab);
});
initialize().catch(error=>{fail(error);$("save-status").textContent="Dashboard could not load. Nothing saved.";});
window.addEventListener("beforeunload",event=>{
  if (pendingSaves || cleanupBusy) {event.preventDefault();event.returnValue="";}
});
