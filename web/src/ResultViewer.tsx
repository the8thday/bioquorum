import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, request } from "./api";
import type { Product } from "./Artifacts";
import { ResultTable, tableAt, type AnswerTable } from "./ResultTables";
import type { EvidenceRef } from "./types";

export type ResultItem = Product | AnswerTable;
type Page = {header:string[]; rows:string[][]; more:boolean};
const filePath = (base:string,id:string) => base+"/artifacts/"+encodeURIComponent(id);

function Report({text,file,products,base,open}: {text:string;file:Product;products:Product[];base:string;open:(p:Product)=>void}) {
  if (/\.txt$/i.test(file.name)) return <pre className="report-text">{text}</pre>;
  const lines=text.split(/\r?\n/);
  const segments=lines.map(t=>[{text:t}]);
  const blocks:ReactNode[]=[];
  let code:string[]|null=null;
  for(let i=0;i<lines.length;i++){
    const line=lines[i].trim();
    if (/^```/.test(line)) {
      if (code) {blocks.push(<pre key={i}>{code.join("\n")}</pre>); code=null;} else code=[];
      continue;
    }
    if (code) {code.push(lines[i]);continue;}
    if (!line) continue;
    const cells = tableAt(segments,i);
    if (cells) {blocks.push(<ResultTable key={i} cells={cells}/>);i+=cells.length;continue;}
    const heading=/^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {blocks.push(heading[1].length===1?<h2 key={i}>{heading[2]}</h2>:<h3 key={i}>{heading[2]}</h3>);continue;}
    const image=/^!\[([^\]]*)\]\(([^)]+)\)$/.exec(line);
    if (image && file.run && !/^(?:[a-z]+:|\/|\\)/i.test(image[2])) {
      const parts=file.name.split("/").slice(0,-1);
      let inside=true;
      for(const part of image[2].split("/")) {if(part===".."){if(!parts.length){inside=false;break;}parts.pop();}else if(part && part!==".") parts.push(part);}
      const matches=products.filter(p=>p.run===file.run && p.preview==="figure" && p.name===parts.join("/"));
      if(inside && matches.length===1) {
        const figure=matches[0];
        blocks.push(<button key={i} className="report-figure" onClick={()=>open(figure)}><img alt={image[1] || figure.name} src={filePath(base,figure.id)+"/preview/image"}/></button>);
        continue;
      }
    }
    blocks.push(<p key={i}>{lines[i]}</p>);
  }
  if(code) blocks.push(<pre key="code-end">{code.join("\n")}</pre>);
  return <article className="report-document">{blocks}</article>;
}

function FileContents({file,base,products,open}: {file:Product;base:string;products:Product[];open:(p:Product)=>void}) {
  const [page,setPage]=useState(1);
  const [table,setTable]=useState<Page>();
  const [text,setText]=useState<string>();
  const [error,setError]=useState("");
  const path=filePath(base,file.id);
  useEffect(()=>{
    const c=new AbortController();
    setError("");setTable(undefined);
    const load=file.preview==="table" ? request<Page>(path+"/table?page="+page,undefined,c.signal).then(setTable)
      :file.preview==="report" ? request<{text:string}>(path+"/preview/text",undefined,c.signal).then(data=>setText(data.text)) : undefined;
    load?.catch(e=>{if(!c.signal.aborted)setError(errorMessage(e));});
    return ()=>c.abort();
  },[path,page,file.preview]);
  if(error) return <p role="alert">{error}</p>;
  if(file.preview==="figure") return <img className="reader-figure" alt={file.name} src={path+"/preview/image"} onError={()=>setError("The recorded image could not be displayed.")}/>;
  if(file.preview==="report" && text!==undefined) return <Report {...{text,file,products,base,open}}/>;
  if(!table) return <p role="status">Loading preview…</p>;
  return <><div className="table-scroll"><table><thead><tr>{table.header.map((s,i)=><th key={i} scope="col">{s}</th>)}</tr></thead>
    <tbody>{table.rows.map((r,i)=><tr key={i}>{r.map((s,j)=><td key={j}>{s}</td>)}</tr>)}</tbody></table></div>
    {(page>1 || table.more) && <div className="actions"><button className="quiet" disabled={page===1} onClick={()=>setPage(page-1)}>Previous page</button> Page {page}
      <button className="quiet" disabled={!table.more} onClick={()=>setPage(page+1)}>Next page</button></div>}</>;
}

export function ResultViewer({items,active,base,products,select,close,open,evidence,inspector}: {
  items:ResultItem[];active:ResultItem;base:string;products:Product[];select:(id:string)=>void;
  inspector:()=>void;close:()=>void;open:(p:Product)=>void;evidence:(id:string,ref:EvidenceRef)=>void;
}) {
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
        <FileContents key={active.id} file={active} {...{base,products,open}}/></>}
  </section>;
}
