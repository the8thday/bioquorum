import { SelectedText } from "./References";
import { Fragment } from "react";
import { figurePath, request } from "./api";
import { Dialog } from "./Dialog";
import type { TurnRef, EvidenceRef } from "./types";

interface Source {source: string; tool: string; task_id?: string; scope?: string}
interface Run {
  values: {name: string; value: string | number; unit?: string}[];
  previous_cells?: {code: string}[]; code: string; outputs: {text: string}[];
  figures: string[]; inputs: {content_hash: string; path?: string; uri: string}[];
  code_hash: string; stdout_hash?: string; isolation?: string;
  lineage?: {direction: string; function: string; path: string; post_run_hash?: string}[];
}
type Evidence = {kind: "quote"; title: string; quote: string; data: Source}
  | {kind: "value"; title: string; data: Run};

export async function readEvidence(base: string, answer: string, ref: EvidenceRef): Promise<Evidence> {
  const path = base + "/answers/" + answer + "/" + ref.type + "/";
  return ref.type === "quote"
    ? {kind: "quote", title: ref.source, quote: ref.quote, data: await request<Source>(path + ref.call_id)}
    : {kind: "value", title: ref.name, data: await request<Run>(path + ref.run_id)};
}

export function Highlight({source, quote}: {source: string; quote: string}) {
  const escape = (term: string) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(quote.split(/\s+/).map(escape).join("\\s+")).exec(source);
  return <pre>{match
    ? <>{source.slice(0, match.index)}<mark>{match[0]}</mark>{source.slice(match.index + match[0].length)}</>
    : <>Verified quote from decoded source: <mark>{quote}</mark>{"\n\n" + source}</>}</pre>;
}

function Calculation({data, name, base, openFigure}: {data: Run; name: string; base: string;openFigure?:(hash:string)=>void}) {
  const value = data.values.find(v => v.name === name);
  const sections = [
    ["Input hashes", data.inputs.map(i => i.content_hash + "\n" + (i.path || i.uri)).join("\n\n") || "No file inputs declared."],
    ["Code hash", data.code_hash], ["Output hash", data.stdout_hash],
    ["Observed I/O (post-run)", data.lineage?.map(i => i.direction + " · " + i.function + " · " + i.path +
      (i.post_run_hash ? "\n" + i.post_run_hash : "")).join("\n")],
  ];
  return <>
    {value && <p>{value.value}{value.unit && " " + value.unit}</p>}
    {!!data.previous_cells?.length && <><h3>Earlier cells in this calculation</h3><pre>{data.previous_cells.map(c => c.code).join("\n\n")}</pre></>}
    <h3>Code</h3><pre>{data.code}</pre>
    <h3>Output</h3><pre>{data.outputs.map(o => o.text).join("\n") || "No text output recorded."}</pre>
    {!!data.figures.length && <><h3>Figures</h3>{data.figures.map((hash,i) =>
      <button className="figure-trigger" key={i} onClick={()=>openFigure?.(hash)} aria-label="Open calculation figure"><img src={figurePath(base, hash)} alt="Figure from this calculation" loading="lazy"/></button>)}</>}
    {sections.map(([title, text]) => text && <Fragment key={title}><h3>{title}</h3><pre>{text}</pre></Fragment>)}
    {data.isolation && <p className="muted">Kernel isolation: {data.isolation}</p>}
  </>;
}

export function EvidenceDialog({evidence, base, close, item, add,openFigure}: {openFigure:(hash:string)=>void;item?: TurnRef; add?: (ref:TurnRef, visibleText?:string, note?:string) => boolean; evidence: Evidence; base: string; close: () => void}) {
  return <Dialog id="evidence" title={evidence.title} kicker="Evidence" close={close} closeLabel="Close evidence">
    <div id="evidence-body">{evidence.kind === "value"
      ? <Calculation data={evidence.data} name={evidence.title} base={base} openFigure={openFigure}/>
      : <>{/^PMID:\d+$/.test(evidence.title) && <a href={"https://pubmed.ncbi.nlm.nih.gov/" + evidence.title.slice(5) + "/"}
          target="_blank" rel="noopener noreferrer">Open full record in PubMed</a>}
        {evidence.data.task_id && <p className="muted">{evidence.data.scope || "Retrieved by a literature subtask"}</p>}
        <h3>Recorded source · {evidence.data.tool}</h3><div data-ref-kind="source" data-ref-id={item?.id}><Highlight source={evidence.data.source} quote={evidence.quote}/></div></>}
    </div>
    {item && add && <><SelectedText add={add}/><button className="quiet" onClick={() => add(item)}>Quote {item.kind} in question</button></>}
  </Dialog>;
}
