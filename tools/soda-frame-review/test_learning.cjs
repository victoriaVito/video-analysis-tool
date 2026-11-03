const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor() {this.value="";this.checked=true;this.disabled=false;this.open=false;this.textContent="";this.children=[];this.dataset={};}
  replaceChildren(...items) {this.children=items;}
  append(...items) {this.children.push(...items);}
  showModal() {this.open=true;}
  close() {this.open=false;}
}
const elements=new Map();
const get=id=>{
  if(!elements.has(id))elements.set(id,new Element());
  return elements.get(id);
};
let selected=82,posts=[],errors=[],failPost=false,tabCalls=[],revision="fixture-revision",events=[];
const descendants=element=>[...element.children].flatMap(child=>child instanceof Element ? [child,...descendants(child)] : []);
const receipt={status:"complete",video:5,selected:82,curation_applied:true,recoverably_hidden:18341,
  id:"fixture-run",package:"data/fixture",diagram:"soda-fixture.html",request:"Inspect saved evidence."};
const context=vm.createContext({
  $,document:{querySelectorAll:selector=>selector==="#learning-pairs input"
    ? descendants(get("learning-pairs")).filter(x=>x.type==="checkbox") : [get("nav-button")],getElementById:get,
    addEventListener:()=>{},dispatchEvent:event=>events.push(event.type),createElement:()=>new Element(),createTextNode:text=>text},
  URLSearchParams,location:{search:"?video=5"},Option:function(text,value){this.text=text;this.value=value;},
  state:{},recording:5,pendingSaves:0,busy:false,saveBlocked:false,
  Event,AbortSignal,time:value=>String(value),imageURL:(video,frame,width)=>`/fixture/${video}/${frame}/${width}`,
  workspaceTab:name=>tabCalls.push(name),fail:error=>errors.push(error.message),
  navigator:{clipboard:{writeText:async text=>{context.copied=text;}}},
  request:async url=>{
    if(url==="/api/videos")return [{name:"fixture"}];
    if(url==="/api/state")return {saved:true};
    if(url.startsWith("/api/learning/similar"))return {video:5,revision,hashes:{"3":"fixture-hash","7":"fixture-hash","9":"other-hash"},
      shown:2,candidate_pairs:2,method:"Fixture similarity",
      pairs:[{left:{frame:3,time:1,image:"/3"},right:{frame:7,time:2,image:"/7"},difference:0,identical_file:true},
        {left:{frame:7,time:2,image:"/7"},right:{frame:9,time:3,image:"/9"},difference:1,identical_file:false}]};
    return {preview:{video:5,name:"fixture",revision,selected,source_count:18423,
      nonselected:18423-selected,split:"holdout"},history:[receipt]};
  },
  workspacePost:async(url,payload)=>{
    posts.push({url,payload});
    if(failPost)throw new Error("Unconfirmed fixture response. Refresh saved closeouts before retrying.");
    return receipt;
  },
  buildGallery:()=>{},renderSaved:()=>{},updatePosition:()=>{},setControls:()=>{},
});
function $(id) {return get(id);}
vm.runInContext(fs.readFileSync(path.join(__dirname,"learning.js"),"utf8"),context);
async function evaluate(code) {return vm.runInContext(code,context);}

(async()=>{
  await evaluate("openLearning()");
  assert.match(get("learning-summary").textContent,/82 retained selections/);
  get("learning-hide").checked=false;
  get("learning-compare").checked=false;
  get("learning-notes").value="Preserve counters.";
  await get("learning-run").onclick();
  assert.equal(get("learning-confirm").open,true);
  assert.equal(posts.length,0);
  assert.match(get("learning-confirm-summary").textContent,/Recording 6.*82/);
  assert.match(get("learning-confirm-summary").textContent,/Do not change trash/);
  get("learning-cancel").onclick();
  assert.equal(get("learning-confirm").open,false);
  get("learning-confirm-run").onclick();
  assert.equal(posts.length,0);

  get("learning-hide").checked=true;
  await get("learning-run").onclick();
  await get("learning-confirm-run").onclick();
  assert.equal(posts.length,1);
  assert.equal(posts[0].payload.video,5);
  assert.equal(posts[0].payload.confirm,true);
  assert.equal(posts[0].payload.hide_nonselected,true);
  assert.equal(posts[0].payload.compare,false);
  assert.equal(context.recording,5);
  assert.equal(context.state.saved,true);
  assert.equal(events.at(-1),"review-updated");
  assert.match(get("learning-status").textContent,/Closeout saved/);
  assert.equal(get("learning-prompt").value,receipt.request);
  await get("learning-copy").onclick();
  assert.equal(context.copied,receipt.request);
  assert.equal(get("nav-button").disabled,false);

  context.saveBlocked=true;
  await get("learning-run").onclick();
  assert.match(errors.at(-1),/reload blocked decisions/);
  assert.equal(posts.length,1);
  context.saveBlocked=false;selected=0;
  await get("learning-run").onclick();
  assert.match(errors.at(-1),/Select at least one/);
  assert.equal(posts.length,1);

  selected=82;failPost=true;
  await get("learning-run").onclick();
  await get("learning-confirm-run").onclick();
  assert.match(get("learning-status").textContent,/Unconfirmed fixture response/);
  assert.equal(posts.length,2);
  assert.equal(get("nav-button").disabled,false);
  context.location.search="?video=5&tab=learning";
  await evaluate("restoreWorkspaceTab()");
  assert.equal(tabCalls.at(-1),"learning");

  failPost=false;
  await get("learning-scan").onclick();
  assert.equal(get("learning-pairs").children.length,2);
  const inputs=descendants(get("learning-pairs")).filter(x=>x.type==="checkbox");
  assert.equal(inputs.length,4);
  assert.equal(inputs.filter(x=>x.checked).length,0);
  inputs[1].checked=true;inputs[1].onchange();
  assert.equal(inputs[2].checked,true);
  assert.match(get("learning-discard-summary").textContent,/1 selected frames/);
  get("learning-hide").checked=false;
  await get("learning-run").onclick();
  assert.match(errors.at(-1),/Enable recoverable cleanup/);
  get("learning-hide").checked=true;
  await get("learning-run").onclick();
  assert.match(get("learning-confirm-summary").textContent,/retain 81 of 82/);
  assert.match(get("learning-confirm-summary").textContent,/Discard reviewed frames: 7/);
  assert.match(get("learning-confirm-summary").textContent,/18342 total/);
  await get("learning-confirm-run").onclick();
  assert.deepEqual(Array.from(posts.at(-1).payload.discard_frames),[7]);
  assert.equal(posts.at(-1).payload.similarity_hashes["7"],"fixture-hash");

  revision="changed-feedback";
  await get("learning-run").onclick();
  assert.match(errors.at(-1),/Decisions changed; review/);
  assert.equal(get("learning-pairs").children.length,0);
  assert.match(get("learning-discard-summary").textContent,/0 selected frames/);
  await get("learning-scan").onclick();
  const restored=descendants(get("learning-pairs")).filter(x=>x.type==="checkbox");
  restored[0].checked=true;restored[0].onchange();
  get("learning-clear-discards").onclick();
  assert.equal(restored.filter(x=>x.checked).length,0);
  console.log("Learning confirmation, similarity proposals, synchronized discards, cancellation, stale scan, receipts and no-retry checks passed");
})().catch(error=>{console.error(error);process.exitCode=1;});
