const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function element(tagName="div") {
  const listeners=new Map();
  const node = {
    textContent:"", value:"", hidden:true, disabled:false, style:{},
    tagName:tagName.toUpperCase(), className:"", attributes:{}, dataset:{}, children:[],
    append(...children){this.children.push(...children);},
    replaceChildren(...children){this.children=children;},
    setAttribute(key,value){this.attributes[key]=value;}, removeAttribute(){},
    addEventListener(name,fn){if(!listeners.has(name))listeners.set(name,[]);listeners.get(name).push(fn);},
    click(){
      let stopped=false;
      const event={preventDefault(){},stopImmediatePropagation(){stopped=true;}};
      for (const listener of listeners.get("click") || []) {listener(event);if(stopped)return;}
      return this.onclick?.(event);
    },
    pause(){}, scrollIntoView(){}, focus(){},
    showModal(){this.open=true;}, close(){this.open=false;},
    getAttribute(key){return this.attributes[key] ?? null;},
  };
  node.classList={
    toggle(name,on) {
      const classes=new Set(node.className.split(/\s+/).filter(Boolean));
      if(on)classes.add(name);else classes.delete(name);
      node.className=[...classes].join(" ");
    },
    contains(name){return node.className.split(/\s+/).includes(name);}
  };
  return node;
}
async function main() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id,element());
    return elements.get(id);
  };
  const calls = [], history = [], errors = [];
  let revision = 0, rejectNext = false, failNext = false;
  const keyListeners=new Map();
  let saved = [], trashed = [], groups = [], categories = [];
  const savedState=()=>({labels:saved,trashed,frame_groups:groups,intervals:[],revision:String(revision),
    category_assignments:categories,
    token:"test",split_by_video:{fixture:"calibration",other:"holdout"}});
  const categoryFields=()=>{
    const fields=[];
    function walk(node, inCategory=false) {
      inCategory ||= node.className==="category-controls";
      if (inCategory && ["INPUT","BUTTON"].includes(node.tagName)) fields.push(node);
      for (const child of node.children || []) walk(child,inCategory);
    }
    for (const root of elements.values()) walk(root);
    return fields;
  };
  const sandbox = {
    console, Map, Set, Date, Blob, URL, URLSearchParams, setTimeout, AbortSignal, Event,
    document:{
      getElementById:get,
      querySelectorAll:selector=>selector===".category-controls input,.category-controls button" ? categoryFields() : [],
      querySelector:()=>element(),
      addEventListener:(name,fn)=>keyListeners.set(name,fn), dispatchEvent(){},
      createElement:element,createDocumentFragment:element
    },
    window:{addEventListener(){}},
    fetch:async(url,options)=>{
      if (url==="/api/state") return {ok:true,json:async()=>savedState()};
      if (url==="/api/client-error") return {ok:true,json:async()=>({logged:true})};
      const body=JSON.parse(options.body);
      calls.push(body);
      await new Promise(resolve=>setTimeout(resolve,5));
      if(failNext) { failNext=false; throw new Error("Response lost; delivery unknown"); }
      if(rejectNext) {
        rejectNext=false;errors.push("conflict");
        return {ok:false,json:async()=>({error:"Concurrent change"})};
      }
      assert.equal(body.revision,String(revision),"Every save uses the latest returned revision");
      let operationMessage;
      let noOp=false;
      if (url==="/api/frame-group" && body.action==="merge") {
        const target=groups.find(group=>group.id===body.id);
        if (target.frames.includes(body.frame)) {
          noOp=true;
          operationMessage=`Frame ${body.frame} is already in Group ${target.display_number}; nothing changed.`;
        }
      }
      if (url==="/api/frame-group" && body.action==="membership") {
        const source=groups.find(group=>group.id===body.source_id);
        if (source.frames.length-1<2 && body.dissolve_ack!==true) {
          return {ok:false,json:async()=>({error:"Confirm dissolving the source group"})};
        }
      }
      if (!noOp) {
        history.push({...body});
        revision++;
      }
      if (url==="/api/frame-group") {
        if (body.action==="create") {
          const used=groups.map((group,index)=>group.display_number || index+1);
          let display_number=1;while(used.includes(display_number))display_number++;
          groups.push({
            id:`group-${revision}`,video:"fixture",frames:body.frames,type:body.type,name:body.name,
            display_number,...(body.selected_unit ? {selected_unit:true} : {})
          });
        } else if (body.action==="merge" && !noOp) {
          const target=groups.find(group=>group.id===body.id);
          target.frames=[...new Set([...target.frames,body.frame])].sort((a,b)=>a-b);
        } else if (body.action==="renumber" || body.action==="configure") {
          groups.find(group=>group.id===body.id).display_number=body.display_number;
          if (body.action==="configure") groups.find(group=>group.id===body.id).color=body.color.toUpperCase();
        } else if (body.action==="membership") {
          const source=groups.find(group=>group.id===body.source_id);
          source.frames=source.frames.filter(frame=>frame!==body.frame);
          if (body.member_action==="move") {
            const destination=groups.find(group=>group.id===body.destination_id);
            destination.frames=[...new Set([...destination.frames,body.frame])].sort((a,b)=>a-b);
          }
          if (source.frames.length<2) groups=groups.filter(group=>group.id!==source.id);
          operationMessage=`Frame ${body.frame} membership corrected.`;
        } else if (body.action==="ungroup") {
          groups=groups.filter(group=>group.id!==body.id);
        }
      } else if (url==="/api/category") {
        assert.equal(body.category_id,"saga_map");
        assert.equal(typeof body.assigned,"boolean");
        categories=categories.filter(item=>!(item.target_type===body.target_type &&
          (body.target_type==="frame" ? item.frame===body.frame : item.group_id===body.group_id)));
        if (body.assigned) categories.push({...body,video:"fixture"});
      } else if (body.action) {
        trashed=trashed.filter(x=>x.frame!==body.frame);
        if(body.action==="trash")trashed.push({video:"fixture",frame:body.frame});
      } else {
        saved=saved.filter(x=>x.frame!==body.frame);
        saved.push({video:"fixture",frame:body.frame,label:body.label,recorded_at:String(revision)});
      }
      categories=categories.filter(item=>item.target_type!=="group" ||
        groups.some(group=>group.id===item.group_id));
      return {ok:true,json:async()=>({...savedState(),...(operationMessage?{operation_message:operationMessage}:{})})};
    },
  };
  vm.createContext(sandbox);
  const source=fs.readFileSync(`${__dirname}/app.js`,"utf8").replace(
    /^initialize\(\)\.catch.*$/m,"");
  vm.runInContext(source,sandbox);
  vm.runInContext(`
    videos=[{name:"fixture",times:[0,1,2,3],preview:false,default_end:3}];
    state={labels:[],intervals:[],revision:"0",token:"test"};last=3;
    document.getElementById("spacing").value="all";
    galleryFrames=[0,1,2,3];
    function pick(frame,checked) {
      pendingSelections.set(frame,checked);
      enqueueSave({video:0,frame,end_frame:frame,label:checked?"new_state":"uncertain",note:"fixture"},
                  {frame,checked});
    }
    pick(1,true);pick(2,true);pick(3,true);pick(1,false);
  `,sandbox);
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(history.length,4);
  assert.deepEqual(saved.sort((a,b)=>a.frame-b.frame).map(x=>[x.frame,x.label]),
    [[1,"uncertain"],[2,"new_state"],[3,"new_state"]]);
  assert.equal(vm.runInContext("pendingSaves",sandbox),0);
  assert.equal(vm.runInContext("busy",sandbox),false);
  assert.equal(vm.runInContext("pendingSelections.size",sandbox),0);
  assert.equal(get("selection-total").textContent,"2 selected in this recording");
  vm.runInContext('document.getElementById("spacing").value="approved";buildGallery();',sandbox);
  assert.deepEqual(Array.from(vm.runInContext("galleryFrames",sandbox)),[1,2]);
  vm.runInContext(`
    state.preselections=[{video:"fixture",frames:[{source_frame:2,claim:"Synthetic suggestion"},{source_frame:4,claim:"Other suggestion"}]}];
    document.getElementById("spacing").value="suggested";
    buildGallery();renderGallery();
  `,sandbox);
  assert.deepEqual(Array.from(vm.runInContext("galleryFrames",sandbox)),[1,2,3],
    "Manually approved frames appear even outside the analyst preselection");
  assert.equal(get("selection-total").textContent,"2 selected in this recording");
  vm.runInContext(`
    state.preselections[0].frames[1].review_group="uncertain";
    buildGallery();renderGallery();
  `,sandbox);
  assert.deepEqual(Array.from(vm.runInContext("galleryFrames",sandbox)),[1,2],
    "Primary suggestions exclude secondary candidates but preserve exact approvals");
  assert.match(get("gallery-info").textContent,/1 secondary candidates/);
  vm.runInContext(`
    document.getElementById("spacing").value="uncertain";buildGallery();renderGallery();
  `,sandbox);
  assert.deepEqual(Array.from(vm.runInContext("galleryFrames",sandbox)),[1,2,3],
    "Secondary candidates remain inspectable alongside exact approvals");
  assert.match(get("gallery-info").textContent,/not rejected/);
  const nearbyPositions=vm.runInContext(`(()=>{
    const previousShow=showFrame, previousTimes=videos[0].times, positions=[];
    videos[0].times=[0,0.3,1.1,2.8];
    showFrame=index=>positions.push(index);
    const cards=document.getElementById("gallery").children[0].children;
    cards[0].children[1].onclick();
    cards[0].children[2].onclick();
    cards.at(-1).children[2].onclick();
    showFrame=previousShow;videos[0].times=previousTimes;
    return positions;
  })()`,sandbox);
  assert.deepEqual(Array.from(nearbyPositions),[0,2,3],
    "Nearby inspection uses source timestamps, clamps and does not approve anything");
  assert.equal(history.length,4,"Browsing primary, uncertain and nearby frames never writes labels");
  vm.runInContext('document.getElementById("spacing").value="all";buildGallery();',sandbox);
  vm.runInContext('enqueueSave({video:0,frame:2,action:"trash"});',sandbox);
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(vm.runInContext("isTrashed(2)",sandbox),true);
  assert.equal(get("selection-total").textContent,"1 selected in this recording");
  assert.deepEqual(get("gallery").children[0].children.map(x=>x.dataset.frame),[1,3,4]);
  vm.runInContext("trashView=true;renderGallery();",sandbox);
  assert.deepEqual(get("gallery").children[0].children.map(x=>x.dataset.frame),[2]);
  vm.runInContext('enqueueSave({video:0,frame:2,action:"restore"});',sandbox);
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(vm.runInContext("isTrashed(2)",sandbox),false);
  assert.equal(get("selection-total").textContent,"2 selected in this recording");
  vm.runInContext("trashView=false;renderGallery();",sandbox);
  const beforeGroups=JSON.stringify(saved), writesBeforeGroup=calls.length;
  vm.runInContext("last=1;endTime=1;buildGallery();renderGallery();",sandbox);
  get("group-start").onclick();
  assert.deepEqual(Array.from(vm.runInContext("[...groupDraft.members].sort()",sandbox)),[2,3],
    "Initial group set uses kept frames across the recording, independent of gallery page");
  vm.runInContext("last=3;endTime=3;buildGallery();renderGallery();",sandbox);
  assert.equal(get("group-save").disabled,true,"Explicit review is required");
  get("group-save").onclick();
  assert.equal(calls.length,writesBeforeGroup,"Starting or inspecting a draft never writes");
  const draftRows=get("group-members").children[0].children;
  const deselect=draftRows[1].children[1].children[0];
  deselect.checked=false; deselect.onchange();
  assert.equal(get("group-save").disabled,true,"A single member cannot be saved");
  vm.runInContext("toggleGroupMember(4,true)",sandbox);
  assert.match(get("group-count").textContent,/2 exact members: #2 \| 00:01.000, #4 \| 00:03.000/);
  get("group-type").value="same_scene";
  get("group-name").value="<img onerror=alert(1)>"; get("group-name").oninput();
  get("group-reviewed").checked=true; get("group-reviewed").onchange();
  assert.equal(get("group-save").disabled,false);
  get("group-save").onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.deepEqual(groups[0].frames,[2,4],"Only exact reviewed members are saved, never the gap");
  assert.equal(groups[0].type,"same_scene");
  assert.equal(JSON.stringify(saved),beforeGroups,"Grouping never relabels members");
  assert.equal(vm.runInContext("groupDraft",sandbox),null);
  assert.equal(get("group-draft").hidden,true);
  const summary=get("group-list").children[0].children[0].children[0];
  assert.equal(summary.textContent,"Group 1 · Same scene: <img onerror=alert(1)> (2 frames)",
    "Names are literal text, not markup");
  const groupedCards=get("gallery").children[0].children;
  assert.equal(groupedCards[1].children.at(-1).className,"group-badge");
  assert.notEqual(groupedCards[2].children.at(-1).className,"group-badge",
    "Intervening frames have no membership badge");
  assert.equal(groupedCards[1].children.at(-1).children[0].className,"group-color-chip");
  groupedCards[1].children.at(-1).onclick();
  assert.equal(get("group-list").children[0].children[0].open,true,"Badges open exact group inspection");
  assert.equal(get("groups-tab").hidden,false,"A group badge opens the separate Groups tab");
  assert.equal(get("review-tab").hidden,true,"The Groups tab keeps the frame reviewer separate");
  const beforeInspect=calls.length;
  vm.runInContext("const groupOriginalShow=showFrame; let inspectedFrame; showFrame=i=>{inspectedFrame=i;};",sandbox);
  get("group-list").children[0].children[0].children[2].children[1].children[0].onclick();
  assert.equal(vm.runInContext("inspectedFrame",sandbox),3);
  assert.equal(calls.length,beforeInspect,"Inspection never writes annotations");
  vm.runInContext("showFrame=groupOriginalShow;",sandbox);
  await get("reload").onclick();
  // Keyboard grouping uses independent exact membership, never the Keep starting set.
  const key=(value,extra={})=>keyListeners.get("keydown")({
    key:value,target:{tagName:"BODY"},preventDefault(){},...extra
  });
  const cards=()=>get("gallery").children[0].children;
  const staging=()=>Array.from(vm.runInContext("[...stagedFrames]",sandbox));
  vm.runInContext('activateWorkspaceTab("editor")',sandbox);
  vm.runInContext("imageReady=true;current=1;clearGroupDraft();",sandbox);
  const keyboardWrites=calls.length, keyboardLabels=JSON.stringify(saved);
  for (const extra of [
    {target:{tagName:"INPUT"}},{target:{tagName:"TEXTAREA"}},{target:{tagName:"SELECT"}},
    {target:{tagName:"SPAN",isContentEditable:true}},
    {target:{tagName:"SPAN",closest:()=>({})}},
    {ctrlKey:true},{metaKey:true},{altKey:true},{shiftKey:true},{repeat:true},{isComposing:true}
  ]) key("1",extra);
  assert.deepEqual(staging(),[],"Editing and modifier/repeat/composition guards prevent staging");
  vm.runInContext("busy=true",sandbox);key("1");
  vm.runInContext("busy=false;playing=true",sandbox);key("1");
  vm.runInContext("playing=false;trashView=true",sandbox);key("1");
  vm.runInContext("trashView=false;imageReady=false",sandbox);key("1");
  vm.runInContext("imageReady=true;startGroupDraft()",sandbox);key("1");
  vm.runInContext("clearGroupDraft()",sandbox);
  vm.runInContext('activateWorkspaceTab("groups")',sandbox);key("1");
  vm.runInContext('activateWorkspaceTab("editor")',sandbox);
  assert.deepEqual(staging(),[],"Busy/playback/Trash/image loading/manual draft/other tabs are guarded");
  key("M");
  assert.equal(calls.length,keyboardWrites,"M cannot save fewer than two members");
  key("1");
  assert.deepEqual(staging(),[2]);
  assert.equal(cards()[1].classList.contains("selected"),true,"Keep remains selected");
  assert.equal(cards()[1].classList.contains("staged"),true,"Yellow has a separate visual class over green");
  assert.equal(get("frame-image").classList.contains("staged"),true);
  key("1");assert.deepEqual(staging(),[],"1 toggles off");
  get("stage-current").onclick();
  vm.runInContext("current=3;updatePosition()",sandbox);
  key("1");
  assert.deepEqual(staging(),[2,4],"Navigation preserves exact non-contiguous members");
  assert.equal(JSON.stringify(saved),keyboardLabels);
  assert.equal(calls.length,keyboardWrites,"Staging never writes decisions");
  get("stage-save").onclick();
  key("M");key("1");
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(calls.length,keyboardWrites+1,"Busy keyboard cannot duplicate a save");
  const unitId=groups.at(-1).id;
  assert.equal(groups.at(-1).selected_unit,true);
  assert.equal(groups.at(-1).type,"same_scene","No animation inference");
  assert.deepEqual(groups.at(-1).frames,[2,4]);
  assert.equal(JSON.stringify(saved),keyboardLabels);
  assert.deepEqual(staging(),[]);
  assert.equal(cards().length,3,"Two members become ONE card, not two badged cards");
  let unitCard=cards().find(card=>card.dataset.group===unitId);
  assert.equal(unitCard.classList.contains("selected"),true);
  assert.equal(unitCard.children.length,5,"Unit starts collapsed with category controls, without member thumbnails");
  assert.equal(unitCard.children[2].getAttribute("aria-expanded"),"false");
  unitCard.children[2].onclick();
  unitCard=cards().find(card=>card.dataset.group===unitId);
  assert.equal(unitCard.children[4].children.length,2,"Expanded exact member thumbnails");
  assert.equal(unitCard.children[2].getAttribute("aria-expanded"),"true");
  const beforeUnitInspect=calls.length;
  vm.runInContext("showFrame=i=>{inspectedFrame=i;}",sandbox);
  unitCard.children[4].children[1].children[0].onclick();
  assert.equal(vm.runInContext("inspectedFrame",sandbox),3);
  vm.runInContext("showFrame=groupOriginalShow",sandbox);
  assert.equal(calls.length,beforeUnitInspect);
  await get("reload").onclick();
  assert.equal(cards().find(card=>card.dataset.group===unitId).children.length,5,
    "Reload reconstructs a collapsed selected unit");
  vm.runInContext('document.getElementById("spacing").value="approved";buildGallery();renderGallery()',sandbox);
  assert.equal(cards().length,2,"Approved-only includes one group plus unrelated Keep");
  assert.equal(cards().filter(card=>card.dataset.group).length,1);
  vm.runInContext('document.getElementById("spacing").value="uncertain";buildGallery();renderGallery()',sandbox);
  assert.equal(cards().filter(card=>card.dataset.group).length,1,"Suggestion filters retain selected units");
  // Legacy overlapping metadata must neither hide frames nor duplicate unit cards.
  groups.push({id:"old-overlap",video:"fixture",frames:[2,3,4],type:"animation",name:"legacy"});
  await get("reload").onclick();
  assert.equal(cards().length,2);
  assert.equal(cards().find(card=>card.dataset.frame===3).children.at(-1).className,"group-badge");
  vm.runInContext("last=1;buildGallery();renderGallery()",sandbox);
  unitCard=cards().find(card=>card.dataset.group===unitId);
  assert.match(unitCard.children[0].textContent,/1\/2 members in view/);
  unitCard.children[2].onclick();
  assert.equal(cards().find(card=>card.dataset.group===unitId).children[4].children.length,1,
    "Expansion does not leak members beyond the window");
  vm.runInContext('last=3;document.getElementById("spacing").value="all";buildGallery();enqueueSave({video:0,frame:2,action:"trash"})',sandbox);
  await vm.runInContext("saveQueue",sandbox);
  unitCard=cards().find(card=>card.dataset.group===unitId);
  assert.equal(unitCard.dataset.frame,4,"A trashed anchor is replaced by the first visible member");
  assert.equal(unitCard.children[4].children.length,1,"Trashed members are not expanded in normal view");
  vm.runInContext("trashView=true;renderGallery()",sandbox);
  assert.deepEqual(cards().map(card=>card.dataset.frame),[2],"Trash remains individual and restorable");
  assert.equal(cards().some(card=>card.dataset.group),false);
  vm.runInContext('enqueueSave({video:0,frame:2,action:"restore"})',sandbox);
  await vm.runInContext("saveQueue",sandbox);
  vm.runInContext("trashView=false;renderGallery()",sandbox);
  // Pagination counts collapsed units, not hidden children.
  vm.runInContext(`
    videos[0].times=Array.from({length:40},(_,i)=>i);
    last=39;endTime=39;buildGallery();page=1;renderGallery();
  `,sandbox);
  assert.equal(cards().length,16);
  assert.equal(cards()[0].dataset.frame,18,"Page two starts after 16 display units");
  vm.runInContext("page=2;renderGallery()",sandbox);
  assert.equal(cards().length,7,"40 frames minus one collapsed member => 39 units");
  vm.runInContext("page=0;renderGallery()",sandbox);
  cards().find(card=>card.dataset.group===unitId).children[3].onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(cards().length,16);
  assert.deepEqual(cards().map(card=>card.dataset.frame),Array.from({length:16},(_,i)=>i+1),
    "Ungroup restores the exact individual chronological gallery");
  assert.equal(groups.some(group=>group.id==="old-overlap"),true,"Ungroup preserves overlapping old metadata");
  assert.equal(JSON.stringify(saved),keyboardLabels,"Ungroup preserves individual decisions");
  vm.runInContext("current=0;imageReady=true",sandbox);key("1");
  vm.runInContext("current=2",sandbox);key("1");
  vm.runInContext('enqueueSave({video:0,frame:4,action:"trash"})',sandbox);
  await vm.runInContext("saveQueue",sandbox);
  assert.deepEqual(staging(),[1,3],"An independent annotation save does not discard staging");
  assert.equal(vm.runInContext("stagedRevision===state.revision",sandbox),true,
    "Own successful writes safely advance the draft revision");
  vm.runInContext('enqueueSave({video:0,frame:1,action:"trash"})',sandbox);
  await vm.runInContext("saveQueue",sandbox);
  assert.deepEqual(staging(),[3],"Trashed staged members are removed, not silently saved");
  vm.runInContext('enqueueSave({video:0,frame:1,action:"restore"});enqueueSave({video:0,frame:4,action:"restore"})',sandbox);
  await vm.runInContext("saveQueue",sandbox);
  get("stage-clear").onclick();
  assert.deepEqual(staging(),[],"Accessible clear cancels staging without a write");
  // Failure/conflict clears the unsafe yellow draft and never retries.
  vm.runInContext("current=0;imageReady=true",sandbox);key("1");
  vm.runInContext("current=2",sandbox);key("1");
  const stagedAt=vm.runInContext("stagedRevision",sandbox);
  vm.runInContext('state={...state,revision:"external-change"}',sandbox);
  rejectNext=true;key("M");
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(calls.at(-1).revision,stagedAt);
  assert.equal(vm.runInContext("saveBlocked",sandbox),true);
  assert.deepEqual(staging(),[]);
  assert.match(get("save-status").textContent,/inspect saved groups/);
  await get("reload").onclick();
  vm.runInContext("current=0;imageReady=true",sandbox);key("1");
  vm.runInContext("current=2",sandbox);key("1");
  failNext=true;key("M");
  await vm.runInContext("saveQueue",sandbox);
  assert.match(get("error").textContent,/delivery unknown/);
  assert.deepEqual(staging(),[]);
  const failedWrites=calls.length;key("M");
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(calls.length,failedWrites,"No retry after unknown delivery");
  await get("reload").onclick();
  vm.runInContext("current=0;imageReady=true",sandbox);key("1");
  await get("reload").onclick();
  assert.deepEqual(staging(),[],"Explicit reload clears staging");
  vm.runInContext("current=0;imageReady=true",sandbox);key("1");
  vm.runInContext('state={...state,revision:"closeout-refresh"};renderSaved()',sandbox);
  assert.deepEqual(staging(),[],"External refresh invalidates staging");
  await get("reload").onclick();
  get("group-list").children[0].children.at(-1).children[3].onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.deepEqual(Array.from(vm.runInContext("recordingGroups()[0].frames",sandbox)),[2,4],
    "Reloaded decisions retain exact metadata");
  get("group-start").onclick();
  await get("reload").onclick();
  assert.equal(vm.runInContext("groupDraft",sandbox),null,"Reload clears stale drafts");
  get("group-start").onclick();
  vm.runInContext('state={...state,revision:"closeout-refresh"};renderSaved();',sandbox);
  assert.equal(vm.runInContext("groupDraft",sandbox),null,"Learning-style state refresh clears stale drafts");
  assert.equal(get("group-save").disabled,true);
  await get("reload").onclick();
  get("group-start").onclick();
  vm.runInContext('enqueueSave({video:0,frame:2,label:"new_state"});',sandbox);
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(vm.runInContext("groupDraft",sandbox),null,"Decision writes invalidate membership drafts");
  get("group-list").children[0].children[0].children[3].onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(groups.length,0);
  assert.equal(get("selection-total").textContent,"2 selected in this recording");
  get("group-start").onclick();
  get("group-reviewed").checked=true; get("group-reviewed").onchange();
  const reviewedRevision=vm.runInContext("groupDraft.revision",sandbox);
  vm.runInContext('state={...state,revision:"changed-after-review"};',sandbox);
  rejectNext=true;
  get("group-save").onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(groups.length,0,"Conflicted group writes do not appear saved");
  assert.equal(calls.at(-1).revision,reviewedRevision,"Group saves use the reviewed revision, not a newer state");
  assert.equal(vm.runInContext("saveBlocked",sandbox),true);
  assert.equal(vm.runInContext("groupDraft",sandbox),null);
  assert.deepEqual(staging(),[],"Recording change clears yellow staging");
  await get("reload").onclick();
  // Recording changes clear membership, and do not write or affect another recording.
  get("group-start").onclick();
  vm.runInContext("clearGroupDraft();current=0;imageReady=true",sandbox);key("1");
  vm.runInContext(`
    const groupApplyWindow=applyWindow; applyWindow=()=>{};
    videos.push({name:"other",times:[0,1],default_end:1});
    document.getElementById("recording").value="1"; selectRecording();
  `,sandbox);
  assert.equal(vm.runInContext("groupDraft",sandbox),null);
  assert.equal(vm.runInContext("recordingGroups().length",sandbox),0);
  vm.runInContext(`
    document.getElementById("recording").value="0"; selectRecording();
    applyWindow=groupApplyWindow; last=3;videos[0].times=[0,1,2,3];groups=[];
    videos[0].retention_only=true; videos[0].retained_frames=[2,3];
    startGroupDraft();
  `,sandbox);
  assert.deepEqual(Array.from(vm.runInContext("[...groupDraft.members].sort()",sandbox)),[2,3],
    "Retained-only archives allow metadata grouping without enabling labels");
  vm.runInContext("clearGroupDraft();videos[0].retention_only=false;buildGallery();renderGallery();",sandbox);
  const beforeConflict=calls.length;
  rejectNext=true;
  vm.runInContext("pick(4,true);pick(2,false);",sandbox);
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(calls.length,beforeConflict+1,"No queued writes continue after a failed save");
  assert.equal(errors.length,3);
  assert.equal(vm.runInContext("saveBlocked",sandbox),true);
  assert.equal(get("error").textContent,"Concurrent change");
  assert.equal(vm.runInContext("pendingSaves",sandbox),0);
  assert.equal(saved.find(x=>x.frame===2).label,"new_state");
  vm.runInContext(`
    const originalShowFrame=showFrame;
    let jumpedTo;
    showFrame=index=>{jumpedTo=index;};
    current=1;
  `,sandbox);
  get("next-two").onclick();
  assert.equal(vm.runInContext("jumpedTo",sandbox),3);
  get("back-two").onclick();
  assert.equal(vm.runInContext("jumpedTo",sandbox),0);
  get("next-two-half").onclick();
  assert.equal(vm.runInContext("jumpedTo",sandbox),3);
  get("back-two-half").onclick();
  assert.equal(vm.runInContext("jumpedTo",sandbox),0);
  vm.runInContext("showFrame=originalShowFrame;current=0;",sandbox);
  const listeners=new Map();
  sandbox.document.addEventListener=(name,callback)=>listeners.set(name,callback);
  sandbox.location={origin:"http://127.0.0.1:8766",href:"http://127.0.0.1:8766/"};
  sandbox.history={replaceState(){}};
  vm.runInContext(fs.readFileSync(`${__dirname}/enhancements.js`,"utf8"),sandbox);
  vm.runInContext("saveBlocked=false;pendingSaves=0;",sandbox);
  const graph={
    revision:"approved-snapshot",
    nodes:[{id:"f2",frame:2,time:1,title:"Frame 2",image:"/fixture-2",note:""},
           {id:"f3",frame:3,time:2,title:"Frame 3",image:"/fixture-3",note:""}],
    edges:[{from:"f2",to:"f3",elapsed_seconds:1}],mermaid:"flowchart TD"
  };
  const reviewFetch=sandbox.fetch;
  sandbox.fetch=async()=>({ok:true,json:async()=>graph});
  await vm.runInContext("buildDiagram()",sandbox);
  assert.equal(get("diagram-nodes").children[0].children.length,1);
  get("diagram-next").onclick();
  assert.equal(get("diagram-nodes").children[0].children.length,2);
  assert.equal(get("diagram-next").disabled,true);
  assert.equal(get("diagram-export").disabled,false);
  listeners.get("review-updated")();
  assert.equal(get("diagram-export").disabled,true,"Changed approvals require rebuilding");
  assert.equal(vm.runInContext('dashboardScript("host plugin error")',sandbox),false);
  assert.equal(vm.runInContext('dashboardScript("http://127.0.0.1:8766/app.js:12")',sandbox),true);
  sandbox.fetch=async()=>({ok:true,json:async()=>({...graph,nodes:[],edges:[]})});
  await vm.runInContext("buildDiagram()",sandbox);
  assert.match(get("diagram-status").textContent,/No approved frames/);
  assert.equal(get("diagram-export").disabled,true);
  sandbox.fetch=reviewFetch;
  vm.runInContext(`
    saveBlocked=false;busy=false;pendingSaves=0;trashView=false;clearGroupDraft();clearStaging();
    videos[0].times=[0,1,2,3,4,5,6];videos[0].retention_only=false;
    videos[0].retained_frames=null;last=6;endTime=6;current=0;
    galleryFrames=[0,1,2,3,4,5,6];document.getElementById("spacing").value="all";
  `,sandbox);
  groups=[]; await get("reload").onclick(); get("review-tab").hidden=false;
  vm.runInContext(`
    enqueueSave({video:0,action:"create",frames:[2,4],type:"animation",name:"Editable target"},
      null,"/api/frame-group");
  `,sandbox);
  await vm.runInContext("saveQueue",sandbox);
  const target={...groups[0],frames:[...groups[0].frames]};
  assert.equal(target.display_number,1);
  vm.runInContext("buildGallery();renderGallery();",sandbox);
  vm.runInContext("current=5;imageReady=true;updatePosition()",sandbox);
  const openSelect=get("merge-current-select"), openWrites=calls.length;
  assert.match(openSelect.children[1].textContent,/Group 1/);
  openSelect.value=target.id;openSelect.onchange();
  assert.equal(calls.length,openWrites,"Open-frame dropdown changes also remain local");
  get("merge-current").onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.deepEqual(groups[0].frames,[2,4,6],"Open-frame control appends its exact current frame");
  const frameCard=frame=>cards().find(card=>card.dataset.frame===frame);
  const mergeControl=frame=>frameCard(frame).children.find(child=>child.className==="frame-merge-controls");
  const frameOneMerge=mergeControl(1), frameOneSelect=frameOneMerge.children[0].children[0];
  const frameOneButton=frameOneMerge.children[1], dropdownWrites=calls.length;
  frameOneSelect.value=target.id;frameOneSelect.onchange();
  assert.equal(calls.length,dropdownWrites,"Choosing a destination alone never writes");
  frameOneButton.onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.deepEqual(groups[0].frames,[1,2,4,6],"Gallery merge appends one exact chronological member");
  assert.equal(groups[0].id,target.id);
  assert.equal(groups[0].type,target.type);
  assert.equal(groups[0].name,target.name);
  const mergeRevision=revision, mergeHistory=history.length, beforeNoopCalls=calls.length;
  mergeControl(1).children[1].onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(calls.length,beforeNoopCalls+1,"An already-member request is reported explicitly by the API");
  assert.equal(history.length,mergeHistory,"Already-member confirmation does not write");
  assert.equal(revision,mergeRevision,"Already-member confirmation does not change the revision");
  assert.match(get("save-status").textContent,/already in Group 1/);
  const details=get("group-list").children[0].children[0];
  assert.equal(details.children[0].children[0].style.backgroundColor,"#808080",
    "Legacy groups without a saved color use the neutral display default");
  const numberControls=details.children[1], numberInput=numberControls.children[0].children[0];
  const colorInput=numberControls.children[1].children[0];
  const numberSave=numberControls.children[2], numberWrites=calls.length;
  numberInput.value="8";
  colorInput.value="red";
  numberSave.onclick();
  assert.equal(calls.length,numberWrites,"Invalid color is rejected before a save");
  assert.match(get("error").textContent,/#RRGGBB/);
  colorInput.value="#12ab34";
  assert.equal(calls.length,numberWrites,"Editing number and color alone never writes");
  numberSave.onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(groups[0].display_number,8);
  assert.equal(groups[0].color,"#12AB34","Number and color save atomically");
  assert.equal(groups[0].id,target.id,"Renumbering preserves the stable internal id");
  assert.deepEqual(groups[0].frames,[1,2,4,6],"Renumbering preserves exact members");
  assert.match(mergeControl(1).children[0].children[0].children[1].textContent,/Group 8/,
    "Dropdown refreshes with the saved display number");
  assert.match(mergeControl(1).children[0].children[0].children[1].textContent,/#12AB34/,
    "Dropdown retains an accessible color indication");
  assert.equal(mergeControl(1).children[0].children[0].children[1].style.color,"#12AB34",
    "Native group options use the color where supported");
  assert.equal(cards().find(card=>card.dataset.frame===1).children.at(-1).children[0].style.backgroundColor,
    "#12AB34","Saved color appears as a chip without replacing selected-state styling");
  const newControl=mergeControl(3), newSelect=newControl.children[0].children[0];
  const beforeNewStage=calls.length;
  newSelect.value="new";newSelect.onchange();
  assert.equal(calls.length,beforeNewStage,"New group selection remains a local draft");
  newControl.children[1].onclick();
  assert.deepEqual(staging(),[3],"New group destination stages only the chosen exact frame");
  assert.equal(calls.length,beforeNewStage,"Staging does not persist until M with two frames");
  vm.runInContext("current=4;imageReady=true",sandbox);key("1");key("M");
  await vm.runInContext("saveQueue",sandbox);
  const stagedUnit=groups.find(group=>group.selected_unit);
  assert.deepEqual(stagedUnit.frames,[3,5],"M persists only the two exact yellow members");
  assert.equal(stagedUnit.type,"same_scene","New Merge destination retains the existing selected-unit workflow");
  vm.runInContext("clearStaging();",sandbox);
  const oneFrameControl=mergeControl(6);
  oneFrameControl.children[0].children[0].value="new";
  oneFrameControl.children[0].children[0].onchange();
  oneFrameControl.children[1].onclick();
  const oneFrameWrites=calls.length;
  key("M");
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(calls.length,oneFrameWrites,"M cannot persist a one-frame group");
  assert.match(get("error").textContent,/at least two exact frames/);
  groups=[
    {id:"membership-source",video:"fixture",frames:[1,2,3,6,7],type:"animation",name:"Source",display_number:1,color:"#AA2244",selected_unit:true},
    {id:"membership-target",video:"fixture",frames:[4,5],type:"same_scene",name:"Target",display_number:2,color:"#2266AA"}
  ];
  sandbox.groups=groups;
  vm.runInContext('state={...state,frame_groups:groups};activateWorkspaceTab("review");renderSaved()',sandbox);
  vm.runInContext('videos[0].times=Array.from({length:8},(_,i)=>i);last=7;endTime=7;document.getElementById("spacing").value="all";buildGallery();renderGallery()',sandbox);
  let galleryUnit=cards().find(card=>card.dataset.group==="membership-source");
  galleryUnit.children[2].onclick();
  galleryUnit=cards().find(card=>card.dataset.group==="membership-source");
  let galleryMember=galleryUnit.children[4].children[0];
  const galleryActions=galleryMember.children[1], galleryDestination=galleryActions.children[0];
  const galleryWrites=calls.length;
  galleryDestination.value="membership-target";galleryDestination.onchange();
  assert.equal(calls.length,galleryWrites,"Expanded gallery destination selection alone never writes");
  galleryActions.children[1].onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.deepEqual(groups[0].frames,[2,3,6,7],"Expanded selected-unit member controls use the shared move handler");
  assert.deepEqual(groups[1].frames,[1,4,5]);
  galleryUnit=cards().find(card=>card.dataset.group==="membership-source");
  galleryMember=galleryUnit.children[4].children[0];
  assert.equal(galleryMember.children[1].children[2].textContent,"Remove from group",
    "Expanded gallery members expose the same explicit removal control");
  galleryMember.children[1].children[2].onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.deepEqual(groups[0].frames,[3,6,7],"Expanded gallery Remove changes only that exact membership");
  vm.runInContext('activateWorkspaceTab("groups")',sandbox);
  let sourceDetails=get("group-list").children[0].children.find(item=>item.children?.[0]?.textContent?.includes("Group 1"));
  let firstMember=sourceDetails.children[2].children[0], membershipActions=firstMember.children[1];
  const destination=membershipActions.children[0], move=membershipActions.children[1], uiWrites=calls.length;
  assert.match(destination.children[1].textContent,/Group 2 — Same scene: Target \[#2266AA\]/);
  destination.value="membership-target";destination.onchange();
  assert.equal(calls.length,uiWrites,"Choosing a correction destination alone never writes");
  move.onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.deepEqual(groups[0].frames,[6,7],"Groups-tab Move removes only the chosen source membership");
  assert.deepEqual(groups[1].frames,[1,3,4,5],"Groups-tab Move adds exactly one frame to the destination");
  assert.equal(get("groups-tab").hidden,false,"Membership corrections remain in the Groups workspace");
  sourceDetails=get("group-list").children[0].children.find(item=>item.children?.[0]?.textContent?.includes("Group 1"));
  let sourceMembers=sourceDetails.children[2].children;
  sourceMembers[0].children[1].children[2].onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.deepEqual(groups[0].frames,[6,7],"Remove changes only the specified source membership");
  sourceDetails=get("group-list").children[0].children.find(item=>item.children?.[0]?.textContent?.includes("Group 1"));
  sourceMembers=sourceDetails.children[2].children;
  const dialog=get("group-dissolve-confirm"), confirmWrites=calls.length;
  sourceMembers[0].children[1].children[2].onclick();
  assert.equal(dialog.open,true,"Removing from a two-member source previews dissolution");
  assert.equal(calls.length,confirmWrites,"Dissolution preview makes no write");
  get("group-dissolve-cancel").onclick();
  assert.equal(dialog.open,false);
  assert.deepEqual(groups[0].frames,[6,7],"Cancelling leaves the group unchanged");
  sourceMembers[0].children[1].children[2].onclick();
  get("group-dissolve-approve").onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(groups.some(group=>group.id==="membership-source"),false,"Confirmed dissolution removes only the undersized source group");
  assert.deepEqual(groups.find(group=>group.id==="membership-target").frames,[1,3,4,5]);

  // Category controls share one renderer and one explicit-save handler across
  // viewer, cards, settings and members. The mock never changes Keep for them.
  groups=[
    {id:"category-unit",video:"fixture",frames:[1,2,3],type:"same_scene",name:"Unit",display_number:1,selected_unit:true},
    {id:"category-metadata",video:"fixture",frames:[4,5],type:"animation",name:"Metadata",display_number:2}
  ];
  categories=[];
  await get("reload").onclick();
  vm.runInContext('current=7;activateWorkspaceTab("review");updatePosition();renderGallery()',sandbox);
  const categoryUI=parent=>parent.children.find(child=>child.className==="category-controls");
  const categoryInput=controls=>controls.children[1].children[0];
  const categorySave=controls=>controls.children[2];
  const directCategory=frame=>categories.find(item=>item.target_type==="frame" && item.frame===frame);
  const groupCategory=id=>categories.find(item=>item.target_type==="group" && item.group_id===id);
  const categoryDecisions=JSON.stringify(saved), categoryGroups=JSON.stringify(groups);
  let controls=get("current-category").children[0];
  assert.match(controls.children[0].textContent,/Direct frame category: None/);
  const beforeCategory=calls.length;
  categoryInput(controls).checked=true;
  assert.equal(calls.length,beforeCategory,"Viewer checkbox alone never saves");
  assert.match(controls.children[0].textContent,/None/,"Badge reflects saved state, not the unsaved checkbox");
  categorySave(controls).onclick();
  assert.equal(categoryInput(controls).disabled,true,"Category input is disabled during an in-flight save");
  assert.equal(categorySave(controls).disabled,true);
  categorySave(controls).onclick(); // Synchronous busy guard prevents duplicate saves.
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(calls.length,beforeCategory+1);
  assert.equal(calls.at(-1).frame,8,"Viewer targets its exact displayed source frame");
  assert.ok(directCategory(8));
  assert.match(get("current-category").children[0].children[0].textContent,/Direct frame category: Saga map/);
  assert.equal(categorySave(get("current-category").children[0]).disabled,false,"Viewer Save re-enables after successful readback");
  controls=categoryUI(cards().find(card=>card.dataset.group==="category-unit"));
  const beforeGroupCategory=calls.length;
  categoryInput(controls).checked=true;
  assert.equal(calls.length,beforeGroupCategory,"Group card checkbox alone never saves");
  categorySave(controls).onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.ok(groupCategory("category-unit"));
  assert.equal(directCategory(1),undefined,"Categorizing the group never tags its members");
  assert.equal(directCategory(2),undefined);
  let categoryDetails=get("group-list").children[0].children;
  controls=categoryUI(categoryDetails[1].children[1]);
  categoryInput(controls).checked=true;
  const beforeSettingsCategory=calls.length;
  assert.equal(calls.length,beforeSettingsCategory);
  categorySave(controls).onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.ok(groupCategory("category-metadata"),"Several independent groups may be categorized");
  categoryDetails=get("group-list").children[0].children;
  controls=categoryUI(categoryDetails[0].children[2].children[1]);
  categoryInput(controls).checked=true;
  categorySave(controls).onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.ok(directCategory(2),"Group member editor saves the direct exact-frame category");
  assert.ok(directCategory(8),"Several independent frames may be categorized");
  assert.equal(categories.length,4);
  assert.equal(JSON.stringify(saved),categoryDecisions,"Category saves preserve all decisions");
  assert.equal(JSON.stringify(groups),categoryGroups,"Category saves preserve all group fields");
  controls=categoryUI(cards().find(card=>!card.dataset.group && card.dataset.frame===8));
  assert.equal(categoryInput(controls).checked,true,"Individual gallery reflects direct saved categories");
  let categoryUnit=cards().find(card=>card.dataset.group==="category-unit");
  categoryUnit.children[2].onclick();
  categoryUnit=cards().find(card=>card.dataset.group==="category-unit");
  const categorizedMember=categoryUnit.children[4].children[1];
  assert.match(categoryUI(categorizedMember).children[0].textContent,/Direct frame category: Saga map/);
  assert.match(categoryUI(categoryUnit).children[0].textContent,/Group reference category: Saga map/);
  const categoryMove=categorizedMember.children[1];
  categoryMove.children[0].value="category-metadata";categoryMove.children[0].onchange();
  categoryMove.children[1].onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.ok(directCategory(2),"Member Move preserves direct categories without inheritance");
  assert.equal(categories.length,4);
  vm.runInContext('changeGroupMembership(recordingGroups()[0],1,"remove")',sandbox);
  get("group-dissolve-approve").onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(groupCategory("category-unit"),undefined,"Dissolution clears only its group reference");
  assert.ok(groupCategory("category-metadata"));
  assert.ok(directCategory(2));
  controls=categoryUI(get("group-list").children[0].children[0].children[1]);
  const beforeClear=calls.length;
  categoryInput(controls).checked=false;
  assert.equal(calls.length,beforeClear,"Unchecking group category alone never clears");
  categorySave(controls).onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(groupCategory("category-metadata"),undefined);
  assert.ok(directCategory(2),"Clearing a group category never clears a member category");
  controls=get("current-category").children[0];
  categoryInput(controls).checked=false;categorySave(controls).onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(directCategory(8),undefined);
  assert.ok(directCategory(2),"Clearing one frame does not clear another");
  await get("reload").onclick();
  assert.equal(categories.length,1,"Reload retains only explicitly saved assignments");
  vm.runInContext('videos[0].retention_only=true;videos[0].retained_frames=[2,8];renderSaved();updatePosition();setControls()',sandbox);
  controls=get("current-category").children[0];
  assert.equal(categorySave(controls).disabled,false,"Retained-only category metadata remains editable");
  categoryInput(controls).checked=true;
  rejectNext=true;
  const beforeCategoryConflict=calls.length;
  categorySave(controls).onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(vm.runInContext("saveBlocked",sandbox),true,"Category conflict uses shared save block");
  assert.equal(categorySave(get("current-category").children[0]).disabled,true,"Category controls remain blocked after conflict");
  categorySave(controls).onclick();
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(calls.length,beforeCategoryConflict+1,"No category retry after stale save");
  assert.equal(directCategory(8),undefined);
  await get("reload").onclick();
  controls=get("current-category").children[0];
  assert.equal(categoryInput(controls).checked,false,"Reload discards unsafe unsaved category choice");
  failNext=true;categoryInput(controls).checked=true;categorySave(controls).onclick();
  await vm.runInContext("saveQueue",sandbox);
  const afterCategoryLostResponse=calls.length;
  categorySave(controls).onclick();
  assert.equal(calls.length,afterCategoryLostResponse,"Unknown category delivery is never retried");
  assert.equal(vm.runInContext("saveBlocked",sandbox),true);
  await get("reload").onclick();
  assert.equal(JSON.stringify(saved),categoryDecisions,"Category edits and errors never alter Keep");

  // Integrate the actual deferred tab scripts: capture navigation must beat
  // workspace.js onclick handlers; drag-to-QA must hide the editor too.
  sandbox.MutationObserver=class {observe(){}};
  sandbox.Image=class {
    set src(value){this._src=value;this.onload?.();}
    get src(){return this._src;}
  };
  sandbox.document.createTextNode=text=>text;
  sandbox.history.replaceState=(_state,_title,url)=>{
    sandbox.location.href=String(url);sandbox.location.search=new URL(url).search;
  };
  vm.runInContext(fs.readFileSync(`${__dirname}/workspace.js`,"utf8"),sandbox);
  vm.runInContext(fs.readFileSync(`${__dirname}/learning.js`,"utf8"),sandbox);
  keyListeners.get("DOMContentLoaded")();
  vm.runInContext(`
    let syntheticWorkspaceLoads=0;
    loadWorkspace=async()=>{syntheticWorkspaceLoads++;};
    videos[0].times=Array.from({length:40},(_,i)=>i);
    videos[0].retention_only=false;videos[0].retained_frames=null;
    last=19;endTime=19;current=0;trashView=false;
    activateWorkspaceTab("review");
    document.getElementById("spacing").value="all";
  `,sandbox);
  saved=[{video:"fixture",frame:2,label:"new_state",recorded_at:"1"}];
  groups=[
    {id:"nav-unit",video:"fixture",frames:[2,4],selected_unit:true,type:"same_scene",display_number:14,color:"#123456"},
    {id:"nav-metadata",video:"fixture",frames:[3,20,30],type:"animation",display_number:15,color:"#654321"},
  ];
  categories=[{video:"fixture",category_id:"saga_map",target_type:"frame",frame:3}];
  trashed=[{video:"fixture",frame:3}];
  sandbox.syntheticState=savedState();
  vm.runInContext('state=syntheticState;buildGallery();page=1;expandedGroups.add("nav-unit");renderSaved()',sandbox);
  const contextBefore=vm.runInContext('JSON.stringify({page,last,endTime,recording,filter:$("spacing").value,expanded:[...expandedGroups]})',sandbox);
  get("gallery").children[0].children[0].children[0].onclick();
  assert.equal(get("editor-tab").hidden,false,"Gallery thumbnail opens editor intentionally");
  assert.equal(get("review-tab").hidden,true);
  assert.equal(vm.runInContext("imageReady",sandbox),true);
  get("editor-back").onclick();
  assert.equal(vm.runInContext('JSON.stringify({page,last,endTime,recording,filter:$("spacing").value,expanded:[...expandedGroups]})',sandbox),contextBefore);
  get("tab-groups").click();
  const metadataSettings=get("group-list").children[0].children[1];
  metadataSettings.children[2].children[2].children[0].onclick(); // Exact frame 30, outside window.
  assert.equal(vm.runInContext("current+1",sandbox),30);
  assert.equal(get("editor-tab").hidden,false);
  assert.match(get("editor-recording").textContent,/Recording 1: fixture/);
  assert.equal(vm.runInContext("last",sandbox),19,"Opening outside-window member does not expand gallery");
  get("next").onclick();
  assert.equal(vm.runInContext("current+1",sandbox),31,"Editor navigates whole source");
  assert.equal(get("timeline").max,39);
  get("editor-back").onclick();get("tab-editor").click();
  assert.equal(get("timeline").value,30,"Returning to editor restores its exact timeline position");
  assert.equal(vm.runInContext('JSON.stringify({page,last,endTime,recording,filter:$("spacing").value,expanded:[...expandedGroups]})',sandbox),contextBefore);
  key("1");
  assert.deepEqual(staging(),[31],"Editor keyboard stages its exact outside-window frame after image load");
  get("editor-back").onclick();
  key("1");
  assert.deepEqual(staging(),[31],"Gallery keyboard cannot stage a hidden editor frame");
  vm.runInContext('stageExactFrame(18)',sandbox);
  assert.deepEqual(staging(),[31,18],"Gallery card staging works without requiring viewer image");
  key("M");await vm.runInContext("saveQueue",sandbox);
  assert.deepEqual(groups.at(-1).frames,[18,31]);
  assert.equal(vm.runInContext("last",sandbox),19,"Grouping does not expand the gallery window");
  for (const tab of ["editor","groups","library","qa","review"]) {
    vm.runInContext("playing=true",sandbox);
    get(`tab-${tab}`).click();
    assert.equal(vm.runInContext("playing",sandbox),false,"Every navigation tab pauses playback");
    for (const other of ["review","editor","groups","library","qa","learning"]) {
      assert.equal(get(`${other}-tab`).hidden,other!==tab,`Only ${tab} panel is visible`);
    }
  }
  vm.runInContext('activateWorkspaceTab("editor");workspaceTab("qa")',sandbox);
  assert.equal(get("editor-tab").hidden,true,"QA drag navigation uses unified panel ownership");
  assert.equal(get("qa-tab").hidden,false);
  let finishPlayback;
  get("video").play=()=>new Promise(resolve=>{finishPlayback=resolve;});
  vm.runInContext('activateWorkspaceTab("editor")',sandbox);
  const pendingPlayback=get("play").onclick();
  get("tab-review").click();
  finishPlayback();await pendingPlayback;
  assert.equal(vm.runInContext("playing",sandbox),false,"Late play resolution cannot resume after tab navigation");
  assert.equal(get("video").hidden,true);

  // Shared exact member Trash/restore. Saved group/category/Keep state never changes.
  vm.runInContext('activateWorkspaceTab("groups");renderGroups()',sandbox);
  const membershipBefore=JSON.stringify(groups), categoryBeforeTrash=JSON.stringify(categories), keepBeforeTrash=JSON.stringify(saved);
  const memberControls=()=>get("group-list").children[0].children[1].children[2].children[0].children[3];
  assert.match(memberControls().children[0].textContent,/In recoverable Trash/);
  assert.equal(memberControls().children[1].textContent,"Restore from Trash");
  memberControls().children[1].onclick();await vm.runInContext("saveQueue",sandbox);
  assert.equal(trashed.some(item=>item.frame===3),false);
  assert.equal(memberControls().children[1].textContent,"Move to Trash");
  memberControls().children[1].onclick();await vm.runInContext("saveQueue",sandbox);
  assert.equal(trashed.some(item=>item.frame===3),true);
  assert.equal(JSON.stringify(groups),membershipBefore);
  assert.equal(JSON.stringify(categories),categoryBeforeTrash);
  assert.equal(JSON.stringify(saved),keepBeforeTrash);
  vm.runInContext('activateWorkspaceTab("review");page=0;expandedGroups.add("nav-unit");renderGallery()',sandbox);
  let expanded=cards().find(card=>card.dataset.group==="nav-unit");
  expanded.children[4].children[1].children[3].children[1].onclick();
  await vm.runInContext("saveQueue",sandbox);
  expanded=cards().find(card=>card.dataset.group==="nav-unit");
  assert.equal(expanded.children[4].children.length,1,"Trashed member leaves normal expansion immediately");
  assert.equal(JSON.stringify(groups),membershipBefore,"Trash never removes member references");
  vm.runInContext('renderGroups()',sandbox);
  assert.equal(get("group-list").children[0].children[0].children[2].children[1].children[3].children[1].textContent,"Restore from Trash");
  vm.runInContext('videos[0].retention_only=true;videos[0].retained_frames=[2,4];renderGroups();setControls()',sandbox);
  assert.equal(get("group-list").children[0].children[0].children[2].children[1].children[3].children[1].disabled,true);
  vm.runInContext('videos[0].retention_only=false;videos[0].retained_frames=null',sandbox);

  // Preview and confirmation issue one compact operation, not per-card writes.
  const cleanupRequests=[];
  let cleanupFailure=false, cleanupEligible=31;
  const fixtureFetch=sandbox.fetch;
  sandbox.fetch=async(url,options)=>{
    if(url!=="/api/cleanup") return fixtureFetch(url,options);
    const body=JSON.parse(options.body);cleanupRequests.push(body);
    assert.equal(options.headers["X-Review-Token"],"test");
    if(cleanupFailure) throw new Error("Unconfirmed cleanup delivery");
    if(body.action==="preview")return {ok:true,json:async()=>({
      video:0,name:"fixture",revision:String(revision),source_count:40,selected_count:4,
      existing_trash_count:5,eligible_count:cleanupEligible,preview_token:"proof"
    })};
    assert.deepEqual(body,{action:"apply",video:0,revision:String(revision),preview_token:"proof",confirm:true});
    revision++;
    return {ok:true,json:async()=>({...savedState(),cleanup_result:{moved_count:31},
      operation_message:"Moved 31 unselected frames to recoverable Trash in fixture."})};
  };
  vm.runInContext('clearStaging();clearGroupDraft();saveBlocked=false;busy=false;renderGallery()',sandbox);
  assert.match(get("cleanup-summary").textContent,/3 selected exact frames protected.*2 already in Trash.*35 unselected/);
  saved.push({video:"fixture",frame:2,label:"uncertain",recorded_at:"2999"});
  sandbox.syntheticState=savedState();
  vm.runInContext("state=syntheticState;renderGallery()",sandbox);
  assert.equal(get("selection-total").textContent,"0 selected in this recording + 2 selected groups (independent of Keep)",
    "Legacy duplicate point cases use the latest individual label");
  assert.match(get("cleanup-summary").textContent,/3 selected exact frames protected/,
    "A selected-unit member stays protected even after its individual Keep is cleared");
  saved.pop();sandbox.syntheticState=savedState();vm.runInContext("state=syntheticState",sandbox);
  const cleanupWrites=calls.length;
  await get("cleanup-preview").onclick();
  assert.match(get("cleanup-confirm-summary").textContent,/Recording 1: fixture.*exactly 31.*Preserve 4.*5 existing Trash.*40 source/);
  assert.equal(get("cleanup-confirm").open,true);
  const pendingBeforeCleanup=vm.runInContext("pendingSelections.size",sandbox);
  const unselectedCard=cards().find(card=>!card.dataset.group);
  const cardCheckbox=unselectedCard.children.find(node=>node.className==="frame-select").children[0];
  cardCheckbox.checked=!cardCheckbox.checked;cardCheckbox.onchange();
  assert.equal(vm.runInContext("pendingSelections.size",sandbox),pendingBeforeCleanup,
    "An in-flight preview cannot create an unsaved optimistic gallery selection");
  get("cleanup-cancel").onclick();
  assert.equal(cleanupRequests.length,1,"Cancel sends no mutation");
  assert.equal(calls.length,cleanupWrites);
  assert.equal(vm.runInContext("busy",sandbox),false);
  await get("cleanup-preview").onclick();
  await get("cleanup-apply").onclick();
  assert.deepEqual(cleanupRequests.map(item=>item.action),["preview","preview","apply"]);
  assert.match(get("save-status").textContent,/Moved 31/);
  assert.equal(calls.length,cleanupWrites,"No annotation, learning or export request on standalone cleanup");
  cleanupEligible=0;
  await get("cleanup-preview").onclick();
  assert.equal(get("cleanup-apply").disabled,true);
  get("cleanup-cancel").onclick();
  vm.runInContext('videos[0].retention_only=true;videos[0].retained_frames=[2,4];renderGallery()',sandbox);
  const beforeRetainedPreview=cleanupRequests.length;
  await get("cleanup-preview").onclick();
  assert.equal(cleanupRequests.length,beforeRetainedPreview,"Retained-only never requests visibility mutation");
  assert.match(get("cleanup-summary").textContent,/Retained-only/);
  vm.runInContext('videos[0].retention_only=false;videos[0].retained_frames=null',sandbox);
  cleanupEligible=31;
  await get("cleanup-preview").onclick();
  cleanupFailure=true;await get("cleanup-apply").onclick();
  assert.equal(vm.runInContext("saveBlocked",sandbox),true);
  assert.match(get("save-status").textContent,/Reload decisions and inspect Trash.*No automatic retry/);
  const failedCleanupCount=cleanupRequests.length;
  await get("cleanup-apply").onclick();await get("cleanup-preview").onclick();
  assert.equal(cleanupRequests.length,failedCleanupCount);
  sandbox.fetch=fixtureFetch;await get("reload").onclick();

  // Exact existing learning controls with the app's feedback integration.
  const learningRequests=[];
  const descendants=node=>(node.children || []).flatMap(child=>
    typeof child==="object" ? [child,...descendants(child)] : []);
  const originalQueryAll=sandbox.document.querySelectorAll;
  sandbox.document.querySelectorAll=selector=>selector==="#learning-pairs input"
    ? descendants(get("learning-pairs")).filter(node=>node.type==="checkbox") : originalQueryAll(selector);
  sandbox.fetch=async(url,options)=>{
    learningRequests.push(url);
    if(url.startsWith("/api/learning/similar"))return {ok:true,json:async()=>({
      video:0,revision:String(revision),selected_count:413,compared_pairs:85078,
      shown:2,candidate_pairs:2,hashes:{"2":"hash","4":"hash","8":"hash"},
      method:"Compared all 85078 pairs across 413 individual Keep frames.",
      pairs:[{left:{frame:2,time:1,image:"/2"},right:{frame:4,time:3,image:"/4"},difference:0},
             {left:{frame:4,time:3,image:"/4"},right:{frame:8,time:7,image:"/8"},difference:1}]
    })};
    if(url.startsWith("/api/learning?"))return {ok:true,json:async()=>({
      preview:{video:0,revision:String(revision),selected:413,source_count:1000,nonselected:587,split:"calibration"},history:[]
    })};
    return fixtureFetch(url,options);
  };
  get("learning-video").value="0";
  get("error").hidden=false;
  await get("learning-scan").onclick();
  assert.equal(get("error").hidden,true,"Successful scan clears obsolete scan failure only after success");
  assert.match(get("learning-similarity-status").textContent,/413 individual Keep frames/);
  let discardInputs=sandbox.document.querySelectorAll("#learning-pairs input");
  discardInputs[1].checked=true;discardInputs[1].onchange();
  assert.equal(discardInputs[2].checked,true,"Repeated exact frame checkboxes stay synchronized");
  get("learning-clear-discards").onclick();
  assert.ok(discardInputs.every(node=>!node.checked));
  assert.match(get("learning-status").textContent,/Cleared 1 local discard proposals across all pairs.*no decisions saved/);
  get("learning-clear-discards").onclick();
  assert.match(get("learning-status").textContent,/No local discard proposals.*does not select unreviewed/);
  vm.runInContext("learningBusy=true;learningDiscards.add(2)",sandbox);
  get("learning-clear-discards").onclick();
  assert.equal(vm.runInContext("learningDiscards.size",sandbox),1,"Clear cannot race a running learning action");
  vm.runInContext("learningBusy=false;learningDiscards.clear();busy=true",sandbox);
  const beforeBlockedScan=learningRequests.filter(url=>url.startsWith("/api/learning")).length;
  await get("learning-scan").onclick();
  assert.equal(learningRequests.filter(url=>url.startsWith("/api/learning")).length,beforeBlockedScan,"Busy save guard prevents scan network request");
  vm.runInContext("busy=false",sandbox);
  assert.ok(learningRequests.every(url=>url.startsWith("/api/learning?") || url.startsWith("/api/learning/similar") || url==="/api/client-error"),
    "Scanning and clearing proposals never POST Keep, Trash or closeout");
  sandbox.fetch=fixtureFetch;

  // Static DOM ownership checks supplement behavior tests without a live data server.
  const html=fs.readFileSync(`${__dirname}/index.html`,"utf8");
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);
  assert.equal(new Set(ids).size,ids.length,"No duplicate ids after workspace split");
  const galleryEnd=html.indexOf("</main>"), editorStart=html.indexOf('<section id="editor-tab"');
  assert.ok(galleryEnd<editorStart && html.indexOf('<div class="workspace">')>editorStart);
  assert.ok(html.indexOf('id="saved-list"')>editorStart && html.indexOf('id="gallery"')<galleryEnd);
  assert.ok(html.indexOf('id="cleanup-preview"')<html.indexOf('id="gallery"'));
  const styles=fs.readFileSync(`${__dirname}/style.css`,"utf8");
  assert.match(styles,/#gallery \.group-frame img\{[^}]*height:auto;[^}]*object-fit:contain/);
  assert.match(styles,/\.group-card \.group-inspection\{[^}]*align-items:start/);
  assert.match(styles,/#gallery\{align-items:start\}/);
  console.log("Selection, grouping/move/remove, categories, editor/tab ownership/context/keyboard, member Trash/restore, standalone cleanup preview/cancel/apply/no-retry, similarity controls and existing regressions passed");
}
main().catch(error=>{console.error(error);process.exitCode=1;});
