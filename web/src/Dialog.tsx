import { useEffect, useRef, type ReactNode } from "react";

export function Dialog({id, title, close, children, closeLabel, kicker, inline = false}: {
  id: string; title: string; close: () => void; children: ReactNode; closeLabel?: string; kicker?: string; inline?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (inline) return;
    const dialog = ref.current;
    const trigger = document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus({preventScroll:true});
    };
  }, [inline]);
  if (inline) return <section className="ledger" aria-label={title}><h2>{title}</h2>{children}</section>;
  return <dialog ref={ref} id={id} aria-labelledby={id + "-title"} onCancel={close}>
    <header><div>{kicker && <p className="kicker">{kicker}</p>}
      <h2 id={id + "-title"}>{title}</h2></div>
      {closeLabel && <button type="button" className="icon-button" aria-label={closeLabel} onClick={close}>×</button>}
    </header>{children}
  </dialog>;
}
