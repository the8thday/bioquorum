import { useEffect, useState } from "react";
import { errorMessage, request } from "./api";

type Account = {connected:boolean; signed_in:boolean; account:string};
export type Provider = {kind:string; allowed:boolean;};
const labels:Record<string,string> = {openai:"OpenAI API key",chatgpt:"ChatGPT plan",deepseek:"DeepSeek",openrouter:"OpenRouter",qwen:"Qwen",ollama:"Local Ollama","openai-compatible":"Custom OpenAI-compatible endpoint"};
const usage = "https://chatgpt.com/settings/usage";

function OpenAIModelPicker({connection, model, select}: {
  connection:string; model:string; select:(id:string)=>void;
}) {
  const [ids,setIds] = useState<string[]>([]);
  const [search,setSearch] = useState("");
  const [manual,setManual] = useState(false);
  const [error,setError] = useState("");
  const [pending,setPending] = useState(true);
  const [revision,setRevision] = useState(0);

  useEffect(()=>{
    const controller = new AbortController();
    setPending(true);setError("");
    request<string[]>("/api/settings/openai-models/"+encodeURIComponent(connection),{},controller.signal)
      .then(items=>{if (!controller.signal.aborted) setIds(items);})
      .catch(e=>{if (!controller.signal.aborted) {setIds([]);setError(errorMessage(e));}})
      .finally(()=>{if (!controller.signal.aborted) setPending(false);});
    return ()=>controller.abort();
  },[connection,revision]);

  const options = ids.filter(id=>id===model || id.toLowerCase().includes(search.trim().toLowerCase()));
  const typeId = manual || (!pending && !ids.length);
  return <div className="account-catalog">
    {typeId ? <label>Model ID<input maxLength={200} value={model} onChange={e=>select(e.target.value)}/></label> :
      <>{ids.length>12 && <label>Filter models<input type="search" value={search} onChange={e=>setSearch(e.target.value)}/></label>}
      <label>Account models<select aria-label="Account models" disabled={pending} value={model} onChange={e=>select(e.target.value)}>
        <option value="">{pending ? "Loading models…" : options.length ? "Choose a model" : "No matching models"}</option>
        {options.map(id=><option key={id}>{id}</option>)}</select></label>
      {ids.length>0 && <small>For research, choose a text model that supports tool calls.</small>}</>}
    {error && <p role="status">{error}</p>}
    {!pending && !error && !ids.length && <p role="status">This connection returned no models.</p>}
    <div className="actions">
      {!!ids.length && <button type="button" className="quiet" onClick={()=>{select("");setManual(!manual);}}>
        {manual ? "Choose from account models" : "Enter model ID manually"}</button>}
      <button type="button" className="quiet" disabled={pending} onClick={()=>{select("");setRevision(v=>v+1);}}>Refresh models</button>
    </div>
  </div>;
}

export function ModelConnection({providers, classification, apply, disabled}:{
  providers:Record<string,Provider>; classification:string; disabled:boolean;
  apply:(connection:Record<string,string>)=>Promise<void>;
}) {
  const [kind,setKind] = useState(classification==="confidential" ? "ollama" : "openai");
  const [model,setModel] = useState("");
  const [alias,setAlias] = useState("");
  const [endpoint,setEndpoint] = useState("");
  const [api,setApi] = useState("chat");
  const [role,setRole] = useState("worker");
  const [account,setAccount] = useState<Account>();
  const [catalog,setCatalog] = useState<{id:string;name:string}[]>([]);
  const [notice,setNotice] = useState("");
  const [loading,setLoading] = useState(false);
  const [version,setVersion] = useState(0);
  const chosen = providers[kind];
  const providerKind = chosen?.kind ?? kind;
  const plan = providerKind==="chatgpt";
  const custom = providerKind==="openai-compatible";
  const allowed = chosen?.allowed ?? (classification!=="confidential" || custom || kind==="ollama");

  useEffect(()=>{
    if (!plan || !allowed) return;
    const controller = new AbortController();
    async function load() {
      try {
        const data = await request<Account>("/api/account/chatgpt",undefined,controller.signal);
        setAccount(data);
        if (data.connected) setCatalog(await request("/api/settings/chatgpt-models",{},controller.signal));
        else setCatalog([]);
      } catch(e) {if (!controller.signal.aborted) setNotice(errorMessage(e));}
    }
    void load();
    window.addEventListener("focus",load);
    return ()=>{controller.abort();window.removeEventListener("focus",load);};
  },[plan,allowed,version]);

  async function signIn() {
    const tab = window.open("about:blank","_blank");
    if (!tab) {setNotice("Allow the sign-in tab to open, then try again.");return;}
    tab.opener=null;
    try {
      const {url} = await request<{url:string}>("/api/account/chatgpt/start",{});
      tab.location.replace(url);
      setNotice("Complete sign-in in the new tab, then return here. For SSH, forward the same workbench port on 127.0.0.1.");
    } catch(e) {tab.close();setNotice(errorMessage(e));}
  }
  async function disconnect() {
    setLoading(true);
    try {
      const result=await request<{revoked:boolean}>("/api/account/chatgpt/disconnect",{});
      setAccount(undefined);setCatalog([]);setModel("");setVersion(v=>v+1);
      setNotice(result.revoked ? "Signed out on this device." : "Signed out locally. Check connected apps in ChatGPT to revoke remote access.");
    } catch(e) {setNotice(errorMessage(e));} finally {setLoading(false);}
  }
  function select(value:string) {
    setKind(value);setModel("");setNotice("");setRole(value==="chatgpt" ? "all" : "worker");
    setEndpoint("");setApi("chat");setAlias("");
  }
  return <section className="connection-form" aria-label="Add model">
    <h3>Add a model</h3>
    <label>Connection<select value={kind} onChange={e=>select(e.target.value)}>
      {Object.entries(labels).map(([id,label])=><option key={id} value={id}>{label}</option>)}
      {Object.keys(providers).filter(id=>!labels[id]).map(id=><option key={id}>{id}</option>)}</select></label>
    {!allowed && <p className="warning">This connection is unavailable for {classification} data.</p>}
    {custom && !chosen && <><label>Connection name<input value={alias} placeholder="lab" maxLength={48} onChange={e=>setAlias(e.target.value)}/></label>
      <label>Base URL<input value={endpoint} placeholder="https://your-provider.example/v1" onChange={e=>setEndpoint(e.target.value)}/></label>
      <label>API format<select value={api} onChange={e=>setApi(e.target.value)}><option value="chat">Chat Completions</option><option value="responses">Responses</option></select></label>
      {alias && <small>Key environment variable: BQ_{alias.toUpperCase()}_API_KEY</small>}</>}
    {plan && allowed && <div className="plan-account">
      <p>Eligible research requests use your ChatGPT plan or its credits balance.</p>
      {account?.signed_in && <><p>{account.connected ? "Connected" : "Signed in · Plan permission required"} · {account.account}</p>
        <button type="button" className="quiet" disabled={loading} onClick={disconnect}>Sign out</button> <a href={usage} target="_blank" rel="noreferrer">Manage usage</a></>}
      {!account?.connected && <button type="button" className="quiet chatgpt-signin" onClick={signIn}>{account?.signed_in ? "Authorize ChatGPT plan usage" : "Continue with ChatGPT"}</button>}
      <button type="button" className="quiet" onClick={()=>{setNotice("");setVersion(v=>v+1);}}>Refresh account</button></div>}
    {providerKind==="openai" && allowed ? <OpenAIModelPicker key={kind} connection={kind} model={model} select={setModel}/> :
    <label>Model ID{plan ? <select value={model} onChange={e=>setModel(e.target.value)}><option value="">Select an account model</option>
      {catalog.map(m=><option key={m.id} value={m.id}>{m.name} · {m.id}</option>)}</select> :
      <input value={model} maxLength={200} placeholder={providerKind==="ollama" ? "Installed model tag" : "Exact model ID from your provider"} onChange={e=>setModel(e.target.value)}/>}</label>}
    <label>Use model for<select value={role} onChange={e=>setRole(e.target.value)}>
      <option value="worker">Research worker</option><option value="all">All research roles</option>
      <option value="reviewer">Reviewer</option><option value="planner">Planner</option><option value="summarizer">Summarizer</option></select></label>
    <button type="button" className="primary" disabled={disabled || !allowed || !model.trim() || (custom && !chosen && (!alias || !endpoint)) || (plan && !account?.connected)}
      onClick={()=>apply({kind:providerKind,model:model.trim(),role,...(chosen ? {name:kind} : {}),
        ...(custom && !chosen ? {name:alias,base_url:endpoint,api} : {})})}>Save model</button>
    {notice && <p role="status">{notice}</p>}
  </section>;
}
