import { useEffect, useState } from "react";
import { Dialog } from "./Dialog";
import { errorMessage, request } from "./api";

interface Totals {
  calls: number; input_tokens: number; output_tokens: number; cache_read_tokens: number;
  cache_write_tokens: number; latency_ms: number; known_usd: number; unknown_prices: number; zero_prices: number;
}
interface Scope {total: Totals; roles: Record<string, Totals>}
type Limits = Record<"tokens" | "usd" | "seconds", number | null>;
interface Usage {turn: Scope; session: Scope; project: Scope; budget: {limits: Limits; remaining: Limits}}

export function costLabel(v: Totals) {
  if (!v.calls) return "—";
  if (v.unknown_prices === v.calls) return "Price unavailable";
  const price = "$" + v.known_usd.toFixed(6);
  return v.unknown_prices ? price + " + " + v.unknown_prices + " unpriced" : price;
}

const number = (n: number) => n.toLocaleString("en-US");
function Breakdown({value}: {value: Totals}) {
  const fields = [["Input tokens", number(value.input_tokens)], ["Output tokens", number(value.output_tokens)],
    ["Cache read", number(value.cache_read_tokens)], ["Cache write", number(value.cache_write_tokens)],
    ["Model time", (value.latency_ms / 1000).toFixed(2) + " s"], ["Estimated cost", costLabel(value)]];
  return <dl className="metric-list">{fields.map(([label,amount]) =>
    <div key={label}><dt>{label}</dt><dd>{amount}</dd></div>)}</dl>;
}

export function UsageMetrics({scope}: {scope: Scope}) {
  const {total, roles} = scope;
  const tokens = total.input_tokens + total.output_tokens;
  const input = tokens ? total.input_tokens / tokens * 100 : 0;
  const percent = Math.round(input);
  return <div className="usage-metrics">
    <div className="usage-count"><span>Completed model calls</span><strong>{number(total.calls)}</strong></div>
    {tokens > 0 && <figure className="token-chart"><figcaption>Token mix</figcaption>
      <svg viewBox="0 0 100 8" preserveAspectRatio="none" role="img"
        aria-label={`Input tokens, ${percent}%. Output tokens, ${100-percent}%.`}>
        <rect width="100" height="8" className="chart-output"/><rect width={input} height="8" className="chart-input"/>
      </svg><div className="chart-key"><span>Input {percent}%</span><span>Output {100-percent}%</span></div>
    </figure>}
    <Breakdown value={total}/>
    {Object.keys(roles).length > 0 && <section className="role-section"><h3>By role</h3>
      {Object.entries(roles).map(([role,value]) => <details className="role-detail" key={role}>
        <summary><span>{role}</span><small>{number(value.calls)} {value.calls === 1 ? "call" : "calls"}</small></summary>
        <Breakdown value={value}/></details>)}</section>}
  </div>;
}

export function UsageDialog({inline,base, seq, close}: {inline?:boolean; base: string; seq: number; close: () => void}) {
  const [data, setData] = useState<Usage>();
  const [error, setError] = useState("");
  const [scope, setScope] = useState<"turn" | "session" | "project">("turn");
  useEffect(() => {
    const controller = new AbortController();
    request<Usage>(base + "/usage", undefined, controller.signal).then(value => { setData(value); setError(""); })
      .catch(e => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    return () => controller.abort();
  }, [base, seq]);
  return <Dialog inline={inline} id="usage-dialog" title="Model usage" close={close} closeLabel="Close model usage">
    <label className="scope-field">Scope <select value={scope} onChange={e => setScope(e.target.value as typeof scope)}>
      <option value="turn">Latest turn</option><option value="session">This session</option><option value="project">Project</option>
    </select></label>
    <p><a href="https://chatgpt.com/settings/usage" target="_blank" rel="noreferrer">Manage ChatGPT plan usage</a></p>
    {error && <p role="status" className="error">{error}</p>}
    {!data && !error && <p>Loading usage…</p>}
    {data && <><UsageMetrics scope={data[scope]}/>
      <div className="budget"><h3>Latest turn budget</h3>
      {Object.values(data.budget.limits).every(n => n === null) ? <p className="muted">No limits configured</p> :
      (Object.keys(data.budget.limits) as (keyof Limits)[]).map(k => {
        const limit = data.budget.limits[k], left = data.budget.remaining[k];
        return <p key={k}>{k} · {limit === null ? "No limit configured" :
          (left === null ? "Remaining unknown" : Number(left.toFixed(6)) + " remaining") + " / " + limit}</p>;
      })}</div>
    </>}
  </Dialog>;
}
