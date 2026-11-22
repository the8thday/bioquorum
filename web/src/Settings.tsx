import { useEffect, useState } from "react";
import { errorMessage, request } from "./api";
import { KeyHelp } from "./Onboarding";
import { ModelConnection, type Provider } from "./ModelConnection";
import { Dialog } from "./Dialog";

type Value = boolean | string | string[] | number | null;
type Patch = Record<string, Record<string, unknown>>;
interface Settings {
  providers:Record<string,Provider>; revision: string | null; local_setup?: boolean; example: boolean;
  profile: {name:string;recommended_available:boolean;presets:Record<string,Record<string,Value>>};
  project: {name: string; instructions:string; classification: string; data_dirs: string[]};
  agent: Record<string, Value>; models: Record<string, {allowed: boolean; [key: string]: number | boolean | null}>;
  model_evaluation?: {scores:Record<string,Record<string,{correct:number;total:number;failed:number}>>; suggestions:Record<string,string[]>};
  roles: Record<string, string | string[]>; keys: {reference: string; state: string}[];
  permissions: {permission: string; scope: string; target: string; revoked: boolean}[];
  environment: {isolation: string; network: string; packages: [string,string][]; r_packages: [string,string][]; r_available: boolean; lock_hash: string | null};
  connectors: {name: string; mode?: string; status: string; clearance: string; tools: number | null; allowed: boolean}[];
}
const mechanisms = [
  ["binding","Evidence binding","Connect reported figures and quotes to their recorded evidence.","binding-eval"],
  ["binding_retries","Binding retries","Return unresolved bindings to the worker for up to two revisions.","binding-repair-eval"],
  ["compact_at_bytes","Compress at this many bytes","Summarize older messages past this threshold. Leave blank for manual compression.","v02-runtime-controls"],
  ["skills","Research skills","Let the worker load applicable research instructions.","v05-project-context"],
  ["program_context","Research line context","Read previous findings and researcher decisions as background in attached sessions.","v10-program-context"],
  ["project_notes","Project notes","Make the saved researcher note available across sessions.","v05-project-context"],
  ["specialists","Project specialists","Use project-defined roles in parallel tasks.","v12-scale"],
  ["subtasks","Parallel research tasks","Delegate independent questions using the approved tools.","v12-scale"],
  ["literature_subtasks","Literature subtasks","Allow a child session to search the listed literature tools.","v05-literature-subtasks"],
  ["source_to_kernel","Sources in kernel","Stage retrieved source content for computation.","v05-literature-subtasks"],
  ["references","Follow-up references","Attach the recorded source, calculation or finding to a question.","v07-references"],
  ["coverage","Evidence coverage","Record subquestions, actual searches and remaining gaps.","v08-profile"],
  ["domain_skills","Domain workflow skills","Load omics, literature, sequence, structure and chemistry guidance on demand.","v11-domain-skills"],
  ["evidence_skills","Evidence category skills","Suggest evidence types and sources for the three research scenarios.","v08-profile"],
  ["first_pass","First-pass judgment","Record a judgment, counterevidence, disagreement and next step.","v08-profile"],
  ["vision","Figure inspection","Let a compatible model inspect recorded images.","v05-target-expansion"],
  ["plan_default","Request plan approval by default","Start new questions with the plan approval option selected.","v03-plan-gate"],
];
const rateLabels = {input_usd_per_million: "Input USD per million tokens", output_usd_per_million: "Output USD per million tokens", context_window: "Context window in tokens"};
export function SettingsDialog({quick = false, modelsFirst = false, base, close, saved, removeProject}: {removeProject:()=>void; quick?: boolean; modelsFirst?: boolean; base: string; close: () => void; saved: () => Promise<void>}) {
  const [data, setData] = useState<Settings>();
  const [localTag, setLocalTag] = useState("");
  const [patch, setPatch] = useState<Patch>({});
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const c = new AbortController();
    request<Settings>("/api/settings", undefined, c.signal).then(setData).catch(e => {if (!c.signal.aborted) setMessage(errorMessage(e));});
    return () => c.abort();
  }, []);
  function update(section: string, key: string, value: unknown) {
    setPatch(old => {
      const preset = String(old.profile?.name ?? data?.profile.name);
      if (section === "agent" && preset !== "custom" && (key in (data?.profile.presets.minimal ?? {}) || ["domain_skills","subtasks","specialists"].includes(key))) {
        return {...old,profile:{name:"custom"},agent:{...data?.profile.presets[preset],...old.agent,[key]:value}};
      }
      return {...old, [section]: {...old[section], [key]: value}};
    });
    setMessage("");
  }
  function agentValue(key: string) {
    if (patch.profile?.name && patch.profile.name !== "custom" && ["domain_skills","subtasks","specialists"].includes(key)) return false;
    return Object.hasOwn(patch.agent ?? {}, key) ? patch.agent[key] : (data?.profile.presets[String(patch.profile?.name)]?.[key] ?? data?.agent[key]);
  }
  function numeric(key: string, label: string) {
    return <label>{label}<input aria-label={label} type="number" min={key === "binding_retries" ? 0 : undefined}
      max={key === "binding_retries" ? 2 : undefined} step="any" value={String(agentValue(key) ?? "")}
      onChange={e => update("agent",key,e.target.value === "" ? null : Number(e.target.value))}/></label>;
  }
  async function act(path: string, body: unknown) {
    setBusy(true); setMessage("");
    try {
      setData(await request<Settings>(path, body));
      if (path === "/api/settings") setPatch({});
      await saved();
      if (path === "/api/settings") close();
    } catch (e) {setMessage(errorMessage(e));} finally {setBusy(false);}
  }
  const revoked = (patch.permissions?.revoked ?? data?.permissions.filter(p => p.revoked).map(p => [p.permission,p.target]) ?? []) as string[][];
  const common = ["references","literature_subtasks","project_notes","plan_default"];
  function mechanism([key,label,description,report]: string[]) {
    return <div className="setting-row" key={key}>
      {["binding_retries","compact_at_bytes"].includes(key) ? numeric(key,label) :
        <label><input type="checkbox" checked={Boolean(agentValue(key))} onChange={e => update("agent",key,e.target.checked)}/> {label}</label>}
      <small>{description} {!quick && <a href={"https://github.com/the8thday/bioquorum/blob/main/docs/research/" + report + ".md"} target="_blank" rel="noreferrer">Evaluation</a>}</small>
    </div>;
  }
  return <Dialog id="settings-dialog" title={modelsFirst ? "Models & accounts" : quick ? "Chat settings" : "Project settings"} closeLabel="Close settings" close={()=>{if (!busy) close();}}>
    {!data ? <p role="status">{message || "Loading settings…"}</p> : <>
      <p>{data.project.name} · {data.project.classification}</p>
      {data.example && <p>The offline example uses fixed settings.</p>}
      {modelsFirst && data.example && <button className="quiet" onClick={()=>{close();requestAnimationFrame(()=>document.getElementById("new-project")?.click());}}>Create a project to configure models</button>}
      {modelsFirst && !data.example && <ModelConnection providers={data.providers} classification={data.project.classification} disabled={busy || data.example}
        apply={connection=>act("/api/settings",{revision:data.revision,patch:{connection}})}/>}
      <fieldset disabled={busy || data.example}>
      {quick ? <><p className="muted">Project defaults for future questions in this project.</p>
        {mechanisms.filter(([key]) => common.includes(key)).map(mechanism)}</> : <>
      {!modelsFirst && <><details open><summary>Project details</summary>
        <label>Project name<input maxLength={160} value={String(patch.project?.display_name ?? data.project.name)}
          onChange={e=>update("project","display_name",e.target.value)}/></label>
        <label>Data classification<select aria-label="Data classification" value={String(patch.project?.classification ?? data.project.classification)}
          onChange={e=>update("project","classification",e.target.value)}>
          <option value="public">Public</option><option value="internal">Internal</option><option value="confidential">Confidential</option>
        </select></label>
        {!!patch.project?.classification && patch.project.classification!==data.project.classification && <p className="warning">
          This applies to future use of all sessions and files in this project. {patch.project.classification==="public"
            ? "Queries may send existing project content to public services. Manage database search in MCP connectors."
            : "Models and tools will use the new data restrictions."}</p>}
        <label>Project instructions<textarea rows={4} maxLength={6000} value={String(patch.project?.instructions ?? data.project.instructions)}
          onChange={e=>update("project","instructions",e.target.value)}/></label>
        <small>Applied to future research and plans in this project.</small>
        <div className="actions"><button type="button" className="quiet danger" onClick={removeProject}>{location.pathname.startsWith("/projects/") ? "Delete this project…" : "Manage projects"}</button></div></details>
      <details open><summary>Mechanisms</summary>
        <label>Research profile<select aria-label="Research profile" value={String(patch.profile?.name ?? (patch.agent ? "custom" : data.profile.name))}
          onChange={e=>{const name=e.target.value; setPatch(old=>{const next:Patch={...old,profile:{name}}; delete next.agent; return next;});}}>
          <option value="recommended" disabled={!data.profile.recommended_available}>Recommended{data.profile.recommended_available ? "" : " (evaluation pending)"}</option>
          <option value="minimal">Minimal</option><option value="custom">Custom</option>
        </select></label>
        {mechanisms.map(mechanism)}
        <label>Subtask tools (comma-separated)<input defaultValue={(data.agent.subtask_tools as string[]).join(", ")}
          onChange={e=>update("agent","subtask_tools",e.target.value.split(",").map(s=>s.trim()).filter(Boolean))}/></label>
        <label>Concurrent subtasks<input type="number" min={1} max={8} defaultValue={Number(data.agent.subtask_parallelism)}
          onChange={e=>update("agent","subtask_parallelism",Number(e.target.value))}/></label>
        <label>Literature tool names (comma-separated)<input defaultValue={(data.agent.literature_tools as string[]).join(", ")}
          onChange={e => update("agent","literature_tools",e.target.value.split(",").map(s => s.trim()).filter(Boolean))}/></label>
      </details></>}
      {modelsFirst && data.roles.worker?.length>0 && <p>Current research model · {Array.isArray(data.roles.worker) ? data.roles.worker[0] : data.roles.worker}</p>}
      <details><summary>{modelsFirst ? "Project models and fallbacks" : "Models and roles"}</summary>
{Object.entries(data.roles).filter(([role])=>!modelsFirst || role==="worker").map(([role, value]) => {
        const chosen = patch.roles?.[role] ?? value;
        const chain: string[] = Array.isArray(chosen) ? chosen : chosen ? [String(chosen)] : [];
        const models = Object.entries(data.models);
        const options = models.filter(([name,m])=>m.allowed && !chain.includes(name));
        return <div className="setting-row" key={role}><label>{modelsFirst ? "Research model" : role}
          <select aria-label={modelsFirst ? "Research model" : role} value={chain[0] ?? ""}
            onChange={e=>update("roles",role,e.target.value==="auto" ? "auto" : [e.target.value,...chain.slice(1).filter(s=>s!==e.target.value)])}>
            <option value="" disabled>{role==="worker" ? "Select a model" : "Default routing"}</option>{role==="reviewer" && <option>auto</option>}
            {chain[0] && chain[0]!=="auto" && !data.models[chain[0]] && <option>{chain[0]}</option>}{models.map(([name,m])=><option key={name} disabled={!m.allowed}>{name}</option>)}</select></label>
          {chain.slice(1).map((model,i)=><div key={model}>Fallback {i+1} · {model}
            <button type="button" className="quiet" onClick={()=>update("roles",role,chain.filter(s=>s!==model))}>Remove</button></div>)}
          {chain[0] && chain[0]!=="auto" && options.length>0 && <label>Add fallback
            <select aria-label={"Add "+role+" fallback"} value="" onChange={e=>update("roles",role,[...chain,e.target.value])}>
              <option value="" disabled>Select a model</option>{options.map(([name])=><option key={name}>{name}</option>)}</select></label>}
        </div>;
      })}
        {data.local_setup && <details><summary>Use a local Ollama worker</summary>
          <label>Installed model tag<input value={localTag} onChange={e=>setLocalTag(e.target.value)} placeholder="e.g. qwen3:8b"/></label>
          <button className="quiet" disabled={!localTag.trim()} onClick={()=>update("roles","worker",["ollama:"+localTag.trim()])}>Select local model</button></details>}
      {!modelsFirst && data.model_evaluation && <section><h3>Model evaluation</h3>
        {Object.entries(data.model_evaluation.scores).length===0 && <p>Run <code>bq models eval</code> to score the configured models.</p>}
        {Object.entries(data.model_evaluation.scores).map(([model,scores])=><p key={model}>{model} · {Object.entries(scores).map(([role,s])=>role+" "+s.correct+"/"+s.total+(s.failed?" ("+s.failed+" failed)":"")).join(" · ")}</p>)}
        {Object.entries(data.model_evaluation.suggestions).map(([role,models])=><p key={role}>Suggested for {role} · {models.join(", ")}</p>)}</section>}{!modelsFirst && Object.entries(data.models).filter(([,m])=>m.configured!==false).map(([spec,m]) => <details key={spec}><summary>{spec}</summary>
        {Object.entries(rateLabels).map(([k,label]) => <label key={k}>{label}<input type="number" min="0" step="any"
          defaultValue={String(m[k] ?? "")} onChange={e => {
            const n = e.target.value === "" ? null : Number(e.target.value);
            setPatch(old => ({...old, models: {...old.models,[spec]: {...(old.models?.[spec] as object),[k]: n}}}));
          }}/></label>)}</details>)}</details>
      {!modelsFirst && <><details><summary>Budget per turn</summary>{numeric("budget_tokens","Token limit")}{numeric("budget_usd","USD limit")}{numeric("budget_seconds","Time limit in seconds")}</details>
      <details><summary>Permissions</summary>{data.permissions.map(p => {
        const isRevoked = revoked.some(([perm,target]) => perm === p.permission && target === p.target);
        return <label key={p.permission + p.target}><input type="checkbox" checked={!isRevoked} onChange={() => update("permissions","revoked",
          isRevoked ? revoked.filter(([perm,t]) => perm !== p.permission || t !== p.target) : [...revoked,[p.permission,p.target]])}/>
          {p.permission} · {p.target} ({p.scope})</label>;
      })}</details></>}</>}</fieldset>
      {!quick && <>{!modelsFirst && <details><summary>Connectors</summary>{data.connectors.length ? data.connectors.map(s => <div key={s.name}>{s.name} · {s.clearance} · {s.status}
        {s.mode === "replay" && <p className="muted">Recorded responses only. Queries outside this recording cannot retrieve new records.</p>}
        {s.tools != null && " · " + s.tools + " tools"} <button disabled={busy || !base || !s.allowed || data.example}
          onClick={() => act(base+"/connectors/"+encodeURIComponent(s.name)+"/reconnect",{})}>Reconnect</button></div>) : <p>No MCP servers configured.</p>}</details>}
      <details open={modelsFirst}><summary>API keys</summary><p>This reports the environment of the running workbench. Start it from the terminal where you exported the key.</p>
        {data.keys.length ? data.keys.map(k => <div key={k.reference}><code>{k.reference.slice(4)}</code> · {k.state==="set" ? "Detected by workbench" : "Missing from workbench"}{k.state==="missing" && <KeyHelp name={k.reference.slice(4)}/>}</div>) : <p>No provider environment variables configured.</p>}</details>
      {!modelsFirst && <details><summary>Runtime and project</summary><p>Kernel isolation: {data.environment.isolation} · Network: {data.environment.network}</p>
        <p>Data directories: {data.project.data_dirs.join(", ") || "Project directory only"}</p>
        <p>Environment lock SHA-256: <code>{data.environment.lock_hash || "No active project lock"}</code></p>
        <details><summary>Python packages ({data.environment.packages.length})</summary><ul>{data.environment.packages.map(([n,v]) => <li key={n+v}>{n} {v}</li>)}</ul><p>Add a project package with <code>pixi add PACKAGE</code>.</p></details>
        <details><summary>R packages ({data.environment.r_packages.length})</summary>
          <ul>{data.environment.r_packages.map(([n,v]) => <li key={n}>{n} {v}</li>)}</ul></details>
        {data.environment.r_available ? <p>Add an R package with <code>pixi add r-PACKAGE</code>.</p> :
          <p>To enable R, provision the project with <code>pixi add r-base r-irkernel</code> and set <code>pixi = true</code> under <code>[kernel]</code>.</p>}</details>}</>}
      <button className="primary" disabled={!Object.keys(patch).length || busy || data.example} onClick={() => act("/api/settings",{revision:data.revision,patch})}>Save settings</button>
      <p role="status">{message}</p></>}
  </Dialog>;
}
