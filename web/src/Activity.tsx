import type { Activity, Snapshot, TurnRef } from "./types";

export function activityLabel(item: Activity) {
  switch (item.type) {
    case "tool_call_requested": return "Requested " + item.tool;
    case "tool_call_finished":
      if (item.replay_miss) return item.tool + " · No matching recorded response";
      return item.empty_result ? item.tool + " · No items returned" : (item.is_error ? "Failed " : "Completed ") + item.tool;
    case "tool_call_failed": return item.tool + " · " + item.error;
    case "model_call_started": return item.role + " · " + item.model;
    case "model_call_finished": return `${item.role} · ${(item.input_tokens ?? 0) + (item.output_tokens ?? 0)} tokens` +
      (item.cost_known && item.cost_usd != null ? ` · est. $${item.cost_usd.toFixed(6)}` : " · Price unavailable");
    case "model_call_failed": return item.model + " · " + item.error;
    case "permission_decided": return (item.granted ? "Allowed " : "Denied ") + item.permission;
    case "plan_step_done": return "Completed plan step " + ((item.step_index ?? 0) + 1);
    case "plan_deviation": return "Approved plan deviation";
    case "run_finished": return "Calculation recorded";
    case "run_failed": return "Calculation failed · " + item.error;
    case "model_route_fallback": return item.role+" fallback · "+item.previous+" → "+item.next_model;
    case "subtask_started": return "Subtask started · "+item.question;
    case "subtask_finished": return "Subtask "+item.task_id+" · "+item.outcome+" · "+item.input_tokens+" input / "+item.output_tokens+" output tokens";
    case "run_cancelled": return "Calculation interrupted";
    case "run_timed_out": return "Calculation timed out";
    case "rerun_started": return "Rerun started";
    case "rerun_finished": return "Rerun comparison recorded";
    case "replication_started": return "Independent replication started";
    case "replication_finished": return "Replication · " + item.outcome;
    case "bundle_exported": return "Research bundle saved";
    case "literature_started": return "Literature lookup · " + item.question;
    case "literature_finished": return "Literature lookup · " + item.outcome +
      (item.reason ? " · " + item.reason : "") +
      " · " + ((item.input_tokens ?? 0) + (item.output_tokens ?? 0)) + " tokens";
    case "review_started": return "Review started";
    case "review_finished": return (item.limitations?.length ? "Review incomplete · " : "Review finished · ") + item.finding_count + " findings";
    case "error": return item.where + " · " + item.error;
    case "exploration_stopped": return "Exploration stopped · " + item.reason;
    default: return item.type;
  }
}

export function ActivityFeed({currentState,quote,items, connection, progress}: {
  currentState: string; quote?: (ref:TurnRef) => void; items: Snapshot["activity"]; connection: string; progress: string;
}) {
  return <aside className="ledger" aria-label="Research activity">
    <header><h2>Activity</h2><span id="connection" className="muted">{connection === "Connected" ? currentState : connection}</span></header>
    <p id="progress" role="status" className="muted">{progress}</p>
    <ol id="activity">{[...items].reverse().map(item => <li key={item.seq} data-ref-kind="activity" data-ref-id={item.id}>
      {activityLabel(item)}<time dateTime={item.time} className="muted">
        {new Date(item.time).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"})}
      </time>{item.run_id && quote && <button type="button" className="quiet" onClick={() => quote({kind:"run",id:item.run_id!})}>Quote calculation</button>}
    </li>)}</ol>
    {!items.length && <p className="empty-note">Events appear here when research begins.</p>}
  </aside>;
}
