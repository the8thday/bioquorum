import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { errorMessage, request } from "./api";
import type { Project } from "./types";

export function SessionSearch({project, selected, open, rename}: {
  project: Project; selected: string; open: (id: string) => void; rename: (id:string, title:string)=>void;
}) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<{query: string; rows: Project["sessions"]} | null>(null);
  const [error, setError] = useState("");
  const term = query.trim();
  useEffect(() => {
    setError("");
    if (!term) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      request<Project>("/api/sessions?q=" + encodeURIComponent(term), undefined, controller.signal)
        .then(data => setResult({query: term, rows: data.sessions}))
        .catch(e => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    }, 120);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [term, project]);
  const rows = term ? (result?.query === term ? result.rows : null) : project.sessions;
  return <>
    <label className="session-search"><Icon name="search"/><span className="sr-only">Search sessions</span>
      <input type="search" value={query} placeholder="Search sessions" onChange={e => setQuery(e.target.value)}/>
    </label>
    <nav id="sessions" aria-label="Sessions">{rows?.map(s => <div className="session-entry" key={s.id}><button className="quiet"
      aria-current={s.id === selected ? "page" : undefined} onClick={() => open(s.id)}><Icon name="chat"/><span>{s.title}</span></button>
      <button className="session-rename" aria-label={"Rename "+s.title} title="Rename session" onClick={()=>rename(s.id,s.title)}>
        <Icon name="edit"/></button></div>)}</nav>
    <p className="empty-note" role="status">{error || (term ? rows === null ? "Searching…" :
      rows.length ? rows.length + " matching session" + (rows.length === 1 ? "" : "s") : "No matching sessions."
      : !project.sessions.length && "No sessions in this project yet.")}</p>
  </>;
}
