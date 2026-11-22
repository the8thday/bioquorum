import {useEffect,useRef,useState, type PointerEvent} from "react";
import {errorMessage,request} from "../api";
import {filePath,type ViewerProps} from "./registry";

type Point={sample_id:string;x:number;y:number};
type XY={x:number;y:number};
function position(e:PointerEvent<SVGSVGElement>):XY {
  const box=e.currentTarget.getBoundingClientRect();
  return {x:(e.clientX-box.left)/box.width,y:(e.clientY-box.top)/box.height};
}
export default function Figure({base,file,quote,products,initial,comparedFigure}:ViewerProps) {
  const [points,setPoints]=useState<Point[]>([]);
  const [chosen,setChosen]=useState<number[]>(initial?.type==="figure"?initial.points:[]);
  const [compare,setCompare]=useState("");
  const [error,setError]=useState("");
  const anchor=useRef<(XY & {pointIndex:number|null})|null>(null);
  const [extent,setExtent]=useState<XY|null>(null);
  const path=filePath(base,file.id);
  useEffect(()=>{
    const c=new AbortController();
    request<{points:Point[]}>(path+"/annotations",undefined,c.signal).then(data=>setPoints(data.points))
      .catch(e=>{if(!c.signal.aborted)setError(errorMessage(e));});
    return ()=>c.abort();
  },[path]);
  useEffect(()=>{if(comparedFigure)setCompare(comparedFigure);},[comparedFigure]);
  const a=anchor.current;
  function start(e:PointerEvent<SVGSVGElement>) {
    // Capture retargets pointerup to the SVG, so save the original sample here.
    const pointIndex=e.target instanceof SVGCircleElement ? Number(e.target.dataset.point) : null;
    anchor.current={...position(e),pointIndex};
    e.currentTarget.setPointerCapture(e.pointerId);
    setExtent(anchor.current);
  }
  function finish(e:PointerEvent<SVGSVGElement>) {
    if(e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    const b=position(e),a=anchor.current;
    if(!a) return;
    if(Math.abs(a.x-b.x)+Math.abs(a.y-b.y)<0.01) {
      setChosen(a.pointIndex===null?[]:[a.pointIndex]);
    } else {
      setChosen(points.flatMap((p,i)=>p.x>=Math.min(a.x,b.x)&&p.x<=Math.max(a.x,b.x)&&p.y>=Math.min(a.y,b.y)&&p.y<=Math.max(a.y,b.y)?[i]:[]));
    }
    anchor.current=null;setExtent(null);
  }
  const alternatives=products.filter(p=>p.preview==="figure" && p.id!==file.id);
  return <>{error && <p role="alert">{error}</p>}<div className="figure-pair"><figure className="figure-frame"><div className="figure-layer">
    <img className="reader-figure" alt={file.name} src={path+"/preview/image"} onError={()=>setError("The recorded image could not be displayed.")}/>
    {points.length>0 && <svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-label="Select plotted samples"
      onPointerDown={start}
      onPointerMove={e=>{if(anchor.current)setExtent(position(e));}} onPointerUp={finish}
      onPointerCancel={e=>{if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId);anchor.current=null;setExtent(null);}}>
      {points.map((p,i)=><circle key={i} data-point={i} cx={p.x*1000} cy={p.y*1000} r="9" className={chosen.includes(i)?"chosen":""}><title>{p.sample_id}</title></circle>)}
      {a && extent && <rect x={1000*Math.min(a.x,extent.x)} y={1000*Math.min(a.y,extent.y)} width={1000*Math.abs(a.x-extent.x)} height={1000*Math.abs(a.y-extent.y)}/>}</svg>}</div>
    {compare && <figcaption>{file.name} · v{file.version}</figcaption>}</figure>
    {compare && <figure className="figure-frame"><img className="reader-figure" alt="Comparison figure" src={filePath(base,compare)+"/preview/image"}/>
      <figcaption>{alternatives.find(p=>p.id===compare)?.name} · v{alternatives.find(p=>p.id===compare)?.version}</figcaption></figure>}</div>
    {!!points.length && <div className="viewer-controls"><label>Sample <select value={chosen.length===1?chosen[0]:""} onChange={e=>setChosen(e.target.value===""?[]:[+e.target.value])}>
      <option value="">Select a point or drag a region</option>{points.map((p,i)=><option key={i} value={i}>{p.sample_id}</option>)}</select></label>
      {quote && <button className="quiet" disabled={!chosen.length || chosen.length>64} onClick={()=>quote({kind:"artifact",id:file.id,
        selection:{type:"figure",points:chosen}},file.name+" · "+chosen.map(i=>points[i].sample_id).join(", "))}>Quote selected samples ({chosen.length})</button>}
      {chosen.length>64 && <span className="muted" role="status">Select at most 64 points to quote.</span>}</div>}
    {!!alternatives.length && <label>Compare with <select aria-label="Compare with" value={compare} onChange={e=>setCompare(e.target.value)}><option value="">Choose figure</option>
      {alternatives.map(p=><option key={p.id} value={p.id}>{p.name} · v{p.version}</option>)}</select></label>}</>;
}
