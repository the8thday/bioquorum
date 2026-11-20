import type { Segment } from "./types";

export type Cells = Segment[][][];
export interface AnswerTable {id:string; name:string; answer:string; cells:Cells}

function columns(line: Segment[]): Segment[][] {
  const cells: Segment[][] = [[]];
  for (const segment of line) {
    if (segment.ref || segment.problem) {cells.at(-1)!.push(segment);continue;}
    const pieces = segment.text.split(/(?<!\\)\|/);
    pieces.forEach((text,index)=>{
      if (index) cells.push([]);
      if (text) cells.at(-1)!.push({text:text.replace(/\\\|/g,"|")});
    });
  }
  const blank = (cell:Segment[]) => cell.every(s=>!s.text.trim());
  if (cells.length>1 && blank(cells[0])) cells.shift();
  if (cells.length>1 && blank(cells.at(-1)!)) cells.pop();
  return cells;
}

export function tableAt(lines: Segment[][], start:number): Cells | null {
  const divider = lines[start+1];
  if (!divider || !divider.some(s=>s.text.includes("|"))) return null;
  const rule = columns(divider);
  if (!rule.every(cell=>/^:?-{3,}:?$/.test(cell.map(s=>s.text).join("").trim()))) return null;
  const header = columns(lines[start]);
  if (rule.length!==header.length || !lines[start].some(s=>s.text.includes("|"))) return null;
  const rows = [header];
  for (let i=start+2;i<lines.length && lines[i].some(s=>s.text.includes("|"));i++) {
    const cells=columns(lines[i]);
    if (cells.length!==header.length) break;
    rows.push(cells);
  }
  return rows;
}

export function ResultTable({cells,onEvidence}: {cells:Cells;onEvidence?:(s:Segment)=>void}) {
  function row(parts:Segment[][], heading:boolean) {
    const Cell = heading ? "th" : "td";
    return parts.map((cell,i)=><Cell key={i} scope={heading?"col":undefined}>{cell.map((s,j)=>s.ref && onEvidence
      ? <button key={j} className="evidence-link" onClick={e=>{e.stopPropagation();onEvidence(s);}}>{s.text}</button>
      : <span key={j} className={s.problem?"error":undefined} title={s.problem}>{s.text}</span>)}</Cell>);
  }
  return <div className="table-scroll"><table><thead><tr>{row(cells[0],true)}</tr></thead>
    <tbody>{cells.slice(1).map((r,i)=><tr key={i}>{row(r,false)}</tr>)}</tbody></table></div>;
}
