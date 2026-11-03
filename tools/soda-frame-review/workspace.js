"use strict";
let workspaceData=null, qaDraft=null, qaFrames=[], qaUploads=[], jiraSite=null;
let workspaceBusy=false;
async function workspacePost(url,payload) {
  if (!state?.token) throw new Error("Wait for the local project to load.");
  return request(url,{method:"POST",headers:{"Content-Type":"application/json","X-Review-Token":state.token},
    body:JSON.stringify(payload),signal:AbortSignal.timeout(120000)});
}
async function workspaceAction(action) {
  if(workspaceBusy)return;
  workspaceBusy=true;
  document.querySelectorAll("#qa-tab button,#library-tab button").forEach(b=>b.disabled=true);
  try {await action();} catch(error){fail(error);$("qa-status").textContent=error.message;}
  finally {
    workspaceBusy=false;
    document.querySelectorAll("#qa-tab button,#library-tab button").forEach(b=>b.disabled=false);
  }
}
function workspaceTab(name) {
  if(name!=="review")stop();
  const url=new URL(location.href);
  if(name==="review")url.searchParams.delete("tab");
  else url.searchParams.set("tab",name);
  history.replaceState(null,"",url);
  for(const tab of ["review","library","qa","learning"]) {
    $(tab+"-tab").hidden=tab!==name;
    $("tab-"+tab).setAttribute("aria-pressed",String(tab===name));
  }
  if(name==="learning")void openLearning();
  else if(name!=="review")workspaceAction(loadWorkspace);
}
async function loadWorkspace() {
  workspaceData=await request("/api/workspace");
  jiraSite=workspaceData.jira.site;
  const project=$("jira-project").value;
  optionList("jira-project",workspaceData.jira.projects,jiraSite?"Choose a project":"Connect Jira first");
  if([...$("jira-project").options].some(o=>o.value===project))$("jira-project").value=project;
  $("jira-status").textContent=jiraSite ? `Connected to ${jiraSite}. Credentials are session-only.` : "Not connected. Local drafts remain available.";
  $("library-items").replaceChildren();
  for(const item of workspaceData.items) {
    const button=document.createElement("button");button.textContent=item.title;
    button.onclick=()=>workspaceAction(async()=>{
      $("context-artifact").value=item.id;
      $("library-preview").hidden=item.kind!=="diagram";
      $("library-collection").replaceChildren();
      if(item.kind==="diagram")$("library-preview").src=item.url;
      else {
        const graph=await request(item.kind==="snapshot"
          ? `/api/workspace/collection?run=${encodeURIComponent(item.run)}&video=${item.video}`
          : `/api/diagram?video=${item.video}`);
        for(const node of graph.nodes) {
          const card=document.createElement("button");
          const image=document.createElement("img");image.src=node.image;image.alt=`Frame ${node.frame}`;image.loading="lazy";
          const caption=document.createElement("span");caption.textContent=`#${node.frame} | ${time(node.time)} - add to QA`;
          card.append(image,caption);card.draggable=true;
          card.ondragstart=e=>e.dataTransfer.setData("application/x-soda-frame",JSON.stringify({video:item.video,frame:node.frame}));
          card.onclick=()=>{addQaFrame(item.video,node.frame);workspaceTab("qa");};
          $("library-collection").append(card);
        }
      }
    });
    $("library-items").append(button);
  }
  $("qa-drafts").replaceChildren();
  for(const draft of workspaceData.drafts) {
    const button=document.createElement("button");
    button.textContent=`${draft.summary || draft.question || "Untitled"} - ${draft.status}`;
    button.onclick=()=>loadQaDraft(draft);
    $("qa-drafts").append(button);
  }
}
function qaPayload() {
  return {id:qaDraft?.id,version:qaDraft?.version,summary:$("qa-summary").value,
    description:$("qa-description").value,project:$("jira-project").value,
    issue_type:$("jira-type").value,frames:qaFrames,uploads:qaUploads};
}
async function saveQaDraft() {
  qaDraft=await workspacePost("/api/workspace/save",qaPayload());
  $("qa-status").textContent=`Local draft saved: ${qaDraft.id}. No Jira ticket sent.`;
  return qaDraft;
}
function loadQaDraft(draft) {
  qaDraft=draft;qaFrames=draft.frames;qaUploads=draft.uploads;
  $("qa-summary").value=draft.summary;$("qa-description").value=draft.description;
  for(const [id,value] of [["jira-project",draft.project],["jira-type",draft.issue_type]]) {
    if(value && ![...$(id).options].some(o=>o.value===value))$(id).add(new Option(value,value));
    $(id).value=value;
  }
  $("qa-status").replaceChildren(document.createTextNode(`Saved status: ${draft.status}. ${draft.error || ""}`));
  if(draft.issue_url) {
    const a=document.createElement("a");a.href=draft.issue_url;a.textContent=` Open ${draft.issue_key}`;
    a.target="_blank";a.rel="noopener noreferrer";$("qa-status").append(a);
  }
  if(draft.question) {
    $("context-artifact").value=draft.artifact;$("context-question").value=draft.question;
    $("context-prompt").value=draft.description;
  }
  renderQaAttachments();
}
function addQaFrame(index,frame) {
  if(qaFrames.some(f=>f.video===index&&f.frame===frame))return;
  if(qaFrames.length+qaUploads.length>=20){fail("Choose at most 20 images.");return;}
  qaFrames.push({video:index,frame});renderQaAttachments();
}
function renderQaAttachments() {
  $("qa-attachments").replaceChildren();
  [...qaFrames.map((f,i)=>({name:`Recording ${f.video+1}, frame ${f.frame}`,
    src:imageURL(f.video,f.frame),remove:()=>qaFrames.splice(i,1)})),
  ...qaUploads.map((f,i)=>({name:f.name,src:`data:image/${/\.png$/i.test(f.name)?"png":/\.webp$/i.test(f.name)?"webp":"jpeg"};base64,${f.data}`,
    remove:()=>qaUploads.splice(i,1)}))].forEach(item=>{
    const card=document.createElement("article"),image=document.createElement("img"),p=document.createElement("p"),button=document.createElement("button");
    image.src=item.src;image.alt=item.name;p.textContent=item.name;button.textContent="Remove from draft";
    button.onclick=()=>{item.remove();renderQaAttachments();};card.append(image,p,button);$("qa-attachments").append(card);
  });
}
async function addQaFiles(files) {
  for(const file of files) {
    if(qaFrames.length+qaUploads.length>=20)throw new Error("Choose at most 20 images.");
    if(!["image/jpeg","image/png","image/webp"].includes(file.type)||file.size>8000000)throw new Error("Use JPEG, PNG or WebP images up to 8 MB.");
    const data=await new Promise((resolve,reject)=>{
      const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(",")[1]);
      reader.onerror=()=>reject(new Error("Cannot read attachment."));reader.readAsDataURL(file);
    });
    qaUploads.push({name:file.name,data});renderQaAttachments();
  }
}
function optionList(id,items,label) {
  $(id).replaceChildren(new Option(label,""));
  items.forEach(item=>$(id).add(new Option(item.name,item.key || item.id)));
}
for(const name of ["review","library","qa","learning"])$("tab-"+name).onclick=()=>workspaceTab(name);
$("tab-qa").ondragenter=()=>workspaceTab("qa");
$("library-refresh").onclick=()=>workspaceAction(loadWorkspace);
$("context-save").onclick=()=>workspaceAction(async()=>{
  if(!$("context-artifact").value||!$("context-question").value.trim())throw new Error("Choose an artifact and enter your question.");
  const prompt=`Explain this saved Soda artifact using current project evidence.\nArtifact: ${$("context-artifact").value}\nQuestion: ${$("context-question").value}\nProject: eoc_competitive_intelligence\nUse docs/soda-*.html, their evidence links and tools/soda-frame-review/README.md. Read current source before making claims; distinguish observations, interpretations and unknowns. Do not infer purchases or confirmed navigation from chronological arrows.`;
  await workspacePost("/api/workspace/save",{summary:"Context request",description:prompt,artifact:$("context-artifact").value,question:$("context-question").value});
  $("context-prompt").value=prompt;await loadWorkspace();
});
$("context-copy").onclick=()=>workspaceAction(async()=>{await navigator.clipboard.writeText($("context-prompt").value);});
$("qa-current").onclick=()=>addQaFrame(recording,current+1);
$("qa-files").onchange=()=>workspaceAction(()=>addQaFiles($("qa-files").files));
$("qa-drop").ondragover=e=>{e.preventDefault();};
$("qa-drop").ondrop=e=>{
  e.preventDefault();
  workspaceAction(async()=>{
    const value=e.dataTransfer.getData("application/x-soda-frame");
    if(value){const f=JSON.parse(value);addQaFrame(f.video,f.frame);}
    else await addQaFiles(e.dataTransfer.files);
  });
};
document.addEventListener("dragstart",e=>{
  const card=e.target.closest("#gallery .frame-card");
  if(card)e.dataTransfer.setData("application/x-soda-frame",JSON.stringify({video:recording,frame:Number(card.dataset.frame)}));
});
document.addEventListener("review-updated",()=>document.querySelectorAll("#gallery .frame-card").forEach(c=>c.draggable=true));
new MutationObserver(()=>document.querySelectorAll("#gallery .frame-card").forEach(c=>{if(!c.draggable)c.draggable=true;}))
  .observe($("gallery"),{childList:true});
$("qa-help").onclick=()=>{
  const evidence=qaFrames.map(f=>`- Recording ${f.video+1}, frame ${f.frame}: inspect exact source image.`)
    .concat(qaUploads.map(f=>`- Attached image: ${f.name}`)).join("\n");
  $("qa-description").value=($("qa-description").value.trim()?$("qa-description").value+"\n\n":"")+
    `Observed behavior:\n[Describe only what is visible. Ask Copilot to inspect the attached frames if needed.]\n\nExpected behavior:\n[Specify the intended behavior; not inferred from screenshots.]\n\nSteps to reproduce:\n[Record confirmed actions. Chronological frames alone do not prove taps.]\n\nEvidence:\n${evidence||"[Add images or source frames.]"}\n\nUnknowns:\nReproducibility, build/device details and root cause are not yet confirmed.`;
};
$("qa-new").onclick=()=>{qaDraft=null;qaFrames=[];qaUploads=[];$("qa-summary").value="";$("qa-description").value="";$("qa-status").textContent="New unsaved draft.";renderQaAttachments();};
$("qa-save").onclick=()=>workspaceAction(async()=>{await saveQaDraft();await loadWorkspace();});
$("qa-export").onclick=()=>workspaceAction(async()=>{
  const draft=qaDraft?.status && qaDraft.status!=="draft" ? qaDraft : await saveQaDraft();
  downloadDiagram(JSON.stringify(draft,null,2),`qa-draft-${draft.id}.json`,"application/json");
});
$("jira-connect").onclick=()=>workspaceAction(async()=>{
  const token=$("jira-token").value;$("jira-token").value="";
  const data=await workspacePost("/api/jira/connect",{site:$("jira-site").value,email:$("jira-email").value,token});
  jiraSite=data.site;optionList("jira-project",data.projects,"Choose a project");
  $("jira-status").textContent=`Connected to ${data.site}. ${data.note}`;
});
$("jira-disconnect").onclick=()=>workspaceAction(async()=>{await workspacePost("/api/jira/disconnect",{});jiraSite=null;await loadWorkspace();});
$("jira-project").onchange=()=>workspaceAction(async()=>{
  if(!$("jira-project").value)return;
  const data=await request("/api/jira/types?project="+encodeURIComponent($("jira-project").value));
  optionList("jira-type",data.issueTypes || data.values || [],"Choose an issue type");
});
$("qa-create").onclick=()=>workspaceAction(async()=>{
  if(!jiraSite)throw new Error("Connect Jira and choose a project and issue type first.");
  const draft=await saveQaDraft();
  if(!draft.summary.trim()||!draft.description.trim()||!draft.project||!draft.issue_type)throw new Error("Complete the summary, description, project and issue type.");
  $("qa-confirm-text").textContent=`Send "${draft.summary}" to ${jiraSite}, project ${draft.project}, issue type ${draft.issue_type}, with ${draft.attachments.length} images?`;
  $("qa-confirm").showModal();
});
$("qa-confirm-cancel").onclick=()=>$("qa-confirm").close();
$("qa-confirm-send").onclick=()=>workspaceAction(async()=>{
  $("qa-confirm").close();
  $("qa-status").textContent="Sending once. Do not retry if the result is uncertain; inspect the saved receipt.";
  const result=await workspacePost("/api/jira/submit",{id:qaDraft.id,version:qaDraft.version,site:jiraSite,confirm:true});
  loadQaDraft(result);await loadWorkspace();
});
