import {useEffect, useState} from "react";
import {errorMessage, request} from "../api";
import {filePath, type ViewerProps} from "./registry";

interface Page {
  header:string[]; rows:string[][]; row_numbers:number[]; more:boolean;
  statistics:null|{count:number; nonempty:number; numeric:number; min:number|null; max:number|null};
}

export default function Table({file,base,quote,initial}:ViewerProps) {
  const [page,setPage]=useState(initial?.type==="table"?Math.ceil(initial.row/30):1);
  const [filter,setFilter]=useState("");
  const [query,setQuery]=useState("");
  const [sort,setSort]=useState("");
  const [desc,setDesc]=useState(false);
  const [stats,setStats]=useState("");
  const [data,setData]=useState<Page>();
  const [columns,setColumns]=useState<string[]>([]);
  const [error,setError]=useState("");
  useEffect(()=>{
    const controller=new AbortController();
    const params=new URLSearchParams({page:String(page),q:query,order:desc?"desc":"asc"});
    if (sort!=="") params.set("sort",sort);
    if (stats!=="") params.set("stats",stats);
    setData(undefined);setError("");
    request<Page>(filePath(base,file.id)+"/table?"+params,undefined,controller.signal)
      .then(result=>{if(!controller.signal.aborted){setData(result);setColumns(result.header);}})
      .catch(e=>{if(!controller.signal.aborted)setError(errorMessage(e));});
    return ()=>controller.abort();
  },[base,file.id,page,query,sort,desc,stats]);
  function cite(row:number,column?:number) {
    quote?.({kind:"artifact",id:file.id,selection:{type:"table",row,column}},
      file.name+" · row "+row+(column===undefined?"":" · "+columns[column]));
  }
  const options=columns.map((name,i)=><option key={i} value={i}>{name || "Column "+(i+1)}</option>);
  const s=data?.statistics;
  return <>
    <form className="viewer-controls" onSubmit={e=>{e.preventDefault();setPage(1);setQuery(filter);}}>
      <input aria-label="Filter table rows" maxLength={256} placeholder="Filter rows" value={filter} onChange={e=>setFilter(e.target.value)}/>
      <button className="quiet">Filter</button>
      <label>Sort <select aria-label="Sort" value={sort} onChange={e=>{setPage(1);setSort(e.target.value);}}>
        <option value="">Original order</option>{options}</select></label>
      {sort!=="" && <button type="button" className="quiet" onClick={()=>{setPage(1);setDesc(!desc);}}>{desc?"Descending":"Ascending"}</button>}
      <label>Statistics <select value={stats} onChange={e=>setStats(e.target.value)}><option value="">Choose column</option>{options}</select></label>
    </form>
    {s && <p>{s.count} matched rows · {s.nonempty} nonempty · {s.numeric} numeric{s.numeric>0 && <> · min {s.min} · max {s.max}</>}</p>}
    {error ? <p role="alert">{error}</p> : !data ? <p role="status">Loading preview…</p> :
      <><div className="table-scroll"><table><thead><tr><th scope="col">Row</th>{data.header.map((v,i)=><th key={i} scope="col">{v}</th>)}</tr></thead>
        <tbody>{data.rows.map((cells,i)=><tr key={data.row_numbers[i]}><th scope="row">{quote?
          <button className="evidence-link" title="Quote row" onClick={()=>cite(data.row_numbers[i])}>{data.row_numbers[i]}</button>:data.row_numbers[i]}</th>
          {cells.map((value,j)=><td key={j} className={initial?.type==="table" && initial.row===data.row_numbers[i] && (initial.column==null || initial.column===j)?"selected-cell":undefined}>{quote && j<columns.length?
            <button className="evidence-link" title="Quote cell" onClick={()=>cite(data.row_numbers[i],j)}>{value || "(empty)"}</button>:value}</td>)}</tr>)}</tbody></table></div>
        {(data.more || page>1) && <div className="actions"><button className="quiet" disabled={page===1} onClick={()=>setPage(page-1)}>Previous page</button> Page {page}
          <button className="quiet" disabled={!data.more} onClick={()=>setPage(page+1)}>Next page</button></div>}</>}
  </>;
}
