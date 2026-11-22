import { useEffect, useState, type FormEvent } from "react";
import { errorMessage, request } from "./api";
import { Icon } from "./Icon";

export type Section = "General" | "Skills" | "MCP connectors";
type Skill = {license:string;name:string; description:string; enabled:boolean; managed:boolean};
type Connector = {name:string; mode:string; clearance:string; enabled:boolean; managed:boolean; status:string; allowed:boolean; tools:number|null};
type Library = {project:string; example:boolean; revision:string|null; skills_enabled:boolean; classification:string; minimal:boolean; public_search:{enabled:boolean;available:boolean}; included_skills:Pick<Skill,"name"|"description"|"license">[]; skills:Skill[]; connectors:Connector[]};

async function encode(file: File) {
  if (file.size > 1024 * 1024) throw new Error("Choose a file of at most 1 MiB.");
  const bytes = new Uint8Array(await file.arrayBuffer());
  return btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join(""));
}

export function Extensions({section,base,newProject,close}: {section:"Skills"|"MCP connectors";base:string;newProject:()=>void;close:()=>void}) {
  const [library,setLibrary] = useState<Library>();
  const [error,setError] = useState("");
  const [notice,setNotice] = useState("");
  const [busy,setBusy] = useState(false);
  const [adding,setAdding] = useState(false);
  const [remove,setRemove] = useState("");
  const [mode,setMode] = useState("HTTPS");
  const [name,setName] = useState("");
  const [url,setUrl] = useState("");
  const [file,setFile] = useState<File>();
  const skills = section === "Skills";
  useEffect(() => {
    const c = new AbortController();
    request<Library>("/api/extensions",undefined,c.signal).then(setLibrary).catch(e => {if(!c.signal.aborted) setError(errorMessage(e));});
    return () => c.abort();
  }, []);
  async function action(fn:()=>Promise<Library>, message:string, dismiss=false) {
    setBusy(true);setError("");setNotice("");
    try {setLibrary(await fn());setAdding(false);setRemove("");setFile(undefined);setNotice(message);if (dismiss) close();}
    catch(e) {setError(errorMessage(e)); await request<Library>("/api/extensions").then(setLibrary).catch(()=>{});}
    finally {setBusy(false);}
  }
  const edit = (fields:object) => request<Library>("/api/extensions",{revision:library?.revision,...fields});
  function install(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void action(async () => {
      if (skills) {
        if (!file) throw new Error("Choose a skill file first.");
        return edit({action:"install_skill",filename:file.name,content:await encode(file)});
      }
      return edit({action:"add_connector",name:form.get("name"),
        ...(mode === "HTTPS" ? {url:form.get("url")} : {command:form.get("command"),args:String(form.get("args") || "").split("\n").map(a=>a.trim()).filter(Boolean)})});
    },skills ? "Skill installed and enabled for future turns." : "Connector added to this project.", !skills);
  }
  const noun = skills ? "skill" : "connector";
  const presets = [["PubMed","pubmed","https://pubmed.mcp.claude.com/mcp"],
    ["Open Targets","opentargets","https://mcp.platform.opentargets.org/mcp"]].filter(([,id])=>!library?.connectors.some(c=>c.name===id));
  const rows = skills ? library?.skills : library?.connectors;
  return <section className="extension-list" aria-label={section+" library"}>
    <header><div><p className="kicker">{library?.project}</p><h3>{section}</h3></div>
      {library && !library.example && !adding && <button className="primary" onClick={()=>{setAdding(true);setError("");setNotice("");setName("");setUrl("");setMode("HTTPS");}}><Icon name="add"/>{skills ? "Install skill" : "Add connector"}</button>}</header>
    {error && <p className="error" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!library && !error && <p role="status">Loading extensions…</p>}
    {library?.example && <p className="extension-hint">This example uses a fixed toolset. <button className="quiet" onClick={newProject}>Create a project to customize</button></p>}
    {adding && <form className="extension-form" onSubmit={install}>
      {skills ? <label>Skill file<input autoFocus type="file" required accept=".md,.zip,.skill" onChange={e=>setFile(e.target.files?.[0])}/>
        <small>Choose SKILL.md or a .zip / .skill package. Text references and assets are included.</small></label> : <>
        {mode==="HTTPS" && presets.length>0 && <div className="connector-presets"><span>Use a preset</span>{presets.map(([label,id,endpoint])=>
          <button type="button" className="quiet" key={id} onClick={()=>{setName(id);setUrl(endpoint);}}>{label}</button>)}</div>}
        <label>Connector name<input name="name" value={name} onChange={e=>setName(e.target.value)} autoFocus required maxLength={32} pattern="[A-Za-z0-9_\-]+" placeholder="e.g. pubmed"/></label>
        <label>Connection type<select value={mode} onChange={e=>setMode(e.target.value)}><option>HTTPS</option><option>Local program</option></select></label>
        {mode==="HTTPS" ? <label>Server URL<input type="url" name="url" value={url} onChange={e=>setUrl(e.target.value)} required placeholder="https://example.org/mcp"/>
          <small>For public project data. Use an endpoint without embedded credentials.</small></label> : <>
          <label>Installed executable<input name="command" required placeholder="e.g. my-mcp-server"/></label>
          <label>Arguments<textarea name="args" rows={2} placeholder="One argument per line"/></label>
          <small>The program runs in the offline sandbox. Install it on this machine first.</small></>}
      </>}
      <div className="actions"><button className="quiet" type="button" disabled={busy} onClick={()=>{setAdding(false);setError("");}}>Cancel</button>
        <button className="primary" disabled={busy}>{skills ? "Install and enable" : "Save connector"}</button></div>
    </form>}
    {library && !skills && !library.example && <section className="extension-row" aria-label="Built-in public databases">
      <Icon name="plug"/><div className="extension-copy"><strong>Built-in public databases</strong>
      <p>Europe PMC papers, PubMed abstracts, ClinicalTrials.gov, US drug labels, GWAS Catalog and Human Protein Atlas.</p>
      {library.public_search.available ? <button className="quiet" disabled={busy} onClick={()=>action(
        ()=>edit({action:"set_public_search",enabled:!library.public_search.enabled}),"Public database settings saved.")}>
        {library.public_search.enabled ? "Disable public search" : "Enable public search"}</button> :
        <p>{library.classification!=="public" ? "Public database queries are blocked for this "+library.classification+" project. To change the classification, open Project settings. You can also import source documents here." : "Choose a non-minimal research profile to use public databases."}</p>}
      </div><span className="extension-state">{!library.public_search.available ? "Blocked" : library.public_search.enabled ? "Enabled" : "Disabled"}</span>
    </section>}
    {library && skills && !adding && !library.example && library.included_skills.length>0 && <details open={library.skills.length===0}>
      <summary>Included with BioQuorum ({library.included_skills.length})</summary>
      {library.minimal && <p>Choose a non-minimal research profile to enable skills.</p>}
      {library.included_skills.map(skill=><article className="extension-row" key={skill.name}>
        <Icon name="skills"/><div className="extension-copy"><strong>{skill.name}</strong><p>{skill.description}<br/>License: {skill.license}</p>
        <button className="quiet" disabled={busy || library.minimal} aria-label={"Enable "+skill.name} onClick={()=>action(
          ()=>edit({action:"install_included_skill",name:skill.name}),skill.name+" enabled.")}>Enable for project</button></div></article>)}
    </details>}
    {!!library && skills && !!rows?.length && !library.skills_enabled && <p className="warning">Research skills are switched off in Project settings.</p>}
    {library && !adding && !rows?.length && <p className="extension-empty">{skills ? "No skills enabled or installed for this project." : "No MCP connectors in this project."}</p>}
    {rows?.map(row=><article className="extension-row" key={row.name}>
      <Icon name={skills ? "skills" : "plug"}/><div className="extension-copy"><strong>{row.name}</strong>
        {"description" in row ? <p>{row.description}<br/>License: {row.license}</p> : <p>{row.mode} · {row.clearance} · {row.enabled ? !row.allowed ? "Blocked by project classification" :
          row.status==="reachable" ? row.tools+" tools available" : row.status==="unchecked" ? "Not tested" : "Unavailable" : "Disabled"}</p>}
        {!library?.example && <div className="extension-ops">
          <button className="quiet" disabled={busy} onClick={()=>action(()=>edit({action:(row.enabled?"disable_":"enable_")+noun,name:row.name}),row.name+(row.enabled?" disabled.":" enabled."))}>{row.enabled?"Disable":"Enable"}</button>
          {"mode" in row && row.enabled && row.allowed && base && <button className="quiet" disabled={busy} onClick={()=>action(async()=>{
            await request(base+"/connectors/"+encodeURIComponent(row.name)+"/reconnect",{});
            return request<Library>("/api/extensions");
          },"Connection check finished.")}>Test connection</button>}
          {row.managed && (remove===row.name ? <><button className="quiet" disabled={busy} onClick={()=>setRemove("")}>Cancel</button>
            <button className="quiet" disabled={busy} onClick={()=>action(()=>edit({action:"remove_"+noun,name:row.name}),row.name+" removed.")}>Confirm removal</button></> :
            <button className="text-action" disabled={busy} onClick={()=>setRemove(row.name)}>Remove</button>)}
        </div>}</div><span className={"extension-state"+(row.enabled ? " enabled" : "")}>{row.enabled?"Enabled":"Disabled"}</span>
    </article>)}
    {!skills && !base && !!rows?.length && !library?.example && <p className="muted">Open a session to test a connection and record the result in its audit log.</p>}
  </section>;
}
