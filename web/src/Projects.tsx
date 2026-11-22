import { useEffect, useImperativeHandle, useState, type FormEvent, type Ref } from "react";
import { Dialog } from "./Dialog";
import { errorMessage, request } from "./api";
import { Icon } from "./Icon";

type Entry = {name: string; classification: string; href: string; key?: string; created?: string; sessions?: number};
type View = "create" | "manage" | "trash" | "confirm";
const projectList = "/api/projects";

function details(project: Entry) {
  if (!project.key) return "Workbench startup directory";
  const date = project.created ? new Date(project.created).toLocaleString() : "";
  const sessions = project.sessions === undefined ? "" : project.sessions === 1 ? "1 session" : project.sessions + " sessions";
  return [date, sessions, project.key.slice(-8)].filter(Boolean).join(" · ");
}

export function Projects({name, settings, actions}: {name: string; settings: () => void; actions:Ref<{remove:()=>void}>}) {
  const [list, setList] = useState<Entry[]>([]);
  const [trash, setTrash] = useState<Entry[]>([]);
  const [view, setView] = useState<View>();
  const [target, setTarget] = useState<Entry>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const c = new AbortController();
    request<Entry[]>(projectList, undefined, c.signal).then(setList)
      .catch(e => { if (!c.signal.aborted) setError(errorMessage(e)); });
    return () => c.abort();
  }, [name]);
  const current = list.find(p => location.pathname === p.href);
  async function remove() {
    if (!current?.key) {await manage("manage");return;}
    setError("");setBusy(true);
    try {
      const projects = await request<Entry[]>(projectList);
      setList(projects);
      const selected = projects.find(p=>p.key===current.key);
      if (!selected) throw new Error("This project is no longer in the library.");
      setTarget(selected);setView("confirm");
    } catch(e) {setError(errorMessage(e));} finally {setBusy(false);}
  }
  useImperativeHandle(actions, ()=>({remove}));
  function close() { if (!busy) {setView(undefined); setError("");} }
  async function manage(next: "manage" | "trash") {
    setView(next); setError(""); setBusy(true);
    try {
      if (next === "manage") setList(await request<Entry[]>(projectList));
      else setTrash(await request<Entry[]>(projectList + "/trash"));
    } catch (e) {setError(errorMessage(e));} finally {setBusy(false);}
  }
  async function move(project: Entry, restore: boolean) {
    setError(""); setBusy(true);
    try {
      await request(projectList + "/" + project.key + (restore ? "/restore" : "/trash"), {});
      if (!restore && project.href === location.pathname) {location.assign("/"); return;}
      setList(await request<Entry[]>(projectList));
      if (restore) setTrash(await request<Entry[]>(projectList + "/trash"));
      setView(restore ? "trash" : "manage");
    } catch (e) {setError(errorMessage(e));} finally {setBusy(false);}
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      const created = await request<Entry>(projectList, Object.fromEntries(form));
      location.assign(created.href);
    } catch (e) { setError(errorMessage(e)); setBusy(false); }
  }
  return <div className="project-switch">
    <label className="project-label" htmlFor="current-project">Project</label>
    <div className="project-picker"><div className="switch-field"><Icon name="Artifacts"/><select id="current-project" className="project-name" value={current?.href ?? ""}
      onChange={e => location.assign(e.target.value)}>{!current && <option value="">{name}</option>}
      {list.map(p => <option key={p.href} value={p.href}>{p.name}{list.some(other=>other.href!==p.href && other.name===p.name) && " · " + (p.key?.slice(-8) ?? "Startup")}</option>)}</select><Icon name="chevron"/></div>
    </div>
    <div className="project-actions">
      <button className="rail-action" onClick={()=>manage("manage")}>Manage projects</button>
      <button id="new-project" className="rail-action" onClick={() => {setError(""); setView("create");}}><Icon name="add"/>New project</button>
    </div>
    {error && !view && <p className="error" role="alert">{error}</p>}
    {view === "create" && <Dialog id="create-project" title="New project" closeLabel="Close new project" close={close}>
      <form className="project-form" onSubmit={create}>
        <label>Project name<input name="name" autoFocus required maxLength={160} placeholder="e.g. Target validation"/></label>
        <label>Project instructions <small>(optional)</small><textarea name="instructions" maxLength={6000} rows={3} placeholder="What should every research session in this project take into account?"/></label>
        <label>Data classification<select name="classification" defaultValue="internal">
          <option value="internal">Internal</option><option value="public">Public</option><option value="confidential">Confidential</option>
        </select></label>
        <p className="muted">Choose Public to search public literature and trial databases. Internal and Confidential projects keep those queries off public services.</p>
        {error && <p role="alert" className="error">{error}</p>}
        <div className="actions"><button className="quiet" type="button" onClick={close}>Cancel</button>
          <button className="primary" disabled={busy}>Create project</button></div>
      </form></Dialog>}
    {(view === "manage" || view === "trash") && <Dialog id="manage-projects" title={view === "trash" ? "Project trash" : "Manage projects"} closeLabel="Close project manager" close={close}>
      <div className="actions"><button className="quiet" disabled={busy} onClick={()=>manage(view==="manage" ? "trash":"manage")}>{view==="manage" ? "View trash":"Back to projects"}</button></div>
      {busy && <p role="status">Loading projects…</p>}
      {view === "trash" && !busy && !trash.length && <p>Trash is empty.</p>}
      {(view === "trash" ? trash : list).map(p=><section className="library-entry" key={p.href}>
        <strong>{p.name}{current?.href === p.href && " (current)"}</strong><small>{p.classification} · {details(p)}</small>{!p.key && <small>The startup directory stays on disk. Projects created in the workbench can be moved to trash here.</small>}
        <div className="actions">{view === "trash" ? <button className="quiet" disabled={busy} onClick={()=>move(p,true)}>Restore</button> : <>
          {p.href === current?.href ? <button className="quiet" onClick={()=>{close(); settings();}}>Edit project</button> : <a href={p.href}>Open project</a>}
          {p.key && <button className="quiet danger" disabled={busy} onClick={()=>{setTarget(p);setError("");setView("confirm");}}>Delete project…</button>}</>}</div>
      </section>)}
      {error && <p role="alert">{error}</p>}</Dialog>}
    {view === "confirm" && target && <Dialog id="delete-project" title="Delete project?" closeLabel="Cancel deletion" close={close}>
      <p><strong>{target.name}</strong></p><p>{details(target)}</p>
      <p>The project, its sessions and files will move to Project trash. You can restore them there.</p>
      {error && <p role="alert">{error}</p>}
      <div className="actions"><button className="quiet" autoFocus disabled={busy} onClick={()=>{setError("");setView("manage");}}>Cancel</button>
        <button className="primary destructive" disabled={busy} onClick={()=>move(target,false)}>Move to trash</button></div></Dialog>}
  </div>;
}
