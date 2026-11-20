import { useEffect, useState } from "react";
import { request, errorMessage } from "./api";
import { Dialog } from "./Dialog";

interface ContextInfo {
  model: string | null; window: number | null; estimated_tokens: number;
  message_counts?: Record<string,number>; skills: string[]; notes: number; sources: number;
  summaries: {from_seq: number; to_seq: number; summary: string; summarizer_model: string}[];
}
export function ContextDialog({inline,base, worker, seq, example, active, close, fork}: {
  inline?:boolean; base: string; worker: string; seq: number; example: boolean; active: boolean;
  close: () => void; fork: () => void;
}) {
  const path = base + "/context" + (worker ? "?worker=" + encodeURIComponent(worker) : "");
  const [data, setData] = useState<ContextInfo>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const c = new AbortController();
    request<ContextInfo>(path, undefined, c.signal).then(setData)
      .catch(e => {if (!c.signal.aborted) setError(errorMessage(e));});
    return () => c.abort();
  }, [path, seq]);
  async function compact() {
    setBusy(true); setError("");
    try { setData(await request<ContextInfo>(path, {})); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  const counts = Object.entries(data?.message_counts ?? {});
  const maxMessages = Math.max(1,...counts.map(([,n]) => n));
  return <Dialog inline={inline} id="context-dialog" title="Model context" closeLabel="Close context" close={close}>
    {!data && !error && <p>Loading context…</p>}
    {data && <><p>{data.estimated_tokens.toLocaleString()} estimated conversation tokens
      {data.window && " / " + data.window.toLocaleString() + " token window"}{data.model && " · " + data.model}</p>
      {data.window != null && data.window > 0 ? <div className="window-chart">
        <label>Estimated window use <strong>{(100*data.estimated_tokens/data.window).toLocaleString(undefined,{maximumFractionDigits:1})}%</strong>
        <progress max={data.window} value={Math.min(data.estimated_tokens,data.window)} /></label>
      </div> : <p className="muted">Window size is unknown.</p>}
      {counts.length > 0 && <figure className="message-chart">
        <figcaption>Messages in current context</figcaption>
        {counts.map(([role,count]) => <div key={role}>
          <span>{role}</span><meter aria-label={role + " message count"} min={0} max={maxMessages} value={count}/><strong>{count}</strong>
        </div>)}</figure>}
      <p className="muted">Estimate uses four UTF-8 bytes per token in the recorded conversation. Instructions, tool schemas and provider overhead can change actual usage.</p>
      <p>Loaded skills: {data.skills.join(", ") || "None"} · Note revisions: {data.notes} · Recorded sources: {data.sources}</p>
      <h3>Compacted history</h3>
      {data.summaries.length ? data.summaries.map((s,i) => <details key={i}><summary>Events {s.from_seq}–{s.to_seq} · {s.summarizer_model}</summary><p>{s.summary}</p></details>)
        : <p>No saved summaries.</p>}
      <p>Compaction changes the conversation sent to the model. Evidence links keep using the original research record.</p>
      <div className="actions"><button className="primary" disabled={busy || active || example} onClick={compact}>
        {busy ? "Compacting…" : "Compact now"}</button>
        <button className="quiet" disabled={active || busy} onClick={fork}>Fork from here</button></div>
      {example && <p className="muted">The offline example has no recorded summarizer response.</p>}</>}
    {error && <p className="error" role="status">{error}</p>}
  </Dialog>;
}
