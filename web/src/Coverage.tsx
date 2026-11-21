import type { CoverageLedger } from "./types";

export function Coverage({ledger}:{ledger:CoverageLedger}) {
  const topics: [string | null,string][] = Object.entries(ledger.topics);
  if (ledger.queries.some(q=>!q.topic)) topics.push([null,"Unassigned queries"]);
  return <details className="coverage-ledger"><summary>Evidence coverage · {ledger.closed ? "closed" : "open"}</summary>
    {topics.map(([key, label]) => {
      const queries = ledger.queries.filter(q=>q.topic===key);
      return <section key={key ?? "_unassigned"}>{key && <strong>{key} · </strong>}{label}
        {queries.length ? <ul>{queries.map(q=><li key={q.call_id}>
          <code>[{q.id}] {q.tool}</code> · {Object.entries(q.scope).map(([k,v])=>k+"="+JSON.stringify(v)).join(", ")}
          <br/><small><time dateTime={q.time}>{q.time}</time> · {q.status==="empty" ? "No records in this search"
            : q.status==="failed" ? "Failed: "+q.reason : q.status==="pending" ? "No result recorded yet"
            : q.count==null ? "Returned · number of records unknown" : q.count+" returned"}</small>
        </li>)}</ul> : <p>No query assigned</p>}
      </section>;
    })}
    {!topics.length && <p>No evidence categories or searches recorded yet.</p>}
    {ledger.gaps.length>0 && <><strong>Remaining gaps</strong><ul>{ledger.gaps.map((g,i)=><li key={i}>{g}</li>)}</ul></>}
    {ledger.decisive && <p><strong>What could change the judgment</strong> · {ledger.decisive}</p>}
  </details>;
}
