import { useState, type FormEvent } from "react";
import { Dialog } from "./Dialog";
import { errorMessage, request, sessionPath } from "./api";
import { useProjectRead } from "./useProjectRead";
import { NameDialog } from "./SessionDialogs";
import type { Snapshot } from "./types";
import type { MaterialVersion } from "./Materials";

type Claim = {id:string; session_id:string; source_event_id:string; event_id?:string; revision?:string; text:string; status:string};
type DecisionVersion = {revision:string; time?:string; text:string; claims:Claim[]};
export type Decision = DecisionVersion & {id:string; needs_review:boolean; history:DecisionVersion[]};
type LineSession = {id:string; title:string; event_id?:string};
type Handoff = {
  name:string; question:string; event_id:string; status:string; sessions:LineSession[]; claims:Claim[];
  materials:MaterialVersion[]; decisions:Decision[]; gaps:{text:string;event_id:string;session_id:string}[];
  pending:{id:string;event_id:string;session_id:string;text?:string;claim_id?:string}[];
};
type ViewRecord = (id:string)=>void;

export function DecisionEntry({decision, showRecord, revise}:{decision:Decision;showRecord:ViewRecord;revise?:()=>void}) {
  return <article aria-label="Research decision" className="program-decision">
    {decision.time && <time className="muted" dateTime={decision.time}>{new Date(decision.time).toLocaleString()}</time>}
    <p><strong>{decision.text}</strong> {decision.needs_review && <span className="review-notice">Needs review</span>}</p>
    <ul aria-label="Fixed claim versions">{decision.claims.map(c=><li key={c.id}>
      <button className="record-origin" onClick={()=>showRecord(c.revision ?? c.source_event_id)}>{c.text}</button> <small>{c.status}</small>
    </li>)}</ul>
    <div className="material-actions"><button className="quiet" onClick={()=>showRecord(decision.revision)}>View decision record</button>
      {revise && <button className="quiet" onClick={revise}>Revise decision</button>}</div>
    {!!decision.history?.filter(v=>v.revision!==decision.revision).length && <details><summary>Previous decision versions</summary>
      <ul>{decision.history.filter(v=>v.revision!==decision.revision).map(v=><li key={v.revision}>
        <button className="record-origin" onClick={()=>showRecord(v.revision)}>{v.text}</button>{v.time && <small> · {new Date(v.time).toLocaleString()}</small>}</li>)}</ul></details>}
  </article>;
}

function DecisionEditor({base,handoff,prior,close,saved}:{
  base:string;handoff:Handoff;prior?:Decision;close:()=>void;saved:()=>void;
}) {
  const [text,setText] = useState(prior?.text ?? "");
  const [basis,setBasis] = useState(handoff.claims);
  const [ids,setIds] = useState<string[]>(prior?.claims.filter(c=>handoff.claims.some(current=>current.id===c.id)).map(c=>c.id) ?? []);
  const [source,setSource] = useState("");
  const [draftId,setDraftId] = useState("");
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  const answers = useProjectRead<Snapshot>(source ? sessionPath(source):null);
  const records = answers.value?.answers.map(a=>({id:a.id,text:a.paragraphs.map(p=>p.segments.map(s=>s.text).join("")).join("\n")})) ?? [];
  async function refreshBasis() {
    setBusy(true);
    try {
      const current=await request<Handoff>(base+"/handoff");
      setIds(selected=>selected.filter(id=>current.claims.some(c=>c.id===id && c.revision===basis.find(before=>before.id===id)?.revision)));
      setBasis(current.claims);setError("");
    } catch(e) {setError(errorMessage(e));} finally {setBusy(false);}
  }
  async function save(event:FormEvent) {
    event.preventDefault();
    if (!text.trim() || !ids.length) {setError("Enter a decision and select its supporting claims.");return;}
    setBusy(true);setError("");
    try {
      const path = base+"/decisions"+(prior ? "/"+encodeURIComponent(prior.id)+"/revise":"");
      const versions=Object.fromEntries(basis.filter(c=>ids.includes(c.id)).map(c=>[c.id,c.revision]));
      await request(path,{text:text.trim(),claim_ids:ids,claim_revisions:versions,...(prior ? {prior_revision:prior.revision} : {})});
      saved();
    } catch(e) {setError(errorMessage(e));setBusy(false);}
  }
  return <Dialog id="decision-editor" title={prior ? "Revise decision":"Record decision"} close={close} closeLabel="Close decision editor">
    <form className="project-form" onSubmit={save}><fieldset disabled={busy}>
      <details><summary>Use a session answer as a draft</summary>
        <label htmlFor="draft-session">Session</label><select id="draft-session" value={source} onChange={e=>{setSource(e.target.value);setDraftId("");}}>
          <option value="">Choose a session</option>{handoff.sessions.map(s=><option key={s.id} value={s.id}>{s.title}</option>)}</select>
        {source && <><label htmlFor="draft-answer">Recorded answer</label><select id="draft-answer" value={draftId} onChange={e=>setDraftId(e.target.value)}>
          <option value="">Choose an answer</option>{records.map((r,i)=><option value={r.id} key={r.id}>Answer {i+1} · {r.text.slice(0,70)}</option>)}</select>
          <button type="button" className="quiet" disabled={!draftId} onClick={()=>setText(records.find(r=>r.id===draftId)?.text ?? text)}>Use answer as draft</button></>}
        {answers.error && <p role="alert">{answers.error}</p>}
      </details>
      <label>Decision text<textarea required maxLength={4000} rows={4} value={text} onChange={e=>setText(e.target.value)}/></label>
      <fieldset className="decision-basis"><legend>Claims supporting this decision</legend>
        {basis.map(c=><label key={c.id}><input type="checkbox" checked={ids.includes(c.id)}
          onChange={e=>setIds(old=>e.target.checked ? [...old,c.id] : old.filter(i=>i!==c.id))}/><span>{c.text} · {c.status}</span></label>)}
      </fieldset>
      {error && <p role="alert" className="error">{error} <button className="quiet" type="button" onClick={refreshBasis}>Refresh supporting claims</button></p>}
      <div className="actions"><button type="button" className="quiet" onClick={close}>Cancel</button>
        <button className="primary">Save decision</button></div>
    </fieldset></form>
  </Dialog>;
}

export function SourceRecord({base,id,close}:{base:string;id:string;close:()=>void}) {
  const result=useProjectRead<{id:string;type:string;session_id:string|null;details:Record<string,unknown>}>(base+"/records/"+encodeURIComponent(id));
  return <Dialog id="program-origin" title="Source record" closeLabel="Close source record" close={close}>
    {result.error ? <p role="alert">{result.error}</p> : result.value ? <>
      <h3>{result.value.type.replaceAll("_"," ")}</h3><p className="muted">{result.value.id}</p>
      <dl className="source-details">{Object.entries(result.value.details).map(([k,v])=><div key={k}>
        <dt>{k.replaceAll("_"," ")}</dt><dd>{typeof v === "string" ? v : <pre>{JSON.stringify(v,null,2)}</pre>}</dd>
      </div>)}</dl>
      {result.value.session_id && <a href={"?session="+encodeURIComponent(result.value.session_id)}>Open session</a>}
    </> : <p role="status">Loading record…</p>}
  </Dialog>;
}

export function ProgramOverview({base,openSession}:{base:string;openSession:(sid:string,draft?:string)=>void}) {
  const [revision,setRevision] = useState(0);
  const result = useProjectRead<Handoff>(base+"/handoff",revision);
  const [entry,setEntry] = useState<string>();
  const [edit,setEdit] = useState<Decision | "new">();
  const [newSession,setNewSession] = useState(false);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");
  const data = result.value;
  async function bundle() {
    setBusy(true);setError("");
    try {
      const res = await fetch(base+"/bundle",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
      if (!res.ok) throw new Error((await res.json().catch(()=>({}))).detail || "Bundle export failed");
      const url = URL.createObjectURL(await res.blob());
      const link = document.createElement("a");
      link.href=url;link.download="research-line.zip";link.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
    } catch(e) {setError(errorMessage(e));} finally {setBusy(false);}
  }
  if (!data) return result.error ? <p role="alert">{result.error} <button className="quiet" onClick={()=>setRevision(v=>v+1)}>Retry</button></p> : <p role="status">Loading handoff…</p>;
  const editable = data.status !== "closed";
  const affected = new Set(data.pending.map(p=>p.claim_id ?? p.id));
  return <section className="program-overview" aria-label="Handoff summary">
    <h2><button className="record-origin question-origin" onClick={()=>setEntry(data.event_id)}>{data.question}</button></h2>
    <div className="material-actions"><a href={base+"/handoff/file"} download>Download handoff summary</a>
      <button className="quiet" disabled={busy} onClick={bundle}>Download research bundle</button></div>
    {error && <p role="alert">{error}</p>}
    <section><h3>Current claims</h3>
      {!data.claims.length && <p className="muted">No current claims.</p>}
      <ul>{data.claims.map(c=><li key={c.id}><button className="record-origin" onClick={()=>setEntry(c.event_id ?? c.source_event_id)}>{c.text}</button>
        {" "}<small>{c.status}</small> {affected.has(c.id) && <span className="review-notice">Needs review</span>}</li>)}</ul>
    </section>
    <section><h3>Pending review</h3>
      {!data.pending.length && <p className="muted">No pending items.</p>}
      <ul>{data.pending.map(p=><li key={p.event_id}><button className="record-origin" onClick={()=>setEntry(p.event_id)}>
        {data.claims.find(c=>c.id===(p.claim_id ?? p.id))?.text ?? p.text ?? p.claim_id ?? p.id}</button></li>)}</ul>
    </section>
    <section><h3>Coverage gaps</h3>{!data.gaps.length && <p className="muted">No gaps recorded.</p>}
      <ul>{data.gaps.map((g,i)=><li key={g.event_id+"-"+i}><button className="record-origin" onClick={()=>setEntry(g.event_id)}>{g.text}</button></li>)}</ul>
    </section>
    <section><div className="material-heading"><h3>Decisions</h3>{editable && <button className="primary" disabled={!data.claims.length}
      onClick={()=>setEdit("new")}>Record decision</button>}</div>
      {!data.decisions.length && <p className="muted">No decisions recorded.</p>}
      {data.decisions.map(d=><DecisionEntry key={d.id} decision={d} showRecord={setEntry} revise={editable ? ()=>setEdit(d):undefined}/>)}
    </section>
    <section><h3>Materials</h3>{!data.materials.length && <p className="muted">No materials registered.</p>}
      <ul>{data.materials.map(m=><li key={m.id}><button className="record-origin" onClick={()=>setEntry(m.id)}>{m.name} · v{m.version}</button> <small>{m.status}</small></li>)}</ul>
    </section>
    <section><div className="material-heading"><h3>Sessions</h3>{editable && <button className="quiet" onClick={()=>setNewSession(true)}>New session in research line</button>}</div>
      {!data.sessions.length && <p className="muted">No sessions attached.</p>}
      <ul>{data.sessions.map(s=><li key={s.id}><a href={"?session="+encodeURIComponent(s.id)}>{s.title}</a></li>)}</ul>
    </section>
    {edit && <DecisionEditor base={base} handoff={data} prior={edit==="new"?undefined:edit} close={()=>setEdit(undefined)}
      saved={()=>{setEdit(undefined);setRevision(r=>r+1);}}/>}
    {entry && <SourceRecord base={base} id={entry} close={()=>setEntry(undefined)}/>}
    {newSession && <NameDialog mode="new" initial="" close={()=>setNewSession(false)} save={async title=>{
      const row=await request<{id:string}>(base+"/sessions/new",{title});
      setNewSession(false);openSession(row.id);
    }}/>}
  </section>;
}
