import { useEffect, useState, type FormEvent } from "react";
import { Dialog } from "./Dialog";
import { errorMessage, request } from "./api";
import { Icon } from "./Icon";

type Entry = {name: string; classification: string; href: string};

export function Projects({name}: {name: string}) {
  const [list, setList] = useState<Entry[]>([]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const c = new AbortController();
    request<Entry[]>("/api/projects", undefined, c.signal).then(setList)
      .catch(e => { if (!c.signal.aborted) setError(errorMessage(e)); });
    return () => c.abort();
  }, []);
  const current = list.find(p => location.pathname === p.href);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      const created = await request<Entry>("/api/projects", {name: form.get("name"), classification: form.get("classification")});
      location.assign(created.href);
    } catch (e) { setError(errorMessage(e)); setBusy(false); }
  }
  return <div className="project-switch">
    <label className="project-label" htmlFor="current-project">Project</label>
    <div className="switch-field"><Icon name="Artifacts"/><select id="current-project" className="project-name" value={current?.href ?? ""}
      onChange={e => location.assign(e.target.value)}>{!current && <option value="">{name}</option>}
      {list.map(p => <option key={p.href} value={p.href}>{p.name}</option>)}</select><Icon name="chevron"/></div>
    <button id="new-project" className="rail-action" onClick={() => {setError(""); setCreating(true);}}><Icon name="add"/>New project</button>
    {error && !creating && <p className="error" role="alert">{error}</p>}
    {creating && <Dialog id="create-project" title="New project" closeLabel="Close new project" close={() => setCreating(false)}>
      <form className="project-form" onSubmit={create}>
        <label>Project name<input name="name" autoFocus required maxLength={160} placeholder="e.g. Target validation"/></label>
        <label>Data classification<select name="classification" defaultValue="internal">
          <option value="internal">Internal</option><option value="public">Public</option><option value="confidential">Confidential</option>
        </select></label>
        <p className="muted">Classification determines which model providers and tools can receive project data.</p>
        {error && <p role="alert" className="error">{error}</p>}
        <div className="actions"><button className="quiet" type="button" onClick={() => setCreating(false)}>Cancel</button>
          <button className="primary" disabled={busy}>Create project</button></div>
      </form></Dialog>}
  </div>;
}
