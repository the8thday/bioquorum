import {useEffect,useRef,useState} from "react";
import igv from "igv";
import {filePath,type ViewerProps} from "./registry";
import {request,errorMessage} from "../api";

interface Interval {chr:string;start:number;end:number}
export default function Genome({base,file,quote,initial}:ViewerProps) {
  const host=useRef<HTMLDivElement>(null);
  const browser=useRef<Awaited<ReturnType<typeof igv.createBrowser>>|null>(null);
  const [ranges,setRanges]=useState<Interval[]>([]);
  const [index,setIndex]=useState(initial?.type==="genome"?initial.record-1:0);
  const [error,setError]=useState("");
  const locus=(r:Interval)=>r.chr+":"+(r.start+1)+"-"+r.end;
  useEffect(()=>{
    const c=new AbortController();
    async function load(){
      const {records}=await request<{records:Interval[]}>(filePath(base,file.id)+"/annotations",undefined,c.signal);
      if(!host.current || c.signal.aborted)return;
      const config={reference:{id:"recorded-intervals",name:"Observed track extents",format:"chromsizes",
        fastaURL:new URL(filePath(base,file.id)+"/preview/chrom-sizes",location.origin).href},loadDefaultGenomes:false,showNavigation:false,
        showIdeogram:false,showRuler:true,showControls:false,showSequence:false,
        locus:locus(records[initial?.type==="genome"?initial.record-1:0]),
        tracks:[{type:"annotation" as const,name:"Recorded BED intervals",features:records}]};
      const instance=await igv.createBrowser(host.current,config);
      if(c.signal.aborted){igv.removeBrowser(instance);return;}
      browser.current=instance;setRanges(records);
    }
    load().catch(e=>{if(!c.signal.aborted)setError(errorMessage(e));});
    return ()=>{c.abort();if(browser.current)igv.removeBrowser(browser.current);browser.current=null;};
  },[base,file.id,initial]);
  return <><div ref={host} className="genome-canvas" aria-label="Genome track"/>
    {error && <p role="alert">{error}</p>}
    {!!ranges.length && <><p>BED intervals · 0-based, end exclusive. Axis bounds follow the recorded intervals. Reference assembly unspecified.</p>
      <div className="viewer-controls"><label>Interval <select aria-label="Interval" value={index} onChange={e=>{
        const next=+e.target.value;setIndex(next);void browser.current?.search(locus(ranges[next]));
      }}>{ranges.map((r,i)=><option key={i} value={i}>{r.chr} [{r.start}, {r.end})</option>)}</select></label>
        {quote && <button className="quiet" onClick={()=>quote({kind:"artifact",id:file.id,selection:{type:"genome",record:index+1}},
          file.name+" · "+ranges[index].chr+" ["+ranges[index].start+", "+ranges[index].end+")")}>Quote interval</button>}</div></>}</>;
}
