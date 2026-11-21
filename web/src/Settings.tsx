import { useEffect, useState } from "react";
import { errorMessage, request } from "./api";
import { Dialog } from "./Dialog";

type Value = boolean | string | string[] | number | null;
type Patch = Record<string, Record<string, unknown>>;
interface Settings {
  revision: string | null; example: boolean;
  profile: {name:string;recommended_available:boolean;presets:Record<string,Record<string,Value>>};
  project: {name: string; classification: string; data_dirs: string[]};
  agent: Record<string, Value>; models: Record<string, {allowed: boolean; [key: string]: number | boolean | null}>;
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
  ["literature_subtasks","Literature subtasks","Allow a child session to search the listed literature tools.","v05-literature-subtasks"],
  ["source_to_kernel","Sources in kernel","Stage retrieved source content for computation.","v05-literature-subtasks"],
  ["references","Follow-up references","Attach the recorded source, calculation or finding to a question.","v07-references"],
  ["coverage","Evidence coverage","Record subquestions, actual searches and remaining gaps.","v08-baseline"],
  ["evidence_skills","Evidence category skills","Suggest evidence types and sources for the three research scenarios.","v08-retrieval-diagnosis"],
  ["first_pass","First-pass judgment","Record a judgment, counterevidence, disagreement and next step.","v08-baseline"],
  ["vision","Figure inspection","Let a compatible model inspect recorded images.","v05-target-expansion"],
  ["plan_default","Request plan approval by default","Start new questions with the plan approval option selected.","v03-plan-gate"],
];
const rateLabels = {input_usd_per_million: "Input USD per million tokens", output_usd_per_million: "Output USD per million tokens", context_window: "Context window in tokens"};
export function SettingsDialog({quick = false, base, close, saved}: {quick?: boolean; base: string; close: () => void; saved: () => Promise<void>}) {
  const [data, setData] = useState<Settings>();
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
      if (section === "agent" && preset !== "custom" && key in (data?.profile.presets.minimal ?? {})) {
        return {...old,profile:{name:"custom"},agent:{...data?.profile.presets[preset],...old.agent,[key]:value}};
      }
      return {...old, [section]: {...old[section], [key]: value}};
    });
    setMessage("");
  }
  function agentValue(key: string) {
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
      if (path === "/api/settings") { setPatch({}); setMessage("Saved for future research turns."); }
      await saved();
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
  return <Dialog id="settings-dialog" title={quick ? "Chat settings" : "Project settings"} closeLabel="Close settings" close={close}>
    {!data ? <p role="status">{message || "Loading settings…"}</p> : <>
      <p>{data.project.name} · {data.project.classification}</p>
      {data.example && <p>The offline example uses fixed settings.</p>}
      <fieldset disabled={busy || data.example}>
      {quick ? <><p className="muted">Project defaults for future questions in this project.</p>
        {mechanisms.filter(([key]) => common.includes(key)).map(mechanism)}</> : <>
      <details open><summary>Mechanisms</summary>
        <label>Research profile<select aria-label="Research profile" value={String(patch.profile?.name ?? (patch.agent ? "custom" : data.profile.name))}
          onChange={e=>{const name=e.target.value; setPatch(old=>{const next:Patch={...old,profile:{name}}; delete next.agent; return next;});}}>
          <option value="recommended" disabled={!data.profile.recommended_available}>Recommended{data.profile.recommended_available ? "" : " (evaluation pending)"}</option>
          <option value="minimal">Minimal</option><option value="custom">Custom</option>
        </select></label>
        {mechanisms.map(mechanism)}
        <label>Literature tool names (comma-separated)<input defaultValue={(data.agent.literature_tools as string[]).join(", ")}
          onChange={e => update("agent","literature_tools",e.target.value.split(",").map(s => s.trim()).filter(Boolean))}/></label>
      </details>
      <details><summary>Models and roles</summary>{Object.entries(data.roles).map(([role, value]) => {
        const chain = Array.isArray(value) ? value : [value];
        const selected = patch.roles?.[role] ?? chain;
        return <label key={role}>{role}<select aria-label={role} value={String(Array.isArray(selected) ? selected[0] ?? "" : selected)}
          onChange={e => update("roles",role,e.target.value === "auto" ? "auto" : [e.target.value,...chain.slice(1).filter(s => s !== e.target.value)])}>
          <option value="" disabled>Default routing</option>{role === "reviewer" && <option>auto</option>}
          {Object.entries(data.models).map(([s,m]) => <option key={s} disabled={!m.allowed}>{s}</option>)}</select>
          {chain.length > 1 && <small>Fallbacks: {chain.slice(1).join(", ")}</small>}</label>;
      })}{Object.entries(data.models).map(([spec,m]) => <details key={spec}><summary>{spec}</summary>
        {Object.entries(rateLabels).map(([k,label]) => <label key={k}>{label}<input type="number" min="0" step="any"
          defaultValue={String(m[k] ?? "")} onChange={e => {
            const n = e.target.value === "" ? null : Number(e.target.value);
            setPatch(old => ({...old, models: {...old.models,[spec]: {...(old.models?.[spec] as object),[k]: n}}}));
          }}/></label>)}</details>)}</details>
      <details><summary>Budget per turn</summary>{numeric("budget_tokens","Token limit")}{numeric("budget_usd","USD limit")}{numeric("budget_seconds","Time limit in seconds")}</details>
      <details><summary>Permissions</summary>{data.permissions.map(p => {
        const isRevoked = revoked.some(([perm,target]) => perm === p.permission && target === p.target);
        return <label key={p.permission + p.target}><input type="checkbox" checked={!isRevoked} onChange={() => update("permissions","revoked",
          isRevoked ? revoked.filter(([perm,t]) => perm !== p.permission || t !== p.target) : [...revoked,[p.permission,p.target]])}/>
          {p.permission} · {p.target} ({p.scope})</label>;
      })}</details></>}</fieldset>
      {!quick && <><details><summary>Connectors</summary>{data.connectors.length ? data.connectors.map(s => <div key={s.name}>{s.name} · {s.clearance} · {s.status}
        {s.mode === "replay" && <p className="muted">Recorded responses only. Queries outside this recording cannot retrieve new records.</p>}
        {s.tools != null && " · " + s.tools + " tools"} <button disabled={busy || !base || !s.allowed || data.example}
          onClick={() => act(base+"/connectors/"+encodeURIComponent(s.name)+"/reconnect",{})}>Reconnect</button></div>) : <p>No MCP servers configured.</p>}</details>
      <details><summary>API keys</summary><p>This reports the environment of the running workbench. Start it from the terminal where you exported the key.</p>
        {data.keys.length ? data.keys.map(k => <p key={k.reference}><code>{k.reference}</code> · {k.state}</p>) : <p>No provider environment variables configured.</p>}</details>
      <details><summary>Runtime and project</summary><p>Kernel isolation: {data.environment.isolation} · Network: {data.environment.network}</p>
        <p>Data directories: {data.project.data_dirs.join(", ") || "Project directory only"}</p>
        <p>Environment lock SHA-256: <code>{data.environment.lock_hash || "No active project lock"}</code></p>
        <details><summary>Python packages ({data.environment.packages.length})</summary><ul>{data.environment.packages.map(([n,v]) => <li key={n+v}>{n} {v}</li>)}</ul><p>Add a project package with <code>pixi add PACKAGE</code>.</p></details>
        <details><summary>R packages ({data.environment.r_packages.length})</summary>
          <ul>{data.environment.r_packages.map(([n,v]) => <li key={n}>{n} {v}</li>)}</ul></details>
        {data.environment.r_available ? <p>Add an R package with <code>pixi add r-PACKAGE</code>.</p> :
          <p>To enable R, provision the project with <code>pixi add r-base r-irkernel</code> and set <code>pixi = true</code> under <code>[kernel]</code>.</p>}</details></>}
      <button className="primary" disabled={!Object.keys(patch).length || busy || data.example} onClick={() => act("/api/settings",{revision:data.revision,patch})}>Save settings</button>
      <p role="status">{message}</p></>}
  </Dialog>;
}
