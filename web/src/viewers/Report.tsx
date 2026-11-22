import {useEffect, useState, type ReactNode} from "react";
import {errorMessage, request} from "../api";
import type {Product} from "../Artifacts";
import {ResultTable, tableAt} from "../ResultTables";
import {filePath, type ViewerProps} from "./registry";
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

export default function ReportViewer(props:ViewerProps) {
  const initial=props.initial?.type==="report" ? props.initial : undefined;
  const [start,setStart]=useState(initial?.start ?? 1);
  const [end,setEnd]=useState(initial?.end ?? 1);
  const [text,setText]=useState<string>();
  const [error,setError]=useState("");
  useEffect(()=>{
    const controller=new AbortController();
    request<{text:string}>(filePath(props.base,props.file.id)+"/preview/text",undefined,controller.signal)
      .then(result=>setText(result.text)).catch(e=>{if(!controller.signal.aborted)setError(errorMessage(e));});
    return ()=>controller.abort();
  },[props.base,props.file.id]);
  if(error) return <p role="alert">{error}</p>;
  if(text===undefined) return <p role="status">Loading preview…</p>;
  const lines=text.split("\n");
  const valid=Number.isInteger(start) && Number.isInteger(end) && start>=1 && start<=end && end<=lines.length;
  return <><Report {...props} text={text}/>{props.quote &&
    <details open={initial ? true : undefined} className="report-selection"><summary>Select report lines</summary>
      <div className="viewer-controls"><label>First line <input type="number" min={1} max={lines.length} value={start} onChange={e=>setStart(+e.target.value)}/></label>
        <label>Last line <input type="number" min={start} max={lines.length} value={end} onChange={e=>setEnd(+e.target.value)}/></label>
        <button className="quiet" disabled={!valid} onClick={()=>props.quote?.({kind:"artifact",id:props.file.id,
          selection:{type:"report",start,end}},props.file.name+" · lines "+start+"–"+end)}>Quote lines</button></div>
      {valid && <pre className="report-text">{lines.slice(start-1,end).join("\n")}</pre>}
    </details>}</>;
}
