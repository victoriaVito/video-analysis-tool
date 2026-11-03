"use strict";
let diagramData=null, diagramVisible=0, diagramRequest=0, diagramStale=false;
let reportingError=false, reportedErrors=0;

async function reportDashboardError(message) {
  if (!state?.token || reportingError || reportedErrors>=30) return;
  reportingError=true;reportedErrors++;
  try {
    await request("/api/client-error",{
      method:"POST",headers:{"Content-Type":"application/json","X-Review-Token":state.token},
      body:JSON.stringify({message:String(message).slice(0,2000)})
    });
  } catch(error) {
    $("connection-status").textContent=`Error log unavailable: ${error.message}`;
    console.error("Dashboard error could not be logged",error);
  } finally {reportingError=false;}
}
function dashboardScript(value) {
  return ["/app.js","/enhancements.js","/workspace.js","/learning.js"].some(path=>String(value || "").includes(`${location.origin}${path}`));
}
window.addEventListener("error",event=>{
  if (event.error && dashboardScript(event.filename)) fail(event.error);
});
window.addEventListener("unhandledrejection",event=>{
  if (dashboardScript(event.reason?.stack)) fail(event.reason);
});

function downloadDiagram(content,name,type) {
  const url=URL.createObjectURL(new Blob([content],{type}));
  const link=document.createElement("a");link.href=url;link.download=name;link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function renderDiagram() {
  if (!diagramData) return;
  const total=diagramData.nodes.length;
  $("diagram-status").textContent=diagramStale
    ? "Saved approvals changed. Rebuild to update this diagram."
    : total ? `${diagramVisible} of ${total} approved steps shown. Saved revision ${diagramData.revision.slice(0,12)}.`
      : "No approved frames in this recording yet. Tick frames in the gallery, then rebuild. Suggestions are not approvals.";
  $("diagram-next").disabled=diagramStale || diagramVisible>=total;
  $("diagram-all").disabled=diagramStale || diagramVisible>=total;
  $("diagram-export").disabled=diagramStale || !total;
  $("diagram-mermaid").disabled=diagramStale || !total;
  const fragment=document.createDocumentFragment();
  diagramData.nodes.slice(0,diagramVisible).forEach((node,index)=>{
    const item=document.createElement("li");item.className="diagram-node";
    if(index) {
      const arrow=document.createElement("p");arrow.className="diagram-arrow";
      const edge=diagramData.edges[index-1];
      arrow.textContent=`\u2193 Later in recording (+${edge.elapsed_seconds.toFixed(3)}s)`;
      item.append(arrow);
    }
    const button=document.createElement("button");button.className="diagram-frame";
    const image=document.createElement("img");
    image.src=node.image;image.alt=`Approved frame ${node.frame}`;image.loading="lazy";
    image.onerror=()=>fail(`Diagram image unavailable for frame ${node.frame}. Check the external drive.`);
    const title=document.createElement("strong");
    title.textContent=`${index+1}. ${node.title} | ${time(node.time)}`;
    const note=document.createElement("p");note.textContent=node.note || "Approved frame; no description added.";
    button.append(image,title,note);
    button.onclick=()=>{
      if (node.time>endTime) {$("end").value=node.time;applyWindow();}
      showFrame(node.frame-1);
      document.querySelector(".viewer").scrollIntoView({block:"start"});
    };
    item.append(button);fragment.append(item);
  });
  $("diagram-nodes").replaceChildren(fragment);
}
async function buildDiagram() {
  if (pendingSaves || saveBlocked) {fail("Wait for confirmed saves or reload decisions before creating a diagram.");return;}
  const id=++diagramRequest, index=recording;
  diagramData=null;diagramStale=true;
  $("diagram-nodes").replaceChildren();
  for(const name of ["diagram-next","diagram-all","diagram-export","diagram-mermaid"])$(name).disabled=true;
  $("diagram-panel").hidden=false;$("diagram-status").textContent="Building from saved approvals...";
  try {
    const data=await request(`/api/diagram?video=${index}`);
    if(id!==diagramRequest || index!==recording)return;
    diagramData=data;diagramVisible=Math.min(1,data.nodes.length);
    diagramStale=false;renderDiagram();
    $("diagram-panel").scrollIntoView({block:"start"});
  } catch(error){fail(error);$("diagram-status").textContent="Diagram creation failed. No approvals changed.";}
}
$("show-diagram").onclick=buildDiagram;
$("diagram-refresh").onclick=buildDiagram;
$("diagram-next").onclick=()=>{diagramVisible++;renderDiagram();};
$("diagram-all").onclick=()=>{diagramVisible=diagramData.nodes.length;renderDiagram();};
$("diagram-close").onclick=()=>{$("diagram-panel").hidden=true;};
$("diagram-export").onclick=()=>{
  if(diagramData && !diagramStale)downloadDiagram(JSON.stringify(diagramData,null,2),"approved-frame-diagram.json","application/json");
};
$("diagram-mermaid").onclick=()=>{
  if(diagramData && !diagramStale)downloadDiagram(diagramData.mermaid,"approved-frame-diagram.mmd","text/plain");
};
document.addEventListener("recording-changed",()=>{
  diagramRequest++;diagramData=null;$("diagram-panel").hidden=true;
  const url=new URL(location.href);url.searchParams.set("video",recording);history.replaceState(null,"",url);
});
document.addEventListener("review-updated",()=>{
  if(diagramData && diagramData.revision!==state.revision){diagramStale=true;renderDiagram();}
  $("connection-status").textContent="Local project connected";
});
async function loadDiagnostics() {
  $("diagnostics-panel").hidden=false;
  try {
    const data=await request("/api/diagnostics");
    $("code-path").textContent=`Editable source: ${data.source_directory}`;
    $("log-path").textContent=`Persistent local log: ${data.log_path || "Logging not configured"}`;
    $("log-entries").textContent=data.entries.join("\n") || "No errors recorded.";
  } catch(error){fail(error);$("log-entries").textContent="Error log could not be loaded.";}
}
$("show-diagnostics").onclick=loadDiagnostics;
$("diagnostics-refresh").onclick=loadDiagnostics;
$("diagnostics-close").onclick=()=>{$("diagnostics-panel").hidden=true;};
