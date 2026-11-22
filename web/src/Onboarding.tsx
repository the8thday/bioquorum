import type { Project } from "./types";

export function KeyHelp({name}: {name: string}) {
  return <details><summary>How to set {name}</summary>
    <p>In the Linux terminal that starts BioQuorum, run these commands and enter the key at the hidden prompt. Restart the workbench from that same terminal using your usual launch command.</p>
    <pre>{`read -rs ${name}\nexport ${name}`}</pre>
    <p>If you already exported it in that terminal, just restart the workbench there.</p></details>;
}

export function Onboarding({project, home, models}: {project: Project; home: boolean; models: () => void}) {
  if (project.example) return <details className="setup"><summary>Use a live model in your own project</summary>
    <p>The metformin example replays a saved session. Open an initialized research project to use your models.</p>
    <pre>uv run --no-sync bioquorum -C /path/to/project</pre></details>;
  const state = project.onboarding;
  if (!state) return null;
  const key = state.credential;
  const ready = state.worker === "ready";
  if (!home && ready && (key?.state === "set" || key?.state === "not_required")) return null;
  return <section className="panel setup model-status" aria-label="Model setup">
    <div><strong>{ready ? state.model : state.worker === "restricted" ? "No worker allowed for this project" : "No research model selected"}</strong>
      {key && <span>{key.state==="connected" ? "Using ChatGPT plan" : key.state==="sign_in" ? "ChatGPT plan · Sign-in required" : key.state === "not_required" ? "No API key required" : key.state === "unsupported" ? "Unsupported credential reference" :
      `${key.name} · ${key.state === "set" ? "Detected by workbench" : "Missing from workbench"}`}</span>}</div>
    <button className="quiet" onClick={models}>{ready ? "Model settings" : "Choose model"}</button>
    {key?.state === "connected" && <a href="https://chatgpt.com/settings/usage" target="_blank" rel="noreferrer">Manage ChatGPT usage</a>}
    {key?.state === "missing" && <KeyHelp name={key.name}/>}
    {!ready && <a href="./example/">Open the offline metformin example</a>}
  </section>;
}
