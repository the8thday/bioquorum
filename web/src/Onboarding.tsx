import type { Project } from "./types";

export function Onboarding({project}: {project: Project}) {
  if (project.example) return <details className="setup"><summary>Use a live model in your own project</summary>
    <p>The metformin example replays a saved session. Open an initialized research project to use your models.</p>
    <pre>uv run --no-sync bioquorum -C /path/to/project</pre></details>;
  const state = project.onboarding;
  if (!state || state.worker === "ready") return null;
  return <section className="panel setup" aria-label="Model setup"><h2>{state.worker === "restricted" ? "No worker allowed for this project" : "Configure a worker model"}</h2>
    {state.needs_local || state.worker === "restricted" ? <p>Choose a worker whose provider is approved for {project.classification} data in bioquorum.toml. For an approved local Ollama installation, replace MODEL_NAME with its installed model tag.</p> :
      <><p>Add these sections to the project’s bioquorum.toml. Merge them with any existing provider or role sections.</p>
      <pre>{'[providers.openai]\napi_key = "env:OPENAI_API_KEY"\n\n[roles]\nworker = "openai:gpt-6.1-sol"\nreviewer = "openai:gpt-6.1-sol"'}</pre>
      <p>In your terminal, enter the key at the hidden prompt, then start the workbench from the same shell.</p>
      <pre>{'read -rs OPENAI_API_KEY\nexport OPENAI_API_KEY\nuv run --no-sync bioquorum -C /path/to/project'}</pre></>}
    {(state.needs_local || state.worker === "restricted") && <pre>{'[providers.ollama]\nkind = "ollama"\n\n[roles]\nworker = "ollama:MODEL_NAME"\nreviewer = "ollama:MODEL_NAME"'}</pre>}
    <a href="./example/">Open the offline metformin example</a></section>;
}
