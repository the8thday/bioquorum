import { Coverage } from "./Coverage";
import { ResultTable, tableAt, type AnswerTable } from "./ResultTables";
import type { TurnRef, Answer, Claim, EvidenceRef, Segment, Turn, Verification } from "./types";

const labels: Record<string, string> = {
  proposed: "Awaiting approval", approved: "Approved", declined: "Declined",
  running: "Running", queued: "Queued", completed: "Completed", failed: "Failed",
  interrupted: "Interrupted", unverified: "Assertion only", grounded: "Grounded",
  reviewed: "Reviewed", reproduced: "Reproduced", replicated: "Independently replicated",
  disputed: "Disputed",
};

function Badge({status}: {status: string}) {
  return <span title={status === "unverified" ? "No verified evidence is linked to this statement." : undefined} className={"badge " + status}>{labels[status] ?? status}</span>;
}

export function TurnCard({turn, animated, waiting}: {turn: Turn; animated?: boolean; waiting?: boolean}) {
  const {plan} = turn;
  return <article className="panel turn">
    <span className="kicker">You</span><p className="question">{turn.question}</p>
    {turn.status === "running" && (animated || waiting)
      ? <span className={"badge running" + (animated ? " running-indicator" : "")}>{waiting ? "Awaiting permission" : "Running"}</span>
      : turn.status && <Badge status={turn.status}/>}
    {turn.error && <p className="error">{turn.error}</p>}
    {plan && <section className="plan">
      <h2>Analysis plan <Badge status={plan.status}/></h2>
      <ol>{plan.steps.map((step, i) => <li key={i} className={plan.done.includes(i) ? "done" : undefined}>
        {plan.done.includes(i) && "Completed · "}{step}</li>)}</ol>
      {turn.status === "completed" && plan.status === "approved" && plan.done.length !== plan.steps.length &&
        <p className="warning">Some plan steps were not reported complete.</p>}
      {plan.reason && <p>{plan.reason}</p>}
      {plan.deviations.map((d, i) => <p className="warning" key={i}>Approved change: {d.change} · {d.reason}</p>)}
    </section>}
  </article>;
}

type Actions = {
  addReference?: (ref: TurnRef) => void;
  onEvidence: (answer: string, ref: EvidenceRef) => void;
  onReview: (answer: string) => void;
  onVerify: (claim: string, kind: "rerun" | "replication") => void;
};

function VerificationResult({entry}: {entry: Verification}) {
  const rerun = entry.type === "rerun_finished";
  const readings = rerun ? entry.result.runs.flatMap(run => run.values ?? []) : entry.result.comparisons;
  const display = (value: string | number | null, unit?: string) =>
    value == null ? "Unavailable" : String(value) + (unit ? " " + unit : "");
  return <details className="verification">
    <summary>{rerun ? "Rerun" : "Independent replication"} · {entry.result.outcome}</summary>
    {rerun && !entry.result.status_eligible && entry.result.outcome !== "inconclusive" &&
      <p className="muted">Local comparison. A locked, isolated run is required for a status upgrade.</p>}
    {rerun && entry.result.runs.map((run, i) => run.reason && <p key={i} className="muted">{run.reason}</p>)}
    {entry.result.note && <p className="muted">{entry.result.note}</p>}
    {readings.length > 0 && <table><thead><tr>{["Result", "Recorded", "New", "Comparison"].map(label =>
      <th key={label}>{label}</th>)}</tr></thead><tbody>{readings.map((v, i) => <tr key={i}>
        <td>{v.name}</td><td>{display(v.original, v.unit)}</td><td>{display(v.observed, v.observed_unit)}</td>
        <td>{v.matched == null ? "Inconclusive" : v.matched ? "Match" : "Different"}</td>
      </tr>)}</tbody></table>}
  </details>;
}

type AnswerProps = Actions & {discuss?:(ref:TurnRef)=>void; openTable?:(item:AnswerTable)=>void; answer: Answer; example: boolean; verification: Verification[]};
const rawText = (segments: Segment[]) => segments.map(segment => segment.raw ?? segment.text).join("").trim();

function inlineText(text: string) {
  return text.split(/(`[^`\n]+`|\*\*[^*\n]+\*\*)/g).map((part, i) => {
    if (i % 2 === 0) return part;
    return part.startsWith("`") ? <span key={i}><span hidden>`</span><code>{part.slice(1,-1)}</code><span hidden>`</span></span> :
      <span key={i}><span hidden>**</span><strong>{part.slice(2,-2)}</strong><span hidden>**</span></span>;
  });
}

function ClaimRow({addReference, parts, claim, unbound, sharedStatus, answer, example, verification, onEvidence, onVerify}:
  Omit<AnswerProps, "onReview"> & {parts: Segment[]; claim?: Claim; unbound?: string[]; sharedStatus?: boolean}) {
  const status = claim?.status ?? "unverified";
  const canVerify = claim?.has_calculation && !answer.inherited && !example &&
    ["reviewed", "reproduced", "replicated"].includes(status);
  return <div className={"claim " + status} data-claim-id={claim?.id}>
    <p data-ref-kind="answer" data-ref-id={answer.id}>{parts.map(({ref, text, problem}, i) => ref
      ? <button key={i} type="button" className="evidence-link" onClick={() => onEvidence(answer.id, ref)}
          title={ref.type === "quote" ? "Open cited source" : "Open calculation for " + ref.name}>
          {ref.type === "quote" ? <><span hidden>{text.slice(0,-("["+ref.source+"]").length)}</span>{"[" + ref.source + "]"}</> : text}</button>
      : <span key={i} className={"segment" + (problem ? " error" : "")} title={problem}>{inlineText(text)}</span>)}</p>
    {!sharedStatus && <Badge status={status}/>}
    {claim?.needs_review && <span className="review-notice">Needs review</span>}
    {claim?.replacement_claim_id && claim.replacement_session_id && <a className="replacement-link"
      href={"?session="+encodeURIComponent(claim.replacement_session_id)+"#claim-"+encodeURIComponent(claim.replacement_claim_id)}>View corrected claim</a>}
    {claim && addReference && <button className="quiet" onClick={() => addReference({kind:"claim",id:claim.id})}>Quote claim</button>}
    {!!unbound?.length && <span className="muted" title={unbound.join(", ")}> · Unbound values or citations</span>}
    {claim?.findings.map((f, i) => <p key={i} className={"muted " + (f.severity === "error" ? "error" : "warning")}>
      {f.check} · {f.evidence}</p>)}
    {claim && verification.filter(entry => entry.result.claim_id === claim.id).map((entry, i) =>
      <VerificationResult key={i} entry={entry}/>)}
    {canVerify && <div className="actions">
      <button className="quiet" onClick={() => onVerify(claim.id, "rerun")}>Rerun calculation</button>
      {status !== "reviewed" && <button className="quiet" onClick={() => onVerify(claim.id, "replication")}>Independent replication</button>}
    </div>}
  </div>;
}

export function AnswerCard(props: AnswerProps) {
  const {answer} = props;
  const rows = answer.paragraphs.map(p => {
    const text = rawText(p.segments).replace(/^(?:[-*•]|\d+[.)])\s+/, "");
    return {p, claim: answer.claims.find(c => rawText(c.segments) === text)};
  });
  const matched = new Set(rows.map(row => row.claim));
  const additional = answer.claims.filter(c => !matched.has(c));
  const sharedStatus = rows.length > 1 && rows.every(({claim}) => !claim);
  const body = [];
  const lines = answer.paragraphs.map(p=>p.segments);
  let tableCount=0;
  function claimRow(i:number) {
    const {p,claim}=rows[i];
    return <div key={i}><ClaimRow {...props} parts={p.segments} sharedStatus={sharedStatus} claim={claim} unbound={p.unbound}/>
      {p.unscoped_absence && <p className="warning">Assertion only · No matching scoped query supports this absence claim.</p>}
      </div>;
  }
  for (let i=0;i<rows.length;i++) {
    const cells = tableAt(lines,i);
    if (!cells) {body.push(claimRow(i));continue;}
    tableCount++;
    const item:AnswerTable = {id:answer.id+"-table-"+i,name:"Answer table "+tableCount,answer:answer.id,cells};
    const end=i+cells.length;
    body.push(<section className="answer-table" key={i}>
      {props.openTable && <button className="result-link" onClick={()=>props.openTable?.(item)}>Open {item.name.toLowerCase()} ↗</button>}
      <div className="inline-table-preview" onClick={()=>{if(!window.getSelection()?.toString()) props.openTable?.(item);}}>
        <ResultTable cells={cells} onEvidence={s=>{if(s.ref)props.onEvidence(answer.id,s.ref);}}/></div>
      <details><summary>Original rows and evidence status</summary>{rows.slice(i,end+1).map((_,j)=>claimRow(i+j))}</details>
    </section>);
    i=end;
  }
  return <article className="panel answer"><header className="answer-author"><span className="answer-mark" aria-hidden="true">Q</span><h2>Findings</h2></header>
    <p className="muted">{answer.model ?? "Model not recorded"}</p>
    {body}
    {answer.first_pass && !!answer.missing_sections?.length && <p className="muted">Sections not reported · {answer.missing_sections.join(", ")}</p>}
    {props.discuss && Object.entries(answer.discussion_items ?? {}).map(([label,excerpt])=>
      <button className="quiet" key={label} onClick={()=>props.discuss?.({kind:"answer",id:answer.id,excerpt})}>Discuss {label.toLowerCase()}</button>)}
    {answer.coverage && <Coverage ledger={answer.coverage}/>}
    {answer.clarification && <aside><strong>{answer.clarification.question}</strong>
      <p>Working assumption · {answer.clarification.assumption}</p>
      {props.discuss && <button className="quiet" onClick={()=>props.discuss?.({
        kind:"activity",id:answer.clarification!.id})}>Answer this question</button>}</aside>}
    {sharedStatus && <Badge status="unverified"/>}
    {answer.review_completed && !answer.review_limitations.length && <p className="muted review-result">Review finished · {answer.claims.length} {answer.claims.length === 1 ? "claim" : "claims"} assessed</p>}
    {answer.review_limitations.length > 0 && <details className="review-limits"><summary>Review incomplete</summary>{answer.review_limitations.map((reason, i) => <p key={i}>{reason}</p>)}</details>}
    {additional.length > 0 && <details className="claims"><summary>Review by claim</summary>
      {additional.map(c => <ClaimRow key={c.id} {...props} parts={c.segments} claim={c}/>)}</details>}
    {!answer.inherited && !props.example && <div className="actions">
      <button className="quiet" onClick={() => props.onReview(answer.id)}>Run review</button>
    </div>}
  </article>;
}
