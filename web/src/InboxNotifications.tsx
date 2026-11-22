import {useEffect,useRef} from "react";
import {request} from "./api";

export function InboxNotifications({enabled,show}:{enabled:boolean;show:()=>void}) {
  const onClick=useRef(show);
  onClick.current=show;
  useEffect(()=>{
    if (!enabled || !("Notification" in window) || Notification.permission!=="granted") return;
    const controller=new AbortController();
    let known:Set<string>|undefined;
    let timer:ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const rows=await request<{id:string}[]>("/api/inbox",undefined,controller.signal);
        if (known && rows.some(row=>!known!.has(row.id))) {
          const notice=new Notification("BioQuorum has new inbox items",{tag:"bioquorum-inbox"});
          notice.onclick=()=>{window.focus();onClick.current();notice.close();};
        }
        known=new Set(rows.map(row=>row.id));
      } catch { /* A disconnected workbench retries after the interval. */ }
      finally {if (!controller.signal.aborted) timer=setTimeout(refresh,15000);}
    }
    void refresh();
    return ()=>{controller.abort();clearTimeout(timer);};
  },[enabled]);
  return null;
}
