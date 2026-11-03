"use strict";
let learningPreview=null, learningBusy=false, learningPayload=null;
let learningSimilarity=null;
const learningDiscards=new Set();
function renderDiscardSummary() {
  $("learning-discard-summary").textContent=`${learningDiscards.size} selected frames marked for recoverable discard: ${[...learningDiscards].sort((a,b)=>a-b).join(", ") || "none"}.`;
  document.querySelectorAll("#learning-pairs input").forEach(input=>{
    input.checked=learningDiscards.has(Number(input.dataset.frame));
  });
}
function clearSimilarity(message="No similarity scan yet.") {
  learningSimilarity=null;learningDiscards.clear();
  $("learning-pairs").replaceChildren();
  $("learning-similarity-status").textContent=message;
  renderDiscardSummary();
}
async function scanLearningSimilarity() {
  if(pendingSaves || busy || saveBlocked)throw new Error("Wait for confirmed frame saves before scanning.");
  clearSimilarity("Scanning selected source frames...");
  await refreshLearning();
  const result=await request(`/api/learning/similar?video=${learningPreview.video}`,{signal:AbortSignal.timeout(120000)});
  if(result.revision!==learningPreview.revision)throw new Error("Decisions changed. Refresh and scan again.");
  learningSimilarity=result;
  $("learning-similarity-status").textContent=`${result.shown} of ${result.candidate_pairs} candidate pairs shown. ${result.method} Review each proposed discard; changes to text or counters may matter.`;
  for(const pair of result.pairs) {
    const article=document.createElement("article");article.className="learning-pair";
    const title=document.createElement("h4");
    title.textContent=`Frames ${pair.left.frame} / ${pair.right.frame} - ${pair.identical_file ? "identical file bytes" : `pixel difference ${pair.difference.toFixed(3)}/255`}`;
    const row=document.createElement("div");row.className="learning-pair-images";
    for(const node of [pair.left,pair.right]) {
      const card=document.createElement("div"),image=document.createElement("img");
      image.src=node.image;image.alt=`Recording ${result.video+1}, frame ${node.frame}`;image.loading="lazy";
      image.onerror=()=>fail(`Similarity preview unavailable for frame ${node.frame}.`);
      const link=document.createElement("a");
      link.href=imageURL(result.video,node.frame,0);link.textContent=`Frame ${node.frame} | ${time(node.time)} - full resolution`;
      link.target="_blank";link.rel="noopener noreferrer";
      const label=document.createElement("label"),input=document.createElement("input");
      input.type="checkbox";input.dataset.frame=String(node.frame);
      input.checked=false;
      input.onchange=()=>{
        if(input.checked)learningDiscards.add(node.frame);else learningDiscards.delete(node.frame);
        renderDiscardSummary();
      };
      label.append(input,document.createTextNode(`Discard frame ${node.frame}`));
      card.append(image,link,label);row.append(card);
    }
    article.append(title,row);$("learning-pairs").append(article);
  }
}
function restoreWorkspaceTab() {
  const tab=new URLSearchParams(location.search).get("tab");
  if(["learning","library","qa"].includes(tab))workspaceTab(tab);
}
document.addEventListener("review-ready",restoreWorkspaceTab,{once:true});
if(state)restoreWorkspaceTab();
async function learningAction(action) {
  if(learningBusy)return;
  learningBusy=true;
  document.querySelectorAll("#learning-tab button,#learning-tab select,#learning-tab input,nav [id^='tab-']").forEach(e=>e.disabled=true);
  try {await action();}
  catch(error) {$("learning-status").textContent=error.message;fail(error);}
  finally {
    learningBusy=false;
    document.querySelectorAll("#learning-tab button,#learning-tab select,#learning-tab input,nav [id^='tab-']").forEach(e=>e.disabled=false);
    if(learningPreview?.retention_only)$("learning-run").disabled=true;
  }
}
async function refreshLearning() {
  learningPreview=null;
  const data=await request(`/api/learning?video=${$("learning-video").value}`);
  if(learningSimilarity && (learningSimilarity.video!==data.preview.video || learningSimilarity.revision!==data.preview.revision)) {
    clearSimilarity("Saved decisions changed. Previous discard proposals cleared; scan again.");
  }
  learningPreview=data.preview;
  $("learning-summary").textContent=`Recording ${learningPreview.video+1}: ${learningPreview.selected} retained selections / ${learningPreview.source_count} original frames. ${learningPreview.nonselected} non-selected. Original split: ${learningPreview.split}.`;
  if(learningPreview.retention_only)$("learning-summary").textContent=`Recording ${learningPreview.video+1}: ${learningPreview.selected} selected originals retained. All ${learningPreview.nonselected} non-selected source images permanently deleted by user request. Completed read-only archive; use saved collections and diagrams. Earlier receipts describe their historical recoverable state.`;
  $("learning-history").replaceChildren();
  for(const run of data.history) {
    const b=document.createElement("button");
    b.textContent=`Recording ${run.video+1} - ${run.selected} frames - ${run.status} - ${run.id}`;
    b.onclick=()=>showLearningResult(run);
    $("learning-history").append(b);
  }
}
async function openLearning() {
  await learningAction(async()=>{
    const videos=await request("/api/videos");
    const previous=$("learning-video").value;
    $("learning-video").replaceChildren(...videos.map((v,i)=>new Option(`Recording ${i+1} - ${v.name}`,String(i))));
    $("learning-video").value=previous || String(recording);
    await refreshLearning();
  });
}
function showLearningResult(run) {
  $("learning-result").replaceChildren();
  const p=document.createElement("p");
  p.textContent=`${run.status}: ${run.selected} retained. ${run.curation_applied ? `${run.recoverably_hidden} frames hidden recoverably, including ${run.discarded_similar?.length || 0} reviewed similar-frame discards.` : "Dashboard curation not applied."} ${run.error || ""} Package: ${run.package}. No model fitted.`;
  $("learning-result").append(p);
  if(run.diagram) {
    const a=document.createElement("a");a.href="/workspace-artifact?name="+encodeURIComponent(run.diagram);
    a.textContent="Open saved selected-frame diagram";a.target="_blank";a.rel="noopener noreferrer";
    $("learning-result").append(a);
  }
  $("learning-prompt").value=run.request || "";
}
$("learning-video").onchange=()=>learningAction(refreshLearning);
$("learning-refresh").onclick=()=>learningAction(refreshLearning);
$("learning-scan").onclick=()=>learningAction(scanLearningSimilarity);
$("learning-clear-discards").onclick=()=>{learningDiscards.clear();renderDiscardSummary();};
$("learning-run").onclick=()=>learningAction(async()=>{
  if(pendingSaves || busy || saveBlocked)throw new Error("Wait for confirmed frame saves, or reload blocked decisions before finalizing.");
  const hadDiscards=learningDiscards.size>0;
  await refreshLearning();
  if(hadDiscards && !learningSimilarity)throw new Error("Decisions changed; review similar-frame discards again before finalizing.");
  if(!learningPreview.selected)throw new Error("Select at least one frame in Frame review first.");
  if(learningDiscards.size>=learningPreview.selected)throw new Error("Keep at least one selected frame.");
  if(learningDiscards.size && !$("learning-hide").checked)throw new Error("Enable recoverable cleanup to discard reviewed similar frames.");
  learningPayload={video:learningPreview.video,revision:learningPreview.revision,confirm:true,
    hide_nonselected:$("learning-hide").checked,save_diagram:$("learning-diagram").checked,
    compare:$("learning-compare").checked,notes:$("learning-notes").value,
    discard_frames:[...learningDiscards].sort((a,b)=>a-b),similarity_hashes:learningSimilarity?.hashes || {}};
  $("learning-confirm-summary").textContent=`Recording ${learningPreview.video+1}: retain ${learningPreview.selected-learningDiscards.size} of ${learningPreview.selected} selections and export their originals. Discard reviewed frames: ${learningPayload.discard_frames.join(", ") || "none"}. ${learningPayload.hide_nonselected ? `Hide ${learningPreview.nonselected} non-selected frames plus ${learningDiscards.size} reviewed similar frames (${learningPreview.nonselected+learningDiscards.size} total) recoverably.` : "Do not change trash."} ${learningPayload.save_diagram ? "Save three diagram formats." : "No published diagram."} ${learningPayload.compare ? "Calculate suggestion agreement." : "No suggestion comparison."}`;
  $("learning-confirm").showModal();
});
$("learning-cancel").onclick=()=>{$("learning-confirm").close();learningPayload=null;};
$("learning-confirm-run").onclick=()=>{
  if(!learningPayload)return;
  const payload=learningPayload;learningPayload=null;$("learning-confirm").close();
  return learningAction(async()=>{
    $("learning-status").textContent="Saving selection package and closeout. Do not retry while this is running.";
    let result;
    try {result=await workspacePost("/api/learning/run",payload);}
    catch(error) {
      throw new Error(`Closeout response was not confirmed. Refresh Saved closeouts before retrying. ${error.message}`);
    }
    showLearningResult(result);
    // Reload canonical feedback without changing the active recording or QA draft.
    state=await request("/api/state");
    buildGallery();renderSaved();updatePosition();setControls();
    document.dispatchEvent(new Event("review-updated"));
    await refreshLearning();
    $("learning-status").textContent="Closeout saved. Visual/semantic Copilot review is a separate step; no model weights changed.";
  });
};
$("learning-copy").onclick=()=>learningAction(async()=>{
  if(!$("learning-prompt").value)throw new Error("Finalize a recording or open a saved closeout first.");
  await navigator.clipboard.writeText($("learning-prompt").value);
  $("learning-status").textContent="Evidence-linked request copied. Paste it into the Copilot chat.";
});
