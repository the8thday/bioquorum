import { useEffect, useRef, useState } from "react";
import { errorMessage } from "./api";
import { Dialog } from "./Dialog";

import { Icon } from "./Icon";

export interface Product {id: string; name: string; kind: string; version: number; status: string; hash?: string; source?: string; turn?: string | null; turn_number?: number | null; path?: string; run?:string; seq:number; preview:"table"|"figure"|"report"|"html"|"sequence"|"structure"|"genome"|"molecule"|null}

export function Artifacts({base, rows, attach, open}: {base: string; rows: Product[]|undefined; attach:(a:Product)=>void;open:(a:Product)=>void}) {
  return <aside className="ledger" aria-label="Session artifacts"><h2>Artifacts</h2>
    <a href={base+"/notebook"} download>Download kernel notebook</a>
    {!rows && <p role="status">Loading artifacts…</p>}
    {rows?.length === 0 && <p>Uploaded files, figures and exported bundles will appear here.</p>}
    {[...(rows ?? [])].reverse().map(row => <article key={row.id} className="artifact-row"><strong>{row.name}</strong>
      <p>{row.kind} · v{row.version} · {row.status}{row.turn_number != null ? ` · Turn ${row.turn_number}` : row.turn ? ` · Turn ${row.turn}` : ""}</p>
      <a href={base+"/artifacts/"+row.id+"/file"} download>Download</a>{" "}
      {row.preview && <button className="quiet" onClick={()=>open(row)}>
        {row.preview==="table"?"Preview table":row.preview==="figure"?"Enlarge figure":row.preview==="report"?"Read report":"View "+row.preview}</button>}
      {row.source === "user_upload" && <button className="quiet" onClick={() => attach(row)}>Attach to question</button>}
    </article>)}</aside>;
}

export async function uploadFile(base: string, file: File, classification: string, signal?: AbortSignal): Promise<Product> {
  const query = new URLSearchParams({name:file.name, classification});
  const response = await fetch(base+"/artifacts?"+query, {
    method:"POST", headers:{"Content-Type":"application/octet-stream"}, body:file, signal,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "Upload failed");
  }
  return response.json();
}

export function Upload({base,resolveBase,classification,onUpload,disabled}: {base:string;resolveBase?:()=>Promise<string>;classification:string;onUpload:(p:Product)=>void;disabled:boolean}) {
  const [open,setOpen] = useState(false);
  const [file,setFile] = useState<File>();
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  const [level,setLevel] = useState(classification);
  const active = useRef<AbortController | null>(null);
  useEffect(() => () => active.current?.abort(),[base]);
  function close() {
    active.current?.abort();
    active.current = null;
    setOpen(false);setFile(undefined);setError("");setBusy(false);
  }
  async function send() {
    if (!file || busy || disabled) return;
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);setError("");
    try {
      const path = resolveBase ? await resolveBase() : base;
      const product = await uploadFile(path, file, level, controller.signal);
      if (!controller.signal.aborted) {onUpload(product);close();}
    } catch (e) {
      if (!controller.signal.aborted) {setError(errorMessage(e));setBusy(false);}
    }
  }
  return <><button type="button" className="icon-button attach-trigger" aria-label="Attach a file" title="Attach a file"
    disabled={disabled} onClick={() => {setLevel(classification);setOpen(true);}}><Icon name="paperclip"/></button>
    {open && <Dialog id="attachment-dialog" title="Attach a file" closeLabel="Close attachment" close={close}>
      <label className="file-picker"><Icon name="paperclip"/><span>Upload file</span>
        <input type="file" disabled={busy} onChange={e => {setFile(e.target.files?.[0]);setError("");}}/></label>
      <label className="file-level">File classification <select value={level} disabled={busy} onChange={e => setLevel(e.target.value)}>
        {["public","internal","confidential"].map(k => <option key={k}>{k}</option>)}</select></label>
      {error && <p role="alert">{error}</p>}
      <div className="actions"><button type="button" className="quiet" onClick={close}>Cancel</button>
        <button type="button" className="primary" disabled={!file || busy || disabled} onClick={send}>{busy ? "Uploading…" : "Attach file"}</button></div>
    </Dialog>}</>;
}
