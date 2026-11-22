import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import type { DraftReference, Project, Snapshot } from "./types";

import { Icon } from "./Icon";
import type { Product } from "./Artifacts";

export function Composer({draftPlan,initialDraft,executing,connection,upload,openSettings,chooseModel,attachments,setAttachments,refs, setRefs, literatureEnabled, planDefault, record, example, worker, setWorker, models, classification, preset, mutate, report}: {
  chooseModel?:()=>void;
  draftPlan:(q:string)=>Promise<{steps:string[];model:string}>;
  initialDraft?:string; executing: boolean; connection: string; upload: ReactNode; openSettings: () => void; attachments:Product[]; setAttachments:(p:Product[])=>void;
  refs: DraftReference[]; setRefs: (items: DraftReference[]) => void;
  literatureEnabled?: boolean; planDefault?: boolean; worker: string; setWorker: (value: string) => void;
  models: Project["worker_models"]; classification: string;
  record: Snapshot; example: Project["example"]; preset: number;
  mutate: (suffix: string, body: unknown) => Promise<void>; report: (error: unknown) => void;
}) {
  const [question, setQuestion] = useState(initialDraft ?? "");
  useEffect(()=>{if(initialDraft) setQuestion(initialDraft);},[initialDraft]);
  const [steps, setSteps] = useState("");
  const [literature, setLiterature] = useState(false);
  const [plan, setPlan] = useState(!!planDefault);
  useEffect(() => setPlan(!!planDefault), [planDefault]);
  const saved=record.plan_draft;
  const restored=useRef<string | undefined>(undefined);
  useEffect(()=>{
    if(saved && restored.current!==saved.question){
      restored.current=saved.question;
      if(!question.trim()) {setQuestion(saved.question);setSteps(saved.steps.join("\n"));setPlan(true);}
    }
  },[saved,question]);
  const [busy, setBusy] = useState(false);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const stepsRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (preset && example) {
      setQuestion(example.question); setSteps(example.steps.join("\n")); setPlan(true);
      questionRef.current?.focus();
    }
  }, [preset, example]);
  const running = record.turns.some(t => t.seq >= 0 && t.status === "running");
  const interrupted = record.turns.find(t => t.seq >= 0 && t.resume_checks !== undefined);
  const checks = Object.entries(interrupted?.resume_checks ?? {});
  const demoDone = !!example && !running && record.turns.some(t => t.status !== "declined" && t.status !== "queued");
  const disabled = busy || running || !!record.pending_plan || demoDone;
  const focused = useRef<DraftReference | undefined>(undefined);
  useEffect(() => {
    const newest = refs.at(-1);
    if (newest && newest !== focused.current && !disabled) questionRef.current?.focus();
    if (!disabled) focused.current = newest;
  }, [refs, disabled]);
  const proposal = record.turns.find(t => t.id === record.pending_plan);
  const planPath = "/turns/" + record.pending_plan + "/plan";
  async function act(action: () => Promise<void>) {
    setBusy(true);
    try { await action(); } catch (e) { report(e); }
    finally { setBusy(false); }
  }
  const notes = refs.flatMap((r,i) => r.note ? [refs.length === 1 && !question.trim()
    ? r.note : "Reference " + (i+1) + ": " + r.note] : []);
  const message = [question.trim(),...notes].filter(Boolean).join("\n\n");
  const lookup = literatureEnabled && !example && !plan && literature;
  function submit(event: FormEvent) {
    event.preventDefault();
    if (chooseModel) {chooseModel(); return;}
    if (!message || message.length > 12000) {report(new Error("Enter a question of up to 12,000 characters, including reference notes."));return;}
    const query = lookup ? message.replace(/^\/literature(?:\s+|$)/, "").trim() : message;
    if (lookup && (!query || [...query].length > 1200)) {
      report(new Error("Enter a literature question of 1 to 1,200 characters.")); return;
    }
    void act(async () => {
      await mutate("/turns", {question:lookup ? "/literature " + query : message, attachments:attachments.map(a=>a.id), references:refs.map(r=>r.item), worker_model: worker || null, steps: plan ? steps.split("\n").map(s => s.trim()).filter(Boolean) : null});
      setAttachments([]); setRefs([]); setQuestion(""); setLiterature(false);
    });
  }
  return <form id="composer" className={demoDone ? "finished-example" : undefined} onSubmit={submit}>
    <div id="controls">
      {interrupted && <article className="panel">
        <h2>Interrupted research</h2>
        {checks.length > 0 && <><p>Verify the outcome of these operations before continuing.</p>
          <ul>{checks.map(([id,tool])=><li key={id}>{tool} · {id}</li>)}</ul></>}
        <button type="button" className="quiet" disabled={busy || running}
          onClick={()=>act(()=>mutate("/turns/"+interrupted.id+"/resume",{checked_calls:checks.map(([id])=>id)}))}>
          {checks.length ? "I checked these operations — resume research" : "Resume research"}</button>
      </article>}
      {running && <article className="panel run-status"><p role="status" className={executing ? "running-indicator" : ""}>{record.permissions.length
        ? "Awaiting your permission" : executing ? "Research running" : connection || "Checking session status…"}</p>
        <button type="button" className="quiet" disabled={busy} onClick={() => act(() => mutate("/stop", {}))}>Stop research</button></article>}
      {record.pending_plan && <article className="panel"><p>Approve the recorded plan to start the analysis.</p>
        <div className="actions"><button type="button" className="primary" disabled={busy}
          onClick={() => act(() => mutate(planPath, {approve: true}))}>Approve and run</button>
          {proposal?.plan && !example && <button type="button" className="quiet" disabled={busy} onClick={() => act(async () => {
            await mutate(planPath, {approve: false, reason: "Researcher requested a revision"});
            setQuestion(proposal.question); setSteps(proposal.plan!.steps.join("\n")); setPlan(true);
            requestAnimationFrame(() => stepsRef.current?.focus());
          })}>Revise plan</button>}
          <button type="button" className="quiet" disabled={busy} onClick={() =>
            act(() => mutate(planPath, {approve: false, reason: "Declined by researcher"}))}>Decline plan</button></div>
      </article>}
      {record.permissions.map(p => <article className="panel" id={"record-"+p.request_id} key={p.request_id}><h2>Permission requested</h2>
        <p>{p.reason} · {p.permission}</p><p className="segment">{p.target}</p><div className="actions">
          {[true, false].map(grant => <button type="button" key={String(grant)} className={grant ? "primary" : "quiet"}
            disabled={busy} onClick={() => act(() => mutate("/permissions/" + p.request_id, {grant}))}>
            {grant ? "Allow once" : "Deny"}</button>)}</div></article>)}
    </div>
    {attachments.length > 0 && <ul className="file-tray" aria-label="Attached files">{attachments.map(a => <li key={a.id}><Icon name="paperclip"/><span>{a.name}</span>
      <button className="icon-button" title="Remove attachment" aria-label="Remove attachment" type="button" onClick={() => setAttachments(attachments.filter(p => p.id!==a.id))}>×</button></li>)}</ul>}
    {refs.length > 0 && <ul className="reference-tray" aria-label="References">{refs.map((r,i) => <li key={i}>
      <span className="reference-kind">{r.item.kind}</span><div className="reference-copy"><span className="reference-label" title={r.label}>{r.label}</span>{r.note && <span className="reference-note">{r.note}</span>}</div>
      <button type="button" className="icon-button" aria-label="Remove reference" onClick={() => setRefs(refs.filter((_,j) => j !== i))}>×</button></li>)}</ul>}
    <label htmlFor="question">Research question</label>
    <textarea id="question" ref={questionRef} rows={2} maxLength={12000} required={!notes.length} disabled={disabled} value={question}
      onChange={e => setQuestion(e.target.value)}
      placeholder={demoDone ? "Start a new session to run the example again." : "What would you like to investigate?"}/>
    <div className="composer-footer">{upload}<button type="button" className="quiet chat-settings" onClick={openSettings} title="Common project settings"><Icon name="settings"/>Chat settings</button><label className="checkbox">
      <input type="checkbox" checked={plan} disabled={disabled || !!lookup} onChange={e => setPlan(e.target.checked)}/> Review a plan first</label>
      {literatureEnabled && !example && <label className="checkbox">
        <input type="checkbox" checked={!!lookup} disabled={disabled || plan} onChange={e=>setLiterature(e.target.checked)}/> Dedicated literature lookup</label>}
      {models.length > 0 && <label className="model-picker"><span className="sr-only">Worker model</span><select title="Worker model" value={worker} disabled={disabled} onChange={e => setWorker(e.target.value)}>
        <option value="">Role default</option>{models.map(m => <option key={m.spec} value={m.spec} disabled={!m.allowed}>
          {m.spec}{!m.allowed && " · unavailable for " + classification + " data"}</option>)}</select></label>}
      <button type="submit" className="primary" disabled={disabled}>{plan ? "Submit plan" : "Run research"}</button></div>
    {plan && <div id="plan-editor">
      {!example && <button type="button" className="quiet" disabled={disabled || !question.trim()} onClick={()=>act(async()=>{
        const draft=await draftPlan(question);
        setSteps(draft.steps.join("\n"));
      })}>Draft steps with planner</button>}
      <label htmlFor="steps">Analysis plan <span className="muted">One step per line</span></label>
      <textarea id="steps" ref={stepsRef} rows={3} maxLength={7320} value={steps} disabled={disabled} onChange={e => setSteps(e.target.value)}
        placeholder={"Identify the evidence needed\nCompare the findings and limits"}/></div>}
  </form>;
}
