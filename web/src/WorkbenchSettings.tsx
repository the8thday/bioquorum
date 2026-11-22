import { useState, type FormEvent } from "react";
import { Dialog } from "./Dialog";
import { errorMessage, request } from "./api";

import { Extensions, type Section } from "./Extensions";
import { Icon } from "./Icon";

export const panels = ["Evidence","Artifacts","Activity","Usage","Context"] as const;
export type Preferences = {inbox_notifications?:boolean; reading_size: "standard" | "large"; initial_panel: typeof panels[number]};
export const defaults: Preferences = {reading_size: "standard", initial_panel: "Activity"};

export function WorkbenchSettings({value, saved, close, initial, base, newProject, openModels, openProjectSettings}: {
  initial:Section; base:string; newProject:()=>void; openModels?:()=>void; openProjectSettings?:()=>void; value: Preferences; saved: (value: Preferences) => void; close: () => void;
}) {
  const [section,setSection] = useState<Section>(initial);
  const [draft,setDraft] = useState(value);
  const [pending,setPending] = useState(false);
  const [error,setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault(); setPending(true);
    try { saved(await request<Preferences>("/api/workbench-settings",draft)); close(); }
    catch (e) { setError(errorMessage(e)); setPending(false); }
  }
  return <Dialog id="workbench-settings" title="Workbench settings" closeLabel="Close workbench settings" close={close}>
    <div className="settings-layout"><nav aria-label="Settings sections">
      <span>Preferences</span><button aria-current={section==="General"?"page":undefined} onClick={()=>setSection("General")}><Icon name="gear"/>General</button>
      {(openModels || openProjectSettings) && <><span>Setup</span>
        {openModels && <button type="button" onClick={openModels}><Icon name="sparkles"/>Models &amp; accounts</button>}
        {openProjectSettings && <button type="button" onClick={openProjectSettings}><Icon name="settings"/>Project settings</button>}</>}
      <span>Customize project</span>{(["Skills","MCP connectors"] as const).map(s=><button key={s} aria-current={section===s?"page":undefined} onClick={()=>setSection(s)}><Icon name={s==="Skills"?"skills":"plug"}/>{s}</button>)}
    </nav><div className="settings-content">{section!=="General" ? <Extensions close={close} key={section} section={section} base={base} newProject={newProject}/> : <><h3>Appearance</h3>
    <p className="muted">Applies to all projects on this machine.</p>
    <form className="preferences-form" onSubmit={submit}>
      <label>Conversation text size<select value={draft.reading_size} onChange={e =>
        setDraft({...draft,reading_size:e.target.value as Preferences["reading_size"]})}>
        <option value="standard">Standard</option><option value="large">Large</option>
      </select></label>
      <label>Inspector when opening a session<select value={draft.initial_panel} onChange={e =>
        setDraft({...draft,initial_panel:e.target.value as Preferences["initial_panel"]})}>
        {panels.map(name => <option key={name}>{name}</option>)}
      </select></label>
      {"Notification" in window && <label><input type="checkbox" checked={!!draft.inbox_notifications}
        onChange={async e=>{
          if (!e.target.checked) {setDraft({...draft,inbox_notifications:false});return;}
          try {
            if (await Notification.requestPermission()==="granted") setDraft({...draft,inbox_notifications:true});
            else setError("Allow notifications for this site in your browser.");
          } catch(e) {setError(errorMessage(e));}
        }}/> Notify me of new inbox items while the workbench is open</label>}
      {error && <p role="alert" className="error">{error}</p>}
      <div className="actions"><button className="quiet" type="button" onClick={close}>Cancel</button>
        <button className="primary" disabled={pending}>Save preferences</button></div>
    </form></>}</div></div></Dialog>;
}
