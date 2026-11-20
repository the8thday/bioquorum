import { useState, type FormEvent } from "react";
import { Dialog } from "./Dialog";
import { errorMessage, request } from "./api";

type Close = {close: () => void};

export function NameDialog({mode, initial, close, save}: Close & {
  mode: "fork" | "new" | "rename"; initial: string; save: (title: string) => Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const title = String(new FormData(event.currentTarget).get("title"));
    setPending(true);
    try { await save(title); }
    catch (e) { setError(errorMessage(e)); setPending(false); }
  }
  return <Dialog id="name-dialog" title={mode === "fork" ? "Fork session" : mode === "rename" ? "Rename session" : "New session"} close={close}>
    <form id="name-form" onSubmit={submit}>
      <label htmlFor="session-title">Session title</label>
      <input id="session-title" name="title" required maxLength={160} defaultValue={initial}/>
      {error && <p className="error" role="status">{error}</p>}
      <div className="actions"><button type="button" className="quiet" onClick={close}>Cancel</button>
        <button type="submit" className="primary" disabled={pending}>Continue</button></div>
    </form></Dialog>;
}

export interface Audit {
  classification: string; next_before: number | null;
  entries: {type: string; seq: number; time: string; actor: string; details: Record<string, unknown>}[];
}

export function AuditDialog({initial, path, close}: Close & {initial: Audit; path: string}) {
  const [page, setPage] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function older() {
    setBusy(true);
    try {
      const next = await request<Audit>(path + "?before=" + page.next_before);
      setPage({...next, entries: [...page.entries, ...next.entries]});
      setError("");
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return <Dialog id="audit-dialog" title="Audit log" closeLabel="Close audit log" close={close}>
    <p className="muted">Data classification · {page.classification}</p>
    <ol id="audit-entries">{page.entries.map(item => <li key={item.seq}>
      <strong>{item.type.replaceAll("_", " ")}</strong><div className="muted">
        {new Date(item.time).toLocaleString()} · {item.actor}</div>
      <div>{Object.entries(item.details).filter(([,v]) => v !== null)
        .map(([k,v]) => k.replaceAll("_", " ") + " " + v).join(" · ")}</div>
    </li>)}{!page.entries.length && <li>No audit events yet.</li>}</ol>
    {error && <p role="status" className="error">{error}</p>}
    {page.next_before !== null && <button className="quiet" disabled={busy} onClick={older}>Older events</button>}
  </Dialog>;
}

export interface Note {body: string; revision: string | null; drafts: {id: string; body: string}[]}

export function NoteDialog({initial, path, close, saved}: Close & {initial: Note; path: string; saved: () => void}) {
  const [note, setNote] = useState(initial);
  const [body, setBody] = useState(initial.body);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function reload() {
    setBusy(true);
    try {
      const latest = await request<Note>(path);
      setNote(latest); setBody(latest.body); setDraft(""); setError("");
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await request(path, {body, prior_revision: note.revision, draft_event_id: draft || null});
      saved();
    } catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return <Dialog id="note-dialog" title="Project note" close={close}>
    <form id="note-form" onSubmit={save}>
      <p className="muted">Saved notes are available to future sessions in this project.</p>
      {!!note.drafts.length && <><label htmlFor="note-draft">Drafts from this session</label>
        <select id="note-draft" value={draft} onChange={e => {
          const id = e.target.value;
          setDraft(id); setBody(note.drafts.find(d => d.id === id)?.body ?? note.body);
        }}><option value="">Start with the saved note</option>
          {note.drafts.map((d,i) => <option key={d.id} value={d.id}>Draft {i+1} · {d.body.slice(0,44)}</option>)}
        </select></>}
      <label htmlFor="note-body">Note</label><textarea id="note-body" rows={10} maxLength={6000}
        value={body} onChange={e => setBody(e.target.value)}/>
      <p role="status" className="muted">{error}</p><div className="actions">
        <button type="button" className="quiet" onClick={close}>Close</button>
        {error && <button type="button" className="quiet" disabled={busy} onClick={reload}>Reload saved note</button>}
        <button type="submit" className="primary" disabled={busy}>Save project note</button></div>
    </form></Dialog>;
}

export interface Estimate {
  isolated_execution_available: boolean; basis: string; estimated_usd: number;
  inputs: {path: string}[]; targets: {name: string; unit?: string}[];
}

export function ReplicationDialog({estimate, close, start}: Close & {estimate: Estimate; start: (body: unknown) => void}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    start({question: values.get("question"),
      definitions: Object.fromEntries(estimate.targets.map(t => [t.name, values.get("target." + t.name)])),
      tolerance: {absolute: Number(values.get("absolute")), relative: Number(values.get("relative"))},
      max_estimated_usd: Number(values.get("budget"))});
  }
  return <Dialog id="blind-dialog" title="Independent replication" close={close}>
    <form id="blind-form" onSubmit={submit}>
      <p>Estimated cost: USD {estimate.estimated_usd.toFixed(6)} · {estimate.basis}</p>
      <p className="muted">Enter a question and definitions without the original code or results.</p>
      <label htmlFor="blind-question">Question for a fresh model session</label>
      <textarea id="blind-question" name="question" required rows={3} maxLength={4000}/>
      {estimate.targets.map(t => <label key={t.name}>{t.name}{t.unit && " (" + t.unit + ")"}
        <input name={"target." + t.name} required maxLength={500}/></label>)}
      <p className="muted">Inputs: {estimate.inputs.map(i => i.path).join(", ") || "None"}</p>
      <label>Absolute tolerance<input type="number" name="absolute" min="0" step="any" defaultValue="0" required/></label>
      <label>Relative tolerance (0 to 1)<input type="number" name="relative" min="0" max="1" step="any" defaultValue="0" required/></label>
      <label>Estimated spend threshold (USD)<input type="number" name="budget" min="0.000001" max="100" step="any" required
        defaultValue={Math.max(.001, Math.ceil(estimate.estimated_usd * 1.25e6) / 1e6)}/></label>
      <div className="actions"><button className="quiet" type="button" onClick={close}>Cancel</button>
        <button className="primary">Approve and start replication</button></div>
    </form></Dialog>;
}
