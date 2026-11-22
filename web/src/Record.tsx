import { useState } from "react";
import { inlineText, type ArtifactResolver } from "./InlineText";
import { BrandMark } from "./BrandMark";
import { Coverage } from "./Coverage";
import { Icon } from "./Icon";
import { ResultTable, tableAt, type AnswerTable } from "./ResultTables";
import type { Product } from "./Artifacts";
import type { TurnRef, Answer, Claim, EvidenceRef, Segment, Turn, Verification } from "./types";

const labels: Record<string, string> = {
  proposed: "Awaiting approval", approved: "Approved", declined: "Declined",
  running: "Running", queued: "Queued", completed: "Completed", failed: "Failed",
  interrupted: "Interrupted", unverified: "Assertion only", grounded: "Grounded",
  reviewed: "Reviewed", reproduced: "Reproduced", replicated: "Independently replicated",
  disputed: "Disputed",
};

const statusTips: Record<string, string> = {
  unverified: "No verified evidence is linked to this statement.",
  grounded: "Linked directly to a recorded calculation output or verbatim source quote.",
  reviewed: "Assessed by the reviewer against its linked evidence.",
  reproduced: "Original calculation code was rerun in an isolated environment with matching output.",
  replicated: "An independent analysis without seeing the original answer reached a matching value.",
  disputed: "A review check or verification found a conflict with the recorded evidence.",
};

function Badge({status}: {status: string}) {
  return <span title={statusTips[status]} className={"badge " + status}>{labels[status] ?? status}</span>;
}

export function TurnCard({turn, animated, waiting, reopen}: {reopen?:(r:TurnRef)=>void; turn: Turn; animated?: boolean; waiting?: boolean}) {
  const {plan} = turn;
  return <article className="panel turn" id={turn.id && "record-"+turn.id}>
    <span className="kicker">You</span><p className="question">{turn.question}</p>
    {turn.selections?.map((r,i)=><button className="result-link" key={i} onClick={()=>reopen?.(r)}>Reopen {r.selection?.type} selection</button>)}
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
  onReview: (answer: string, online?: boolean) => void;
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

type AnswerProps = Actions & {
  onlineReviewAvailable?: boolean;
  discuss?: (ref: TurnRef) => void;
  openTable?: (item: AnswerTable) => void;
  resolveArtifact?: ArtifactResolver;
  turnProducts?: Product[];
  openProduct?: (p: Product) => void;
  base?: string;
  answer: Answer;
  example: boolean;
  verification: Verification[];
};
const rawText = (segments: Segment[]) => segments.map(segment => segment.raw ?? segment.text).join("").trim();

function problemLabel(text: string) {
  const quote = text.match(/^\[@([^\s\]]+)/);
  if (quote) return `[${quote[1]} · unverified]`;
  if (text.startsWith("[=")) return "[value · unverified]";
  return null;
}

function ClaimRow({addReference, parts, claim, sharedStatus, answer, example, verification, onEvidence, onVerify, resolveArtifact}:
  Omit<AnswerProps, "onReview"> & {parts: Segment[]; claim?: Claim; sharedStatus?: boolean}) {
  const status = claim?.status ?? "unverified";
  const canVerify = claim?.has_calculation && !answer.inherited && !example &&
    ["reviewed", "reproduced", "replicated"].includes(status);
  return <div className={"claim " + status} data-claim-id={claim?.id}>
    <p data-ref-kind="answer" data-ref-id={answer.id}>{parts.map(({ref, text, problem}, i) => ref
      ? <button key={i} type="button" className={"evidence-link" + (ref.type === "quote" ? " quote-link" : "")} onClick={() => onEvidence(answer.id, ref)}
          title={ref.type === "quote" ? "Open cited source" : "Open calculation for " + ref.name}>
          {ref.type === "quote" ? <><span hidden>{text.slice(0,-("["+ref.source+"]").length)}</span>{"[" + ref.source + "]"}</> : text}</button>
      : problem
        ? (() => {
            const short = problemLabel(text);
            return <span key={i} className="segment binding-problem" title={problem + "\n" + text}>
              {short ? <><span hidden>{text}</span>{short}</> : inlineText(text, resolveArtifact)}</span>;
          })()
        : <span key={i} className="segment">{inlineText(text, resolveArtifact)}</span>)}</p>
    {!sharedStatus && <Badge status={status}/>}
    {claim?.needs_review && <span className="review-notice">Needs review</span>}
    {claim?.replacement_claim_id && claim.replacement_session_id && <a className="replacement-link"
      href={"?session="+encodeURIComponent(claim.replacement_session_id)+"#claim-"+encodeURIComponent(claim.replacement_claim_id)}>View corrected claim</a>}
    {claim && addReference && <button className="quiet" onClick={() => addReference({kind:"claim",id:claim.id})}>Quote claim</button>}
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
  const [onlineReview, setOnlineReview] = useState(false);
  const {answer} = props;
  const rows = answer.paragraphs.map(p => {
    const text = rawText(p.segments).replace(/^(?:[-*•]|\d+[.)])\s+/, "");
    return {p, claim: answer.claims.find(c => rawText(c.segments) === text)};
  });
  const matched = new Set(rows.map(row => row.claim));
  const additional = answer.claims.filter(c => !matched.has(c));
  const sharedStatus = rows.length > 1 && rows.every(({claim}) => !claim);
  const notes = rows.flatMap(({p}, index) => p.unbound.length ? [{index, values: p.unbound}] : []);
  const body = [];
  const lines = answer.paragraphs.map(p=>p.segments);
  let tableCount=0;
  function claimRow(i:number) {
    const {p,claim}=rows[i];
    return <div key={i}><ClaimRow {...props} parts={p.segments} sharedStatus={sharedStatus} claim={claim}/>
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
  return <article className="panel answer" id={"record-"+answer.id}><header className="answer-author"><BrandMark className="answer-mark"/><h2>Findings</h2></header>
    <p className="muted answer-meta"><span>{answer.model ?? "Model not recorded"}</span>{answer.reasoning_effort && <> · <span className="effort-tag">{answer.reasoning_effort} reasoning</span></>}</p>
    {body}
    {props.turnProducts && props.turnProducts.length > 0 && (
      <div className="result-links answer-artifacts" aria-label="Research results" role="group">
        {props.turnProducts.map((p) => (
          <div key={p.id} className="result-chip">
            <button
              type="button"
              className="result-link"
              aria-label={"Open " + p.name}
              title={p.preview + " · v" + p.version}
              onClick={() => props.openProduct?.(p)}
            >
              <Icon name={p.preview === "table" ? "table" : p.preview === "figure" ? "image" : "document"} />
              <span>{p.name}</span>
            </button>
            {props.base && (
              <a
                className="icon-button result-download"
                href={props.base + "/artifacts/" + encodeURIComponent(p.id) + "/file"}
                download
                aria-label={"Download " + p.name}
                title={"Download " + p.name}
              >
                <Icon name="download" />
              </a>
            )}
          </div>
        ))}
      </div>
    )}
    {answer.first_pass && !!answer.missing_sections?.length && <p className="muted">Sections not reported · {answer.missing_sections.join(", ")}</p>}
    {props.discuss && Object.entries(answer.discussion_items ?? {}).map(([label,excerpt])=>
      <button className="quiet" key={label} onClick={()=>props.discuss?.({kind:"answer",id:answer.id,excerpt})}>Discuss {label.toLowerCase()}</button>)}
    {answer.coverage && <Coverage ledger={answer.coverage}/>}
    {answer.clarification && <aside id={"record-"+answer.clarification.id}><strong>{answer.clarification.question}</strong>
      <p>Working assumption · {answer.clarification.assumption}</p>
      {props.discuss && <button className="quiet" onClick={()=>props.discuss?.({
        kind:"activity",id:answer.clarification!.id})}>Answer this question</button>}</aside>}
    {sharedStatus && <Badge status="unverified"/>}
    {answer.review_completed && !answer.review_limitations.length && <p className="muted review-result">Review finished · {answer.claims.length} {answer.claims.length === 1 ? "claim" : "claims"} assessed</p>}
    {answer.review_limitations.length > 0 && <details className="review-limits"><summary>Review incomplete</summary>{answer.review_limitations.map((reason, i) => <p key={i}>{reason}</p>)}</details>}
    {notes.length > 0 && <details className="claims"><summary>Unbound values or citations · {notes.length} {notes.length === 1 ? "paragraph" : "paragraphs"}</summary>
      {notes.map(({index, values}) => <p key={index}>Paragraph {index + 1} · {values.join(", ")}</p>)}</details>}
    {additional.length > 0 && <details className="claims"><summary>Review by claim</summary>
      {additional.map(c => <ClaimRow key={c.id} {...props} parts={c.segments} claim={c}/>)}</details>}
    {!answer.inherited && !props.example && <div className="actions">
      <button className="quiet" onClick={() => props.onReview(answer.id, !!props.onlineReviewAvailable && onlineReview)}>Run review</button>
      {props.onlineReviewAvailable && <label className="checkbox"><input type="checkbox" checked={onlineReview}
        onChange={e=>setOnlineReview(e.target.checked)}/> Check recorded PMIDs and DOIs online</label>}
    </div>}
  </article>;
}
