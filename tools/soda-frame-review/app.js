"use strict";
const $ = id => document.getElementById(id);
const names = {new_state:"Keep",animation:"Animation",same_state:"Same state",uncertain:"Unsure"};
let videos = [], state, recording = 0, current = 0, last = 0, endTime = 86;
let rangeStart = null, rangeEnd = null, page = 0, galleryFrames = [], busy = false;
let imageReady = false, playing = false, currentImageRequest = 0;
const video = $("video"), perPage = 16;
let saveQueue = Promise.resolve(), pendingSaves = 0, saveBlocked = false;
let trashView = false;
const pendingSelections = new Map();
const trashIndexes = new WeakMap();
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
  let lo = 0, hi = last;
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
function setControls() {
  document.querySelectorAll("[data-label]").forEach(button => button.disabled = busy || saveBlocked || !imageReady || playing || isTrashed(current+1));
  $("trash-current").disabled=busy || saveBlocked || !imageReady || playing;
  document.querySelectorAll(".trash-action").forEach(button=>button.disabled=busy || saveBlocked);
  for (const id of ["range-animation","range-repeat","range-unsure"]) {
    $(id).disabled = busy || saveBlocked || !imageReady || rangeStart === null || rangeEnd === null || rangeEnd <= rangeStart || playing;
  }
  for (const id of ["recording","apply-end","reload"]) $(id).disabled = busy;
  $("play").disabled = !videos[recording]?.preview || busy;
  document.querySelectorAll(".frame-select input").forEach(input => input.disabled = saveBlocked);
  if(videos[recording]?.retention_only) {
    document.querySelectorAll("[data-label],.trash-action,.frame-select input").forEach(e=>e.disabled=true);
    for(const id of ["trash-current","trash-view","range-animation","range-repeat","range-unsure"])$(id).disabled=true;
  }
}
function stop() {
  video.pause(); playing = false; video.hidden = true;
  $("frame-image").hidden = !$("frame-image").getAttribute("src");
  $("play").textContent = "Play"; setControls();
}
function updatePosition() {
  $("position").textContent = `Frame ${(current+1).toLocaleString()} / ${(last+1).toLocaleString()} | ${time(videos[recording].times[current])}`;
  $("timeline").value = current;
  const label = effectiveLabel(current+1);
  $("current-label").textContent = isTrashed(current+1) ? "In Trash" : label ? names[label] : "Unlabeled";
  $("trash-current").textContent = isTrashed(current+1) ? "Restore frame" : "Move to Trash";
  $("original").href = imageURL(recording,current+1,0);
}
function showFrame(index, syncGallery = true) {
  const previous=current;
  stop(); current = Math.max(0, Math.min(last, index));
  if(videos[recording].retention_only) {
    const retained=videos[recording].retained_frames.map(n=>n-1).filter(n=>n<=last);
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
    const frames=visibleGalleryFrames();
    let at = frames.findIndex(x => x >= current);
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
  const approved=state.labels.filter(x=>x.video===videos[recording].name &&
    x.label==="new_state" && !isTrashed(x.frame) && x.frame<=last+1).map(x=>x.frame-1);
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
  const visibleFrames = visibleGalleryFrames();
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
    $("gallery-info").textContent=`Retained-only archive: ${visibleFrames.length} selected originals. Non-selections were permanently deleted. Read-only. Page ${page+1} of ${total}.`;
    $("trash-view").textContent="Permanently cleaned";
  }
  $("page-back").disabled = page === 0; $("page-next").disabled = page === total-1;
  $("page-number").value=page+1;
  $("page-number").max=total;
  const fragment = document.createDocumentFragment();
  for (const index of visibleFrames.slice(page*perPage,(page+1)*perPage)) {
    const card = document.createElement("article");
    const button = document.createElement("button");
    const label = effectiveLabel(index+1);
    const selected = pendingSelections.has(index+1) ? pendingSelections.get(index+1) : label === "new_state";
    card.className = `frame-card ${index === current ? "current" : ""} ${selected ? "selected" : ""}`;
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
      showFrame(index,false);
      document.querySelector(".viewer").scrollIntoView({block:"start",behavior:"instant"});
    };
    const selection = document.createElement("label");
    selection.className = "frame-select";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox"; checkbox.checked = selected; checkbox.disabled = saveBlocked || videos[recording].retention_only;
    checkbox.setAttribute("aria-label",`Keep frame ${index+1}`);
    const selectionText = document.createElement("span");
    selectionText.textContent = selected ? "Selected" : "Select frame";
    checkbox.onchange = () => {
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
    card.append(button);
    if (suggested && !trashView) {
      for (const delta of [-0.5,0.5]) {
        const nearby=document.createElement("button");
        nearby.textContent=`Inspect ${delta>0 ? "+" : ""}${delta}s`;
        nearby.setAttribute("aria-label",`Inspect ${delta} seconds from frame ${index+1}`);
        nearby.onclick=()=>{
          showFrame(nearest(videos[recording].times[index]+delta),false);
          document.querySelector(".viewer").scrollIntoView({block:"start",behavior:"instant"});
        };
        card.append(nearby);
      }
    }
    if (!trashView) card.append(selection);
    card.append(trash);
    fragment.append(card);
  }
  $("gallery").replaceChildren(fragment);
  if (!visibleFrames.length) {
    const empty=document.createElement("p");
    empty.textContent=trashView ? "Trash is empty in this review window." : suggested ? "No analyst suggestions in this window. Choose another gallery view." : "No frames here. Check Trash or expand the review window.";
    fragment.append(empty);
    $("gallery").replaceChildren(fragment);
  }
  const selectedFrames = new Set(state.labels.filter(x=>x.video===videos[recording].name&&x.label==="new_state"&&!isTrashed(x.frame)).map(x=>x.frame));
  for (const [frame,checked] of pendingSelections) {
    if (checked) selectedFrames.add(frame); else selectedFrames.delete(frame);
  }
  $("selection-total").textContent = `${selectedFrames.size} selected in this recording${pendingSaves ? ` | ${pendingSaves} changes saving...` : ""}`;
}
function renderSaved() {
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
      if (entry.time > endTime) { endTime=entry.time; $("end").value=endTime; applyWindow(); }
      showFrame(entry.frame-1);
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
function enqueueSave(payload, selection) {
  pendingSaves++; busy=true; setControls();
  $("save-status").textContent="Saving selections to project...";
  saveQueue = saveQueue.then(async () => {
    try {
      if (saveBlocked) return;
      state = await request("/api/annotation",{
        method:"POST",headers:{"Content-Type":"application/json","X-Review-Token":state.token},
        body:JSON.stringify({...payload,revision:state.revision})
      });
      if (selection && pendingSelections.get(selection.frame)===selection.checked) {
        pendingSelections.delete(selection.frame);
      }
      $("save-status").textContent=`Saved to project at ${new Date().toLocaleTimeString()}`;
      buildGallery();
      if (!selection && payload.label==="new_state") {
        trashView=false;
        const at=visibleGalleryFrames().indexOf(payload.frame-1);
        if(at>=0)page=Math.floor(at/perPage);
      }
      renderSaved(); updatePosition();
    } catch (error) {
      saveBlocked=true; pendingSelections.clear();
      $("save-status").textContent="Some changes were not confirmed. Reload decisions before retrying.";
      fail(error);
    } finally {
      pendingSaves--; busy=pendingSaves>0;
      if (pendingSaves && !saveBlocked) $("save-status").textContent=`Saving ${pendingSaves} more changes...`;
      renderGallery(); setControls();
    }
  });
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
  stop(); recording=Number($("recording").value); current=page=0;trashView=false;
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
    option.textContent=`${i+1}. ${v.name.replace("ScreenRecording_","")}`; $("recording").append(option);
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
  try {
    state=await request("/api/state");saveBlocked=false;pendingSelections.clear();
    buildGallery();renderSaved();renderGallery();updatePosition();setControls();
    $("save-status").textContent="Saved decisions reloaded."; $("error").hidden=true;
  } catch(error){fail(error);}
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
  video.currentTime=videos[recording].times[current>=last?0:current];
  video.playbackRate=Number($("speed").value);
  try {
    await video.play();playing=true;video.hidden=false;$("frame-image").hidden=true;
    $("image-status").hidden=true;$("play").textContent="Pause";setControls();
  } catch(error){stop();fail(`Preview playback failed: ${error.message}`);}
};
video.ontimeupdate=()=>{
  if (!playing) return;
  current=nearest(Math.min(video.currentTime,endTime));updatePosition();
  if (video.currentTime>=endTime) {stop();showFrame(last);}
};
video.onended=()=>{stop();showFrame(current);};
video.onerror=()=>{if(video.getAttribute("src"))fail("Video preview unavailable. Exact-frame navigation is still available.");};
$("speed").onchange=()=>video.playbackRate=Number($("speed").value);
document.addEventListener("keydown",event=>{
  if ($("review-tab").hidden) return;
  if (["INPUT","TEXTAREA","SELECT"].includes(event.target.tagName) || event.ctrlKey || event.metaKey || event.altKey || !state) return;
  const key=event.key.toLowerCase();
  if (key==="arrowright"){event.preventDefault();showFrame(current+1);}
  else if(key==="arrowleft"){event.preventDefault();showFrame(current-1);}
  else if(key===" " && !["BUTTON","SUMMARY","A"].includes(event.target.tagName)){event.preventDefault();if(!$("play").disabled)$("play").click();}
  else if({k:1,a:1,r:1,u:1}[key]){event.preventDefault();saveLabel({k:"new_state",a:"animation",r:"same_state",u:"uncertain"}[key]);}
});
initialize().catch(error=>{fail(error);$("save-status").textContent="Dashboard could not load. Nothing saved.";});
window.addEventListener("beforeunload",event=>{
  if (pendingSaves) {event.preventDefault();event.returnValue="";}
});
