import { Dialog } from "./Dialog";
import type { Settings } from "./Settings";
import { useProjectRead } from "./useProjectRead";

export type ToolDestination = "methods" | "runtime" | "models" | "sources" | "skills" | "program"
  | "materials" | "tasks" | "inbox" | "Evidence" | "Artifacts" | "Context" | "Usage";

export function ResearchTools({session, close, open}: {
  session: boolean; close: () => void; open: (target: ToolDestination) => void;
}) {
  const {value: data, error} = useProjectRead<Settings>("/api/settings");
  function action(target: ToolDestination, label: string, disabled = false) {
    return <button className="quiet" disabled={disabled} onClick={() => open(target)}>{label}</button>;
  }
  return <Dialog id="research-tools" title="Research tools" closeLabel="Close research tools" close={close}>
    {!data ? <p role="status">{error || "Loading project tools…"}</p> : <>
      <p className="muted">{data.project.name} · {data.example ? "Offline example" : data.project.classification + " project"}</p>
      <div className="tool-directory">
        <section><h3>Search and investigate</h3>
          <p>Public databases · {data.example ? "Offline example" : data.project.classification !== "public"
            ? <span>Blocked for {data.project.classification} data</span> : !data.public_search.available
            ? "Minimal profile" : data.public_search.enabled ? "Enabled" : "Off"}</p>
          <p>Evidence coverage · {data.agent.coverage ? "On" : "Off"}<br/>
            Dedicated literature lookup · {data.agent.literature_subtasks ? "On" : "Off"}</p>
          <div>{action("sources","Manage sources")}{action("methods","Research methods")}{action("skills","Browse skills")}</div>
        </section>
        <section><h3>Run an analysis</h3>
          <p>R kernel · {data.environment.r_available ? "Detected" : "Not detected"}<br/>
            Kernel isolation · {data.environment.isolation}</p>
          <p>Attach data to a question. Inspect Python and R packages, the environment lock and model routing.</p>
          <div>{action("runtime","Inspect runtime")}{action("models","Models and roles")}</div>
        </section>
        <section><h3>Verify and export</h3>
          <p>Inspect a source or calculation. Eligible claims offer rerun and independent replication after review.</p>
          <p>Open session artifacts to view structures, sequences, molecules, tracks and tables, or download a notebook. Export the research bundle from Session actions.</p>
          {!session && <small>Open a session to inspect its record.</small>}
          <div>{action("Evidence","Inspect evidence",!session)}{action("Artifacts","View artifacts",!session)}</div>
        </section>
        <section><h3>Continue a project</h3>
          <p>Follow background tasks and pending approvals. Research lines connect sessions, material versions, corrections and handoff reports.</p>
          <p>Parallel tasks · {data.agent.subtasks ? "On" : "Off"}<br/>Specialist profiles · {data.specialist_profiles.length} defined</p>
          <div>{action("tasks","Task board")}{action("inbox","Open inbox")}
            {!data.example && <>{action("program","Open research lines")}{action("materials","Manage materials")}</>}</div>
        </section>
      </div>
      <details><summary>Session usage and context</summary>
        <p>Inspect model costs and token counts, or compact saved conversation history from its context panel.</p>
        {!session && <small>Open a session to see these records.</small>}
        <div>{action("Usage","Inspect usage",!session)}{action("Context","Inspect context",!session)}</div>
      </details>
      <details><summary>Command-line tools and deployment</summary>
        <p>Run <code>bq mcp serve</code> in the terminal to expose tools to another agent. Run <code>bq models eval</code> in the project to score configured models. Live evaluations can incur provider charges.</p>
        <a href="https://github.com/the8thday/bioquorum/blob/main/docs/demo-v0.6.md" target="_blank" rel="noreferrer">Private deployment and runtime setup</a>
      </details>
    </>}
  </Dialog>;
}
