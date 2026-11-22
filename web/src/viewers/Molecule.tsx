import {useEffect,useRef,useState} from "react";
import wasm from "@rdkit/rdkit/RDKit_minimal.wasm?url";
import {filePath,type ViewerProps} from "./registry";
import {request,errorMessage} from "../api";

export default function Molecule({base,file,initial,quote}:ViewerProps){
  const [records,setRecords]=useState<string[]>([]);
  const [index,setIndex]=useState(initial?.type==="molecule"?initial.record-1:0);
  const indexRef=useRef(index);
  indexRef.current=index;
  const [error,setError]=useState("");
  const [image,setImage]=useState("");
  const [worker,setWorker]=useState<Worker>();
  useEffect(()=>{
    const c=new AbortController();
    const w=new Worker(new URL("./chem-worker.ts",import.meta.url),{type:"module"});
    w.onmessage=e=>{
      if(c.signal.aborted || e.data.index!==indexRef.current)return;
      if(e.data.error)setError(e.data.error);
      else setImage("data:image/svg+xml;charset=utf-8,"+encodeURIComponent(e.data.svg));
    };
    w.onerror=()=>{if(!c.signal.aborted)setError("RDKit could not start.");};
    async function load(){
      const response=await fetch(wasm,{signal:c.signal});
      if(!response.ok)throw Error("RDKit runtime unavailable");
      const bytes=await response.arrayBuffer();
      const data=await request<{records:string[]}>(filePath(base,file.id)+"/annotations",undefined,c.signal);
      if(!data.records.length)throw Error("No molecule records");
      if(c.signal.aborted)return;
      const target=Math.min(Math.max(indexRef.current,0),data.records.length-1);
      indexRef.current=target;
      setIndex(target);setRecords(data.records);setWorker(w);
      w.postMessage({bytes,record:data.records[target],index:target},[bytes]);
    }
    load().catch(e=>{if(!c.signal.aborted)setError(errorMessage(e));});
    return ()=>{c.abort();w.onmessage=null;w.onerror=null;w.terminate();};
  },[base,file.id]);
  return <>{image && <img className="molecule-canvas" alt="2D molecule" src={image}/>}
    {error && <p role="alert">{error}</p>}<div className="viewer-controls"><label>Molecule <select aria-label="Molecule" value={index} onChange={e=>{
      const next=+e.target.value;indexRef.current=next;setImage("");setError("");setIndex(next);worker?.postMessage({record:records[next],index:next});
    }}>{records.map((r,i)=><option key={i} value={i}>{i+1} · {r.split("\n",1)[0] || "Untitled"}</option>)}</select></label>
      {quote && <button className="quiet" disabled={!image} onClick={()=>quote({kind:"artifact",id:file.id,selection:{type:"molecule",record:index+1}},file.name+" · molecule "+(index+1))}>Quote molecule</button>}</div></>;
}
