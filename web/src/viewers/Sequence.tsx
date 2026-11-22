import {useEffect,useState} from "react";
import {request,errorMessage} from "../api";
import {filePath,type ViewerProps} from "./registry";

type Record = {record:number;name:string;sequence:string};
type Track = {record:number;start:number;end:number;label:string};
type Data = {records:Record[]; tracks:Track[]; source?:{run:string;hash:string}};
export default function Sequence({base,file,quote,initial}:ViewerProps) {
  const prior=initial?.type==="sequence"?initial:undefined;
  const [data,setData]=useState<Data>();
  const [error,setError]=useState("");
  const [index,setIndex]=useState(prior?.record??1);
  const [start,setStart]=useState(prior?.start??1);
  const [end,setEnd]=useState(prior?.end??1);
  const [offset,setOffset]=useState(Math.floor(((prior?.start??1)-1)/240)*240);
  useEffect(()=>{
    const c=new AbortController();
    request<Data>(filePath(base,file.id)+"/annotations",undefined,c.signal).then(setData)
      .catch(e=>{if(!c.signal.aborted)setError(errorMessage(e));});
    return ()=>c.abort();
  },[base,file.id]);
  if(error) return <p role="alert">{error}</p>;
  if(!data) return <p role="status">Loading sequence…</p>;
  const record=data.records[index-1];
  const valid=Number.isInteger(start) && Number.isInteger(end) && start>=1 && end>=start && end<=record.sequence.length;
  function range(a:number,b:number){setStart(a);setEnd(b);setOffset(Math.floor((a-1)/240)*240);}
  return <><label>Sequence <select value={index} onChange={e=>{setIndex(+e.target.value);range(1,1);}}>
    {data.records.map(r=><option key={r.record} value={r.record}>{r.name} · {r.sequence.length}</option>)}</select></label>
    <p>Positions {offset+1}–{Math.min(offset+240,record.sequence.length)} of {record.sequence.length}</p>
    <div className="sequence-letters">{Array.from(record.sequence.slice(offset,offset+240),(aa,i)=>{
      const pos=offset+i+1;
      return <button key={pos} aria-label={aa+" "+pos} aria-pressed={pos>=start && pos<=end}
        onClick={e=>e.shiftKey?range(Math.min(start,pos),Math.max(start,pos)):range(pos,pos)}>{aa}</button>;
    })}</div>
    <div className="actions"><button className="quiet" disabled={!offset} onClick={()=>setOffset(offset-240)}>Previous residues</button>
      <button className="quiet" disabled={offset+240>=record.sequence.length} onClick={()=>setOffset(offset+240)}>Next residues</button></div>
    <div className="viewer-controls"><label>Start <input type="number" min={1} value={start} onChange={e=>setStart(+e.target.value)}/></label>
      <label>End <input type="number" min={start} max={record.sequence.length} value={end} onChange={e=>setEnd(+e.target.value)}/></label>
      {quote && <button className="quiet" disabled={!valid} onClick={()=>quote({kind:"artifact",id:file.id,
        selection:{type:"sequence",record:index,start,end}},record.name+" · "+start+"–"+end)}>Quote range</button>}</div>
    {data.source && <section><h3>Annotations</h3><small>Run {data.source.run} · {data.source.hash}</small>
      {data.tracks.filter(t=>t.record===index).map((t,i)=><button key={i} className="quiet" onClick={()=>range(t.start,t.end)}>{t.label} · {t.start}–{t.end}</button>)}</section>}</>;
}
