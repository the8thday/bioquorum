import { useEffect, useState } from "react";
import { Dialog } from "./Dialog";
import { SourceRecord } from "./ProgramOverview";
import { errorMessage, request } from "./api";
import { useProjectRead } from "./useProjectRead";
import type { MaterialVersion } from "./Materials";

type Comparison = {
  old_claim_id:string; new_claim_id:string|null; old_text:string; new_text:string|null;
  old_status:string; new_status:string|null;
  values:{name:string; old_value:string|number|null; new_value:string|number|null; unit?:string|null; new_unit?:string|null}[];
};
type Operation = {id:string; session_id:string|null; status:string; reason?:string; repair_prompt?:string; comparisons:Comparison[]};
type Entry = {id:string; event_id:string; current?:boolean; unit?:string|null; name?:string; text?:string; value?:string|number; language?:string; hash?:string; session_id?:string};
type Dependencies = Record<"runs"|"values"|"figures"|"claims"|"decisions"|"bundles",Entry[]> & {unverified_bundles?:string[]; ambiguous_inputs?:{run_id:string;path:string;candidate_versions:string[]}[]};
type Impact = {
  old_version_id:string; replacement_version_id:string|null; impact:Dependencies;
  estimated_model_usd:number; estimate_basis:string; can_recompute:boolean; reason:string|null; operations:Operation[];
};

function showValue(value:string|number|null,unit?:string|null) {
  return value == null ? "Unavailable" : String(value)+(unit ? " "+unit : "");
}

export function ComparisonTable({comparisons}:{comparisons:Comparison[]}) {
  return <>{comparisons.map(c=><section key={c.old_claim_id} className="correction-comparison">
    <h3>Claim comparison</h3>
    <div className="claim-pair">
      <div><strong>Original claim</strong><p>{c.old_text}</p><small>{c.old_status}</small></div>
      <div><strong>Recomputed claim</strong><p>{c.new_text ?? "No revised claim recorded."}</p><small>{c.new_status}</small></div>
    </div>
    {!!c.values.length && <table><caption>Recorded values</caption><thead><tr>
      <th scope="col">Measure</th><th scope="col">Original</th><th scope="col">Recomputed</th>
    </tr></thead><tbody>{c.values.map(v=><tr key={v.name}><th scope="row">{v.name}</th>
      <td>{showValue(v.old_value,v.unit)}</td><td>{showValue(v.new_value, v.new_unit === undefined ? v.unit : v.new_unit)}</td></tr>)}</tbody></table>}
  </section>)}</>;
}

function UsedBy({data,open}:{data:Dependencies;open:(id:string)=>void}) {
  const labels:Record<Exclude<keyof Dependencies,"unverified_bundles" | "ambiguous_inputs">,string> = {runs:"Runs",values:"Values",figures:"Figures",claims:"Claims",decisions:"Decisions",bundles:"Exported bundles"};
  const keys = Object.keys(labels) as (keyof typeof labels)[];
  const empty = !keys.some(k=>data[k].length);
  return <>{empty && !data.ambiguous_inputs?.length && !data.unverified_bundles?.length && <p>No downstream records found.</p>}<dl className="used-by">{keys.map(k=>{
    const entries = data[k];
    if (!entries.length) return null;
    return <div key={k}><dt>{labels[k]}</dt><dd><ul>{entries.map(e=><li key={e.id}>
      <button className="record-origin" onClick={()=>open(e.event_id)}>{e.text ?? (e.name ? e.name+(e.value != null ? " · "+showValue(e.value,e.unit):"") : e.language ? e.language+" · "+e.id : e.hash ?? e.id)}</button>{e.current === false && <small> · Previous decision version</small>}
    </li>)}</ul></dd></div>;
  })}</dl>{!!data.ambiguous_inputs?.length && <details><summary>Unresolved input versions · {data.ambiguous_inputs.length}</summary>
    <ul>{data.ambiguous_inputs.map((a,i)=><li key={i}>{a.path} · {a.run_id} · {a.candidate_versions.length} possible versions</li>)}</ul></details>}
    {!!data.unverified_bundles?.length && <p className="warning">Bundle contents could not be verified · {data.unverified_bundles.join(", ")}</p>}</>;
}

export function ImpactDialog({base,row,versions,close,repair,editable}:{
  base:string;row:MaterialVersion;versions:MaterialVersion[];close:()=>void;repair:(sid:string,draft:string)=>void;editable:boolean;
}) {
  const path = base+"/"+encodeURIComponent(row.id);
  const [revision,setRevision] = useState(0);
  const [record,setRecord] = useState<string>();
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");
  const current = row.status === "active";
  const usage = useProjectRead<Dependencies>(current ? path+"/dependencies":null,revision);
  const impact = useProjectRead<Impact>(current ? null:path+"/impact",revision);
  const running = impact.value?.operations.some(op=>op.status==="running");
  useEffect(()=>{
    if (!running || busy) return;
    const timer = setTimeout(()=>setRevision(r=>r+1),2500);
    return ()=>clearTimeout(timer);
  },[running,revision,busy]);
  const replacement=versions.find(v=>v.id===impact.value?.replacement_version_id);
  async function recompute() {
    const quote = impact.value;
    if (!quote?.can_recompute || !quote.replacement_version_id || busy) return;
    setBusy(true); setError("");
    try {
      await request(path+"/recompute",{replacement_version_id:quote.replacement_version_id});
      setRevision(r=>r+1);
    } catch(e) {setError(errorMessage(e));}
    finally {setBusy(false);}
  }
  const dependencies = usage.value ?? impact.value?.impact;
  const failure = error || usage.error || impact.error;
  return <Dialog id="material-impact" title={current ? "Material usage":"Material impact"} close={close} closeLabel="Close impact">
    <p>{row.name} · Version {row.version}</p>
    {replacement && <p>Replacement · {replacement.name}, version {replacement.version} · {replacement.source}</p>}
    {dependencies && <><h3>Downstream records</h3><UsedBy data={dependencies} open={setRecord}/></>}
    {!dependencies && !failure && <p role="status">Reading the research record…</p>}
    {impact.value && <>
      {impact.value.operations.map(op=><section className="correction-result" key={op.id}>
        <h3>Recomputation · {op.status}</h3>
        {op.reason && <p>{op.reason}</p>}
        <ComparisonTable comparisons={op.comparisons}/>
        {op.session_id && <button className="quiet" onClick={()=>repair(op.session_id!,op.repair_prompt ?? "")}>
          {op.repair_prompt ? "Discuss a repair":"Open recomputation session"}</button>}
      </section>)}
      <details key={impact.value.operations.length} open={!impact.value.operations.some(op=>op.status==="completed")}>
      <summary>Recomputation options</summary><p>Estimated model cost · USD {impact.value.estimated_model_usd.toFixed(2)}</p>
      <p className="muted">{impact.value.estimate_basis}</p>
      {impact.value.reason && <p>{impact.value.reason}</p>}
      {editable && impact.value.can_recompute && <button className="primary" disabled={busy}
        onClick={recompute}>{busy ? "Recomputing…":"Recompute with replacement"}</button>}
      </details>
    </>}
    {failure && <p role="alert" className="error">{failure} <button className="quiet" onClick={()=>{setError("");setRevision(r=>r+1);}}>Refresh impact</button></p>}
    {record && <SourceRecord base={base.slice(0,-"/materials".length)} id={record} close={()=>setRecord(undefined)}/>}
  </Dialog>;
}
