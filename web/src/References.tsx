import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { TurnRef } from "./types";

export function selectionRef(selection: Selection | null): TurnRef | null {
  if (!selection?.rangeCount || selection.isCollapsed || !selection.toString().trim()) return null;
  const range = selection.getRangeAt(0);
  const container = range.commonAncestorContainer;
  const el = (container instanceof Element ? container : container.parentElement)?.closest<HTMLElement>("[data-ref-kind]");
  if (!el?.dataset.refId) return null;
  const {refKind,refId} = el.dataset;
  if (refKind === "activity") return {kind:"activity",id:refId};
  // Hidden formatting delimiters preserve the exact recorded substring across bold/code spans.
  const excerpt = (refKind === "answer" ? range.cloneContents().textContent : selection.toString())?.trim();
  if (!excerpt || excerpt.length > 2000) return null;
  if (refKind === "source" || refKind === "answer") return {kind:refKind,id:refId,excerpt};
  return null;
}

export function SelectedText({add}: {add: (ref: TurnRef, visibleText?: string, note?: string) => boolean}) {
  const [item,setItem] = useState<TurnRef | null>(null);
  const range = useRef<Range | null>(null);
  const anchor = useRef<HTMLDivElement>(null);
  const [editing,setEditing] = useState(false);
  const [label,setLabel] = useState("");
  const [note,setNote] = useState("");
  const [error,setError] = useState("");
  function dismiss() {
    setEditing(false);setNote("");setError("");setItem(null);range.current=null;
    window.getSelection()?.removeAllRanges();
  }
  function stage() {
    if (item && add(item,label,note.trim())) dismiss();
    else setError("Remove a reference from your message first.");
  }
  useEffect(() => {
    if (editing) return;
    function change() {
      const selected = window.getSelection();
      const ref = selectionRef(selected);
      range.current = ref && selected ? selected.getRangeAt(0).cloneRange() : null;
      setLabel(selected?.toString().trim() || "");setItem(ref);
    }
    document.addEventListener("selectionchange",change);
    return () => document.removeEventListener("selectionchange",change);
  }, [editing]);
  useLayoutEffect(() => {
    const action = anchor.current, selected = range.current;
    if (!item || !action || !selected) return;
    function place() {
      if (!action || !selected) return;
      const ancestor = selected.commonAncestorContainer;
      const owner = ancestor instanceof Element ? ancestor : ancestor.parentElement;
      const clip = owner?.closest(".transcript,dialog,#research-panels")?.getBoundingClientRect();
      const top = Math.max(clip?.top ?? 0, document.querySelector(".masthead")?.getBoundingClientRect().bottom ?? 0);
      const bottom = Math.min(clip?.bottom ?? innerHeight, innerHeight);
      const visible = Array.from(selected.getClientRects()).filter(r => r.width > 0 && r.bottom > top && r.top < bottom);
      action.hidden = !visible.length && !editing;
      if (action.hidden) return;
      const first = visible[0] ?? selected.getBoundingClientRect();
      const above = first.top - action.offsetHeight - 6;
      const y = above >= top ? above : Math.max(top,Math.min(bottom-action.offsetHeight,(visible.at(-1) ?? first).bottom+6));
      action.style.top = y + "px";
      action.style.left = Math.max(6,Math.min(first.left,innerWidth-action.offsetWidth-6)) + "px";
    }
    place();
    document.addEventListener("scroll",place,true);
    window.addEventListener("resize",place);
    return () => { document.removeEventListener("scroll",place,true); window.removeEventListener("resize",place); };
  }, [item,editing,error]);
  if (!item) return null;
  return <div ref={anchor} className="selection-anchor">
    {editing ? <section className="selection-editor" role="dialog" aria-label="Reply to selection" onKeyDown={e => {
      if (e.key === "Escape") {e.preventDefault();e.stopPropagation();dismiss();}
    }}>
      <blockquote>{label}</blockquote>
      <label className="sr-only" htmlFor="selection-note">Follow-up question</label>
      <textarea id="selection-note" autoFocus rows={2} maxLength={12000} value={note} placeholder="Ask about this selection…"
        onChange={e => setNote(e.target.value)} onKeyDown={e => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {e.preventDefault();stage();}
        }}/>
      {error && <p role="alert">{error}</p>}
      <div className="actions"><button className="quiet" type="button" onClick={dismiss}>Cancel</button>
        <button className="primary" type="button" onClick={stage}>Add to message</button></div>
    </section> : <button type="button" className="primary selection-action" aria-label="Quote selection in question"
      onPointerDown={e => e.preventDefault()} onClick={() => setEditing(true)}>Quote selection</button>}
  </div>;
}
