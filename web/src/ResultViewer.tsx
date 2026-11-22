import {Suspense, useEffect, useRef} from "react";
import type {Product} from "./Artifacts";
import {ResultTable, type AnswerTable} from "./ResultTables";
import type {EvidenceRef, TurnRef} from "./types";
import {viewerFor, filePath} from "./viewers/registry";

export type ResultItem = Product | AnswerTable;
export function ResultViewer({items,active,base,products,select,close,open,evidence,inspector,quote,initial,comparedFigure}: {
  items:ResultItem[];active:ResultItem;base:string;products:Product[];select:(id:string)=>void;
  comparedFigure?:string;
  initial?:TurnRef["selection"];
  quote?:(item:TurnRef,label:string)=>void;
  inspector:()=>void;close:()=>void;open:(p:Product)=>void;evidence:(id:string,ref:EvidenceRef)=>void;
}) {
  const Viewer="cells" in active ? undefined : viewerFor(active.preview)?.Component;
  const heading=useRef<HTMLHeadingElement>(null);
  useEffect(()=>{heading.current?.focus({preventScroll:true});},[active.id]);
  return <section aria-label="Result viewer" className="result-viewer" onKeyDown={e=>{
    if(e.key==="Escape"){e.stopPropagation();close();}
  }}>
    <div className="viewer-bar"><nav className="result-tabs" aria-label="Open results">{items.map(item=><button key={item.id} className="quiet" title={item.name}
      aria-pressed={active.id===item.id} onClick={()=>select(item.id)}>{item.name}</button>)}</nav><button className="quiet back-inspector" onClick={inspector}>Inspector</button></div>
    <header><div><h2 ref={heading} tabIndex={-1}>{active.name}</h2>
      <small>{"cells" in active ? "From the answer" : active.preview+" · v"+active.version+" · "+active.status+(active.turn_number != null ? " · Turn "+active.turn_number : active.turn ? " · Turn "+active.turn : "")}</small></div>
      <button className="icon-button" aria-label="Close preview" title="Close preview" onClick={close}>×</button></header>
    {"cells" in active ? <ResultTable cells={active.cells} onEvidence={s=>{if(s.ref)evidence(active.answer,s.ref);}}/> :
      <><a className="reader-download" href={filePath(base,active.id)+"/file"} download>Download original</a>
        {Viewer && <Suspense fallback={<p role="status">Loading preview…</p>}><Viewer initial={initial} key={active.id+JSON.stringify(initial)} file={active} {...{base,products,open,quote,comparedFigure}}/></Suspense>}</>}
  </section>;
}
