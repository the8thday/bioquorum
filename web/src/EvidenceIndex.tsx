import type { Answer, EvidenceRef } from "./types";

export function EvidenceIndex({answers,open}: {answers:Answer[];open:(id:string,ref:EvidenceRef)=>void}) {
  const items = new Map<string,{id:string;ref:EvidenceRef;text:string}>();
  for (const answer of answers) {
    for (const row of [...answer.paragraphs,...answer.claims]) {
      for (const s of row.segments) {
        if (!s.ref) continue;
        const ref=s.ref;
        const text=ref.type==="value" ? ref.name+" · "+s.text : ref.source+" · "+ref.quote;
        items.set(JSON.stringify(ref),{id:answer.id,ref,text});
      }
    }
  }
  return <section className="ledger"><h2>Evidence</h2>
    {items.size===0 ? <p>Calculations and sources appear when an answer binds to a recorded result.</p> :
      <ul className="evidence-index">{[...items.entries()].map(([key,item]) =>
        <li key={key}><button className="quiet" onClick={() => open(item.id,item.ref)}>{item.text}</button></li>)}</ul>}
  </section>;
}
