import { useEffect, useState, type FormEvent } from "react";
import { apiPath, errorMessage, request, sessionPath } from "./api";
import { uploadFile, type Product } from "./Artifacts";
import { Dialog } from "./Dialog";
import { ImpactDialog } from "./MaterialImpact";
import { ProgramOverview } from "./ProgramOverview";
import { useProjectRead as useRead } from "./useProjectRead";
import type { Project } from "./types";

const kinds = {
  experimental_data: "Experimental data",
  vendor_delivery: "Vendor delivery",
  literature: "Literature",
  other: "Other",
};
type Program = {id: string; name: string; status: string};
export type MaterialVersion = {
  id: string; material_id: string; version: number; name: string; hash: string; kind: keyof typeof kinds;
  source: string; status: "active" | "superseded" | "withdrawn"; reason: string | null;
  withdrawal_reason: string | null; registered_at: string; registered_by: string; file_id: string;
};
type Edit = {action:"replace" | "withdraw"; row:MaterialVersion} | {action:"register"};
type Props = {project: Project; currentSession: string; newSession: () => void};
type OpenSession = (sid:string, draft?:string)=>void;

export function MaterialHistory({versions, base, editable, onEdit, inspect}: {
  versions: MaterialVersion[]; base: string; editable: boolean; onEdit: (edit: Edit) => void; inspect?:(row:MaterialVersion)=>void;
}) {
  const groups = new Map<string, MaterialVersion[]>();
  for (const row of versions) {
    const group = groups.get(row.material_id) ?? [];
    group.push(row);
    groups.set(row.material_id, group);
  }
  if (!versions.length) return <p className="empty-note">No materials registered in this research line.</p>;
  return <div className="material-history">{Array.from(groups, ([id, group]) => {
    group.sort((a,b) => b.version - a.version);
    return <section className="material-group" key={id}>
      <h3>{group[0].name}</h3>
      {group.map(row => <article className="material-version" id={"material-"+row.id} key={row.id} aria-label={row.name+", version "+row.version}>
        <div className="material-version-title"><strong>Version {row.version}</strong>
          <span className="material-status">{({active:"Active", superseded:"Superseded", withdrawn:"Withdrawn"})[row.status]}</span></div>
        <p className="muted">{kinds[row.kind]} · {row.name}</p>
        <dl>
          <dt>Source</dt><dd>{row.source}</dd>
          <dt>Registered</dt><dd><time dateTime={row.registered_at}>{new Date(row.registered_at).toLocaleString()}</time> · {row.registered_by}</dd>
          {row.reason && <><dt>Replacement reason</dt><dd>{row.reason}</dd></>}
          {row.withdrawal_reason && <><dt>Withdrawal reason</dt><dd>{row.withdrawal_reason}</dd></>}
        </dl>
        <details><summary>File hash</summary><code>{row.hash}</code></details>
        <div className="material-actions"><a href={base+"/"+encodeURIComponent(row.id)+"/file"} download>Download file</a>
          {inspect && <button className="quiet" onClick={()=>inspect(row)}>{row.status === "active" ? "View usage":"View impact"}</button>}
          {editable && row.status === "active" && <>
            <button className="quiet" onClick={() => onEdit({action:"replace",row})}>Replace version</button>
            <button className="quiet" onClick={() => onEdit({action:"withdraw",row})}>Withdraw version</button>
          </>}
        </div>
      </article>)}
    </section>;
  })}</div>;
}

type LineEdit = {action:"create"} | {action:"rename" | "close"; program:Program};

function LineEditor({edit,saved,close}: {edit:LineEdit; saved:(id:string)=>void;close:()=>void}) {
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  const closing = edit.action === "close";
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name") ?? "").trim();
    if (!closing && !name) { setError("Enter a research line name."); return; }
    const path = edit.action === "create" ? "/api/programs"
      : "/api/programs/"+encodeURIComponent(edit.program.id)+"/"+edit.action;
    setBusy(true); setError("");
    try { const row = await request<Program>(path, closing ? {} : {name}); saved(row.id); }
    catch (e) { setError(errorMessage(e)); setBusy(false); }
  }
  const title = {create:"New research line",rename:"Rename research line",close:"Close research line"}[edit.action];
  return <Dialog id="edit-line" title={title} close={close} closeLabel="Dismiss research line editor">
    <form className="project-form" onSubmit={submit}>
      {edit.action === "close" ? <p>Close {edit.program.name}? Materials and session records will remain available to read.</p> :
        <label>Research line name<input name="name" required maxLength={160} defaultValue={edit.action === "rename" ? edit.program.name : ""}/></label>}
      {error && <p role="alert">{error}</p>}
      <div className="actions"><button type="button" className="quiet" onClick={close}>Cancel</button>
        <button className="primary" disabled={busy}>{({create:"Create research line",rename:"Save name",close:"Confirm close"})[edit.action]}</button></div>
    </form>
  </Dialog>;
}

function MaterialEditor({edit,base,project,currentSession,close,saved}: Props & {
  edit:Edit; base:string; close:()=>void; saved:()=>void;
}) {
  const [session,setSession] = useState(currentSession || project.sessions[0]?.id || "");
  const [existing,setExisting] = useState("");
  const [file,setFile] = useState<File>();
  const [uploaded,setUploaded] = useState("");
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  const withdrawing = edit.action === "withdraw";
  const [loadUploads,setLoadUploads] = useState(false);
  const [uploads,setUploads] = useState<Product[]>([]);
  const [readError,setReadError] = useState("");
  useEffect(() => {
    setUploads([]); setReadError("");
    if (!loadUploads || !session) return;
    const controller = new AbortController();
    request<Product[]>(sessionPath(session)+"/artifacts", undefined, controller.signal)
      .then(data => {if (!controller.signal.aborted) setUploads(data.filter(p => p.source === "user_upload"));})
      .catch(e => {if (!controller.signal.aborted) setReadError(errorMessage(e));});
    return () => controller.abort();
  }, [session,loadUploads]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const values = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      const reason = String(values.get("reason") ?? "").trim();
      if (edit.action !== "register" && !reason) throw new Error("Enter a reason.");
      if (edit.action === "withdraw") {
        await request(base+"/"+encodeURIComponent(edit.row.id)+"/withdraw",{reason});
      } else {
        const source = String(values.get("source") ?? "").trim();
        if (!source) throw new Error("Enter a source description.");
        let fileId = loadUploads ? existing : uploaded;
        if (!fileId) {
          if (!file || loadUploads || !session) throw new Error("Choose a file to register.");
          fileId = (await uploadFile(sessionPath(session),file,project.classification)).id;
          setUploaded(fileId);
        }
        if (edit.action === "register") await request(base,{file_id:fileId,kind:values.get("kind"),source});
        else await request(base+"/"+encodeURIComponent(edit.row.id)+"/supersede",{file_id:fileId,source,reason});
      }
      saved();
    } catch (e) { setError(errorMessage(e)); setBusy(false); }
  }
  const title = {register:"Register material",replace:"Replace version",withdraw:"Withdraw version"}[edit.action];
  return <Dialog id="material-editor" title={title} closeLabel="Close material editor" close={close}>
    <form className="project-form" onSubmit={submit}><fieldset disabled={busy}>
      {edit.action !== "register" && <p>{edit.row.name} · Version {edit.row.version}</p>}
      {!withdrawing && <>
        <label><span id="material-session-label">File session</span><select aria-labelledby="material-session-label" value={session} onChange={e => {
          setSession(e.target.value);setExisting("");setUploaded("");
        }}>{project.sessions.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}</select></label>
        <label><span id="material-source-label">File source</span><select aria-labelledby="material-source-label" value={loadUploads ? "existing":"new"} onChange={e => {
          setLoadUploads(e.target.value==="existing");setExisting("");setFile(undefined);setUploaded("");
        }}><option value="new">Upload a file</option><option value="existing">Use an existing upload</option></select></label>
        {loadUploads ? <label><span id="material-upload-label">Existing upload</span><select aria-labelledby="material-upload-label" required value={existing} onChange={e=>setExisting(e.target.value)}>
          <option value="">Choose an uploaded file</option>
          {uploads.map(p=><option key={p.id} value={p.id}>{p.name} · v{p.version}</option>)}
        </select></label> : <label>Material file<input type="file" required
          onChange={e=>{setFile(e.target.files?.[0]);setUploaded("");}}/></label>}
        {readError && <p role="alert">{readError}</p>}
        {edit.action === "register" && <label><span id="material-kind-label">Material type</span><select aria-labelledby="material-kind-label" name="kind" defaultValue="experimental_data">
          {Object.entries(kinds).map(([value,label])=><option key={value} value={value}>{label}</option>)}
        </select></label>}
        <label>Source description<input name="source" required maxLength={1200}/></label>
      </>}
      {edit.action !== "register" && <label>{withdrawing ? "Withdrawal reason":"Replacement reason"}
        <textarea rows={2} name="reason" required maxLength={1200}/></label>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="actions"><button type="button" className="quiet" onClick={close}>Cancel</button>
        <button className="primary">{({register:"Save material",replace:"Save replacement",withdraw:"Confirm withdrawal"})[edit.action]}</button></div>
    </fieldset></form>
  </Dialog>;
}

function LineMaterials({program,openSession,...props}: Props & {program:Program;openSession:OpenSession}) {
  const [revision,setRevision] = useState(0);
  const [edit,setEdit] = useState<Edit | null>(null);
  const [inspected,setInspected] = useState<MaterialVersion>();
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  const base = apiPath("/api/programs/"+encodeURIComponent(program.id));
  const path = base+"/materials";
  const details = useRead<Program & {sessions:{id:string}[]}>(base, revision);
  const history = useRead<{versions:MaterialVersion[]}>(path, revision);
  const editable = details.value && details.value.status !== "closed";
  const attached = details.value?.sessions.some(s => s.id === props.currentSession);
  async function attach() {
    setBusy(true); setError("");
    try {await request(base+"/sessions",{session_id:props.currentSession});setRevision(v=>v+1);}
    catch (e) {setError(errorMessage(e));}
    finally {setBusy(false);}
  }
  const failure = details.error || history.error || error;
  useEffect(()=>{
    if (history.value && location.hash.startsWith("#material-")) document.getElementById(location.hash.slice(1))?.scrollIntoView();
  },[history.value]);
  return <>
    {attached ? <p className="muted">Current session is in this research line.</p> :
      editable && props.currentSession && <button className="quiet" disabled={busy} onClick={attach}>Add current session</button>}
    {failure && <p role="alert" className="error">{failure} <button className="quiet" onClick={() => {setError("");setRevision(v=>v+1);}}>Refresh</button></p>}
    <div className="material-heading"><h2>Registered materials</h2>
      {editable && (props.project.sessions.length ?
        <button className="primary" onClick={()=>setEdit({action:"register"})}>Register material</button> :
        <button className="quiet" onClick={props.newSession}>Create a session for uploads</button>)}
    </div>
    {history.value ? <MaterialHistory versions={history.value.versions} base={path}
      editable={!!editable} onEdit={setEdit} inspect={setInspected}/> : !failure && <p role="status">Loading materials…</p>}
    {inspected && <ImpactDialog row={inspected} versions={history.value?.versions ?? []} base={path} editable={!!editable} close={()=>setInspected(undefined)} repair={openSession}/>}
    {edit && <MaterialEditor {...props} base={path} edit={edit} close={()=>setEdit(null)} saved={()=>{setEdit(null);setRevision(v=>v+1);}}/>}
  </>;
}

export function Materials({overview,openSession,...props}:Props & {overview:boolean;openSession:OpenSession}) {
  const [revision,setRevision] = useState(0);
  const [selected,setSelected] = useState(() => new URL(location.href).searchParams.get("program") ?? "");
  const [edit,setEdit] = useState<LineEdit | null>(null);
  const lines = useRead<{programs:Program[]}>("/api/programs",revision);
  const program = lines.value?.programs.find(p => p.id === selected) ?? lines.value?.programs[0];
  function choose(id:string) {
    setSelected(id);
    const url = new URL(location.href);
    url.searchParams.set("program",id);
    history.replaceState(null,"",url);
  }
  return <main id="workspace" className="materials-page" tabIndex={-1}>
    <header className="record-header"><p className="crumb">{props.project.project}</p><div className="title-row">
      <h1>{overview ? "Research lines":"Materials"}</h1><button className="quiet" onClick={()=>setEdit({action:"create"})}>New research line</button></div></header>
    {lines.error && <p role="alert">{lines.error} <button className="quiet" onClick={()=>setRevision(v=>v+1)}>Retry</button></p>}
    {!lines.value && !lines.error && <p role="status">Loading research lines…</p>}
    {lines.value && !program && <p>No research lines in this project.</p>}
    {program && <><label className="material-line"><span id="material-line-label">Research line</span><select aria-labelledby="material-line-label" value={program.id} onChange={e=>choose(e.target.value)}>
      {lines.value?.programs.map(p=><option key={p.id} value={p.id}>{p.name}{p.status === "closed" ? " (closed)":""}</option>)}
    </select></label>
    {program.status !== "closed" && <div className="material-actions">
      <button className="quiet" onClick={()=>setEdit({action:"rename",program})}>Rename research line</button>
      <button className="quiet" onClick={()=>setEdit({action:"close",program})}>Close research line</button>
    </div>}
    {overview ? <ProgramOverview key={program.id} base={apiPath("/api/programs/"+encodeURIComponent(program.id))} openSession={openSession}/>
      : <LineMaterials key={program.id} program={program} openSession={openSession} {...props}/>}</>}
    {edit && <LineEditor edit={edit} close={()=>setEdit(null)} saved={id=>{choose(id);setEdit(null);setRevision(v=>v+1);}}/>}
  </main>;
}
