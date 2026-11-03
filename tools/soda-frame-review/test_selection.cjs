const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function element() {
  return {
    textContent:"", value:"", hidden:true, disabled:false,
    classList:{toggle(){}}, dataset:{}, children:[],
    append(...children){this.children.push(...children);},
    replaceChildren(...children){this.children=children;},
    setAttribute(){}, addEventListener(){}, pause(){}, scrollIntoView(){},
    getAttribute(){return null;},
  };
}
async function main() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id,element());
    return elements.get(id);
  };
  const calls = [], history = [], errors = [];
  let revision = 0, rejectNext = false;
  let saved = [], trashed = [];
  const sandbox = {
    console, Map, Set, Date, Blob, URL, setTimeout, AbortSignal, Event,
    document:{
      getElementById:get, querySelectorAll:()=>[], querySelector:element, addEventListener(){}, dispatchEvent(){},
      createElement:element,createDocumentFragment:element
    },
    window:{addEventListener(){}},
    fetch:async(url,options)=>{
      const body=JSON.parse(options.body);
      calls.push(body);
      await new Promise(resolve=>setTimeout(resolve,5));
      if(rejectNext) {
        rejectNext=false;errors.push("conflict");
        return {ok:false,json:async()=>({error:"Concurrent change"})};
      }
      assert.equal(body.revision,String(revision),"Every save uses the latest returned revision");
      history.push({...body});
      revision++;
      if (body.action) {
        trashed=trashed.filter(x=>x.frame!==body.frame);
        if(body.action==="trash")trashed.push({video:"fixture",frame:body.frame});
      } else {
        saved=saved.filter(x=>x.frame!==body.frame);
        saved.push({video:"fixture",frame:body.frame,label:body.label,recorded_at:String(revision)});
      }
      return {ok:true,json:async()=>({labels:saved,trashed,intervals:[],revision:String(revision),token:"test"})};
    },
  };
  vm.createContext(sandbox);
  const source=fs.readFileSync(`${__dirname}/app.js`,"utf8").replace(
    /^initialize\(\)\.catch.*$/m,"");
  vm.runInContext(source,sandbox);
  vm.runInContext(`
    videos=[{name:"fixture",times:[0,1,2,3],preview:false}];
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
  const beforeConflict=calls.length;
  rejectNext=true;
  vm.runInContext("pick(4,true);pick(2,false);",sandbox);
  await vm.runInContext("saveQueue",sandbox);
  assert.equal(calls.length,beforeConflict+1,"No queued writes continue after a failed save");
  assert.equal(errors.length,1);
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
  console.log("Selection, trash, approved-only, diagram reveal/staleness and failure-stop checks passed");
}
main().catch(error=>{console.error(error);process.exitCode=1;});
