import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorMessage, figurePath, request, sessionPath } from "./api";
import { Splitter, useSidebarLayout } from "./SidebarLayout";
import { ResultViewer, type ResultItem } from "./ResultViewer";
import { EvidenceIndex } from "./EvidenceIndex";
import { Onboarding } from "./Onboarding";
import { Artifacts, Upload, type Product } from "./Artifacts";
import { ActivityFeed } from "./Activity";
import { Composer } from "./Composer";
import { EvidenceDialog, readEvidence } from "./Evidence";
import { AnswerCard, TurnCard } from "./Record";
import { AuditDialog, NameDialog, NoteDialog, ReplicationDialog, type Audit, type Estimate, type Note } from "./SessionDialogs";
import { SelectedText } from "./References";
import { Dialog } from "./Dialog";
import { SessionSearch } from "./SessionSearch";
import { ContextDialog } from "./Context";
import { defaults, panels, WorkbenchSettings, type Preferences } from "./WorkbenchSettings";
import { SettingsDialog } from "./Settings";
import { UsageDialog } from "./Usage";
import { useRecord } from "./useRecord";
import type { DraftReference, EvidenceRef, Project, Snapshot, TurnRef } from "./types";
import { Icon } from "./Icon";
import { InboxNotifications } from "./InboxNotifications";
import { Tasks } from "./Tasks";
import { Projects } from "./Projects";
import { Materials } from "./Materials";
import type { Section } from "./Extensions";
import plate from "./radiolaria.webp";

type Modal = {kind: "inbox" | "tasks" | "new" | "fork" | "rename" | "archive" | "usage" | "context" | "settings" | "chat-settings"} | {kind:"preferences"; section:Section} | {kind: "audit"; data: Audit} | {kind: "note"; data: Note}
  | {kind: "evidence"; claimIds:string[]; item: TurnRef; data: Awaited<ReturnType<typeof readEvidence>>}
  | {kind: "replication"; path: string; estimate: Estimate};

const emptyRecord: Snapshot = {last_seq:0,questions:[],turns:[],figures:[],answers:[],pending_plan:null,permissions:[],activity:[],verification:[]};

function Intro({start}: {start: () => void}) {
  return <article className="example-entry"><img src={plate} alt="" width="64" height="64"/><div>
    <h2>Explore the metformin evidence</h2><p>Open a recorded session to inspect its papers, figures and calculations.</p>
    <button className="quiet" onClick={start}>Try the metformin example <Icon name="arrow"/></button></div></article>;
}

interface Selection {project: Project; id: string; fill: boolean; draft?:string}
function updateURL(id: string, home = false, leaveLine = false) {
  const url = new URL(location.href);
  if (leaveLine) {url.searchParams.delete("view"); url.hash="";}
  if (url.hash === "#sessions-heading") url.hash = "";
  if (home) url.searchParams.set("home","1"); else url.searchParams.delete("home");
  if (id) url.searchParams.set("session", id);
  else url.searchParams.delete("session");
  history.replaceState(null, "", url);
}

export function App() {
  const [selection, setSelection] = useState<Selection | null>(null);
  const [error, setError] = useState("");
  const [prefs,setPrefs] = useState<Preferences>(defaults);
  const navigation = useRef(0);
  const layout = useSidebarLayout();
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([request<Project>("/api/sessions", undefined, controller.signal),
      request<Preferences>("/api/workbench-settings", undefined, controller.signal).catch(() => defaults)
    ]).then(([project,settings]) => {
      if (controller.signal.aborted) return;
      setPrefs(settings);
      const wanted = new URL(location.href).searchParams.get("session");
      const home = new URL(location.href).searchParams.get("home") === "1";
      const id = home ? "" : project.sessions.find(s => s.id === wanted)?.id ?? project.sessions[0]?.id ?? "";
      updateURL(id,home); setSelection({id, project, fill: false});
    }).catch(e => { if (!controller.signal.aborted) setError(errorMessage(e)); });
    return () => controller.abort();
  }, []);
  async function select(id: string, fill = false, home = false, draft?:string, leaveLine = false) {
    const version = ++navigation.current;
    const project = await request<Project>("/api/sessions");
    if (version !== navigation.current) return;
    if (!id && !home) id = project.sessions[0]?.id ?? "";
    if (id && !project.sessions.some(s => s.id === id)) throw new Error("Session not found");
    updateURL(id,home,leaveLine); setSelection({id, project, fill, draft});
    requestAnimationFrame(() => document.getElementById("session-name")?.focus());
  }
  if (!selection) return <main><p role="status">{error || "Loading…"}</p></main>;
  return <Session key={selection.id} layout={layout} {...selection} select={select} prefs={prefs} setPrefs={setPrefs}/>;
}

function Session({id, project, fill, draft, select, prefs, setPrefs,layout}: Selection & {layout:ReturnType<typeof useSidebarLayout>;prefs: Preferences; setPrefs: (value: Preferences) => void; select: (id: string, fill?: boolean,home?:boolean, draft?:string, leaveLine?:boolean) => Promise<void>}) {
  const base = id ? sessionPath(id) : "";
  const {record, refresh, earlier, full, connection, progress, error} = useRecord(base);
  const transcript = useRef<HTMLDivElement>(null);
  const [anchor,setAnchor] = useState(location.hash.slice(1));
  const located = useRef("");
  const requestedHistory = useRef("");
  useEffect(()=>{
    const changed=()=>{located.current="";setAnchor(location.hash.slice(1));};
    window.addEventListener("hashchange",changed);
    return ()=>window.removeEventListener("hashchange",changed);
  },[]);
  useEffect(()=>{
    if (!record || !/^(claim|record)-/.test(anchor) || located.current===anchor) return;
    const node = anchor.startsWith("claim-")
      ? transcript.current?.querySelector<HTMLElement>('[data-claim-id="'+CSS.escape(anchor.slice(6))+'"]')
      : document.getElementById(anchor);
    if (!node) {
      if (record.has_earlier && requestedHistory.current!==anchor) {
        requestedHistory.current=anchor;
        full().catch(report);
      }
      return;
    }
    let closed = node.closest<HTMLDetailsElement>("details:not([open])");
    while (closed) {closed.open=true; closed=closed.closest("details:not([open])");}
    node.scrollIntoView({block:"center"});
    located.current=anchor;
  },[record,anchor,full]);
  const follow = useRef(false);
  const [modal, setModal] = useState<Modal | null>(null);
  const [message, setMessage] = useState<ReactNode>("");
  const [attachments,setAttachments] = useState<Product[]>([]);
  const [panel,setPanel] = useState<Preferences["initial_panel"]>(prefs.initial_panel);
  const [collapsed,setCollapsed] = useState(() => window.innerWidth < 1000);
  const [navOpen, setNavOpen] = useState(false);
  const [lineView,setLineView] = useState(() => project.example ? null : new URL(location.href).searchParams.get("view"));
  const materials = lineView === "materials" || lineView === "program";
  function showMaterials(open: boolean, overview = false) {
    const url = new URL(location.href);
    if (open) url.searchParams.set("view", overview ? "program" : "materials");
    else { url.searchParams.delete("view"); url.searchParams.delete("program"); }
    history.replaceState(null, "", url);
    setLineView(open ? overview ? "program" : "materials" : null);
    setNavOpen(false);
  }
  const [actionsOpen, setActionsOpen] = useState(false);
  const actionsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!actionsOpen) return;
    const dismiss = (e: PointerEvent) => { if (!actionsRef.current?.contains(e.target as Node)) setActionsOpen(false); };
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") { setActionsOpen(false); actionsRef.current?.querySelector("button")?.focus(); } };
    document.addEventListener("pointerdown", dismiss); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", escape); };
  }, [actionsOpen]);
  const [refs, setRefs] = useState<DraftReference[]>([]);
  function add(item: TurnRef, visibleText?: string, note?: string) {
    const same = (r: DraftReference) => r.item.kind === item.kind && r.item.id === item.id &&
      r.item.name === item.name && r.item.excerpt === item.excerpt && JSON.stringify(r.item.selection) === JSON.stringify(item.selection);
    if (refs.length >= 8 && !refs.some(same)) {setMessage("Attach at most eight references per question.");return false;}
    const entry = {item,label:visibleText || item.excerpt || item.name || item.id,note};
    setRefs(old => old.some(same) ? old.map(r => same(r) && note && note !== r.note
      ? {...r,note:[r.note,note].filter(Boolean).join("\n")} : r) : [...old,entry]);
    setModal(null);
    requestAnimationFrame(() => document.getElementById("question")?.focus());
    return true;
  }
  const quote = project.references ? add : undefined;
  const [worker, setWorker] = useState("");
  const [preset, setPreset] = useState(Number(fill));
  const title = project.sessions.find(s => s.id === id)?.title ?? "Start a research session";
  const running = record?.turns.some(t => t.seq >= 0 && t.status === "running");
  const awaitingPermission = !!record?.permissions.length;
  const executing = !!running && !awaitingPermission && connection === "Connected";
  const close = () => setModal(null);
  const report = (e: unknown) => setMessage(<span className="error">{errorMessage(e)}</span>);
  function attach(p:Product) {
    if (!attachments.some(a => a.id===p.id) && attachments.length >= 8) {setMessage("Attach at most eight files per question."); return;}
    setAttachments(old => old.some(a => a.id===p.id) ? old : [...old,p]);
  }
  const homeSession = useRef("");
  async function prepareSession(title: string) {
    if (!homeSession.current) {
      const created = await request<{id:string}>("/api/sessions",{title:title.trim().slice(0,100) || "Research session"});
      homeSession.current = created.id;
    }
    return homeSession.current;
  }
  async function mutate(suffix: string, body: unknown) {
    if (!id && suffix === "/turns") {
      const sid = await prepareSession((body as {question:string}).question);
      await request(sessionPath(sid)+suffix,body);
      await select(sid);
      return;
    }
    if (suffix === "/turns") follow.current = true;
    setMessage(""); await request(base + suffix, body); await refresh();
  }
  async function create(title: string, fork = false, fill = false) {
    const row = await request<{id: string}>(fork ? base + "/fork" : "/api/sessions", {title});
    showMaterials(false);
    await select(row.id, fill);
  }
  async function start() {
    if (!project.example) return setModal({kind: "new"});
    if (!id) await create("Metformin evidence", false, true);
    else setPreset(n => n + 1);
  }
  async function evidence(answer: string, ref: EvidenceRef) {
    const item: TurnRef = ref.type === "value" ? {kind:"value",id:ref.run_id,name:ref.name} : {kind:"source",id:ref.call_id,excerpt:ref.quote};
    const claims = record?.answers.find(row=>row.id===answer)?.claims ?? [];
    const matches = claims.filter(c=>c.segments.some(s=>s.ref?.type === ref.type && (
      ref.type==="value" && s.ref.type==="value" ? s.ref.run_id===ref.run_id && s.ref.name===ref.name :
      ref.type==="quote" && s.ref.type==="quote" && s.ref.call_id===ref.call_id && s.ref.quote===ref.quote)));
    setModal({kind: "evidence", item, claimIds:matches.map(c=>c.id), data: await readEvidence(base, answer, ref)});
  }
  async function runVerification(path: string, body: unknown) {
    setMessage("Verification running…");
    await request(path, body); await refresh();
    setMessage("Verification complete. Open the comparison under the claim.");
  }
  async function verify(claim: string, kind: "replication" | "rerun") {
    const path = base + "/claims/" + claim + "/" + kind;
    if (kind === "rerun") return runVerification(path, {});
    const estimate = await request<Estimate>(path);
    if (!estimate.isolated_execution_available) throw new Error("Independent replication requires Linux kernel isolation.");
    setModal({kind, path, estimate});
  }
  async function exportBundle() {
    setMessage("Packing the selected session…");
    const bundle = await request<{id: string}>(base + "/bundles", {});
    setMessage(<a href={base + "/bundles/" + bundle.id}>Download research bundle</a>);
  }
  useEffect(() => {
    const el = transcript.current;
    if (el && follow.current) el.scrollTop = el.scrollHeight;
  }, [record?.last_seq]);

  const [opened,setOpened] = useState<ResultItem[]>([]);
  const [activeId,setActiveId] = useState("");
  const active = opened.find(item=>item.id===activeId);
  const returnFocus = useRef(new Map<string,HTMLElement>());
  const [initial,setInitial]=useState<TurnRef["selection"]>();
  function openResult(item:ResultItem,selection?:TurnRef["selection"]) {
    setInitial(selection);
    if (document.activeElement instanceof HTMLElement) returnFocus.current.set(item.id,document.activeElement);
    setOpened(old=>old.some(o=>o.id===item.id)?old:[...old,item]);
    setActiveId(item.id);setCollapsed(false);setNavOpen(false);
  }
  function closeResult() {
    const rest=opened.filter(o=>o.id!==activeId);
    setOpened(rest);setActiveId(rest.at(-1)?.id ?? "");
    if (!rest.length) {
      if (window.innerWidth<1000) setCollapsed(true);
      const origin=returnFocus.current.get(activeId);
      requestAnimationFrame(()=>{if(origin?.isConnected && origin.getClientRects().length)origin.focus({preventScroll:true});
        else document.querySelector<HTMLElement>(".panel-toggle")?.focus({preventScroll:true});});
    }
    returnFocus.current.delete(activeId);
  }
  const products = record?.products ?? [];
  const comparisonTurn=record?.turns.slice().reverse().find(t=>t.selections?.some(r=>r.id===activeId && r.selection?.type==="figure"));
  const comparisonFigures=comparisonTurn ? products.filter(p=>p.turn===comparisonTurn.id && p.preview==="figure") : [];
  const recordedFigures = new Set(record?.figures.map(f=>f.id));
  const resultFigures = new Map(products.filter(p=>recordedFigures.has(p.id)).map(p=>[p.id,p]));
  const sizes=layout.columns(!!active && !materials,!!id && !collapsed && !materials);
  const answerPositions = new Map(record?.answers.map(a=>[a.turn,a.seq]));
  const groups = new Map<string,{seq:number;items:Product[]}>();
  for (const p of products) {
    if (!p.preview || recordedFigures.has(p.id) || (p.kind==="figure" && p.status==="recorded")) continue;
    const key=p.turn || p.id;
    const group=groups.get(key) ?? {seq:p.turn ? answerPositions.get(p.turn) ?? p.seq : p.seq,items:[]};
    group.seq=Math.max(group.seq,p.seq);
    group.items.push(p);
    groups.set(key,group);
  }
  const cards = record ? [
    ...record.questions.map(q => ({seq: q.seq, view: <TurnCard key={"q" + q.seq} turn={{question: q.text,id:q.id}}/>})),
    ...record.turns.map(t => ({seq: t.seq, view: <TurnCard key={t.id} turn={t} reopen={r=>{const p=products.find(product=>product.id===r.id);if(p)openResult(p,r.selection);}} animated={t.seq >= 0 && executing} waiting={t.seq >= 0 && awaitingPermission}/>})),
    ...record.figures.map(f => ({seq: f.seq, view: <article key={"f" + f.seq} className="panel figure"><h2>Recorded figure</h2>
      <button className="figure-trigger" aria-label="Open recorded figure" onClick={()=>{const p=resultFigures.get(f.id);if(p)openResult(p);}}>
        <img src={figurePath(base, f.hash)} loading="lazy" alt="Figure from the recorded calculation"/><span>Open figure <Icon name="arrow"/></span></button>{quote && <button className="quiet" onClick={() => quote({kind:"figure",id:f.hash,name:f.run_id})}>Quote figure</button>}</article>})),
    ...record.answers.map(a => ({seq: a.seq, view: <AnswerCard discuss={project.first_pass ? add : undefined} openTable={openResult} addReference={quote} key={a.id} answer={a} example={!!project.example}
      verification={record.verification} onEvidence={(answer, ref) => evidence(answer, ref).catch(report)}
      onReview={answer => mutate("/answers/" + answer + "/review", {}).catch(report)}
      onVerify={(claim, kind) => verify(claim, kind).catch(report)}/>})),
    ...Array.from(groups,([key,group])=>({seq:group.seq,view:<div className="result-links" aria-label="Research results" role="group" key={"products-"+key}>
      {group.items.map(p=><button key={p.id} className="result-link" aria-label={"Open "+p.name} title={p.preview+" · v"+p.version} onClick={()=>openResult(p)}>
        <Icon name={p.preview==="table"?"table":p.preview==="figure"?"image":"document"}/><span>{p.name}</span></button>)}</div>}))
  ].sort((a,b) => a.seq - b.seq) : [];

  const home = !id;
  const preferences = (section:Section) => {setNavOpen(false);setModal({kind:"preferences",section});};
  return <><a className="skip-link" href="#workspace">{materials ? lineView==="program" ? "Skip to research line":"Skip to materials" : "Skip to research record"}</a><div data-reading-size={prefs.reading_size} style={sizes.style} className={"shell"+(collapsed ? " panels-collapsed" : "")+(layout.compact ? " compact-nav" : "")+(active ? " viewing-result" : "")}><header className="masthead">
    <button className="desktop-nav icon-button" aria-label={layout.compact?"Expand navigation":"Collapse navigation"} title={layout.compact?"Expand navigation":"Collapse navigation"}
      aria-controls="project-nav" aria-expanded={!layout.compact} onClick={()=>layout.setCompact(!layout.compact)}><Icon name="side"/></button>
    <button className="mobile-nav icon-button" aria-label="Projects and sessions" aria-expanded={navOpen} onClick={() => setNavOpen(v => !v)}><Icon name="menu"/></button>
    <div className="brand"><span className="brand-mark" aria-hidden="true"/><strong>BioQuorum</strong></div>
    <span className="masthead-label">Research workspace</span><span className="mode">
      {project.example ? "Offline example" : project.classification + " project"}</span>
    {id && !materials && <button className="quiet panel-toggle" aria-label={collapsed ? "Show research panels" : "Hide research panels"} title="Research panels" aria-expanded={!collapsed} aria-controls="research-panels"
       onClick={() => {setCollapsed(v => !v);setNavOpen(false);}}><Icon name="side"/><span>Inspector</span></button>}
    <button className="icon-button workbench-settings" title="Workbench settings" aria-label="Workbench settings" onClick={() => preferences("General")}><Icon name="gear"/></button>
  </header>
    <aside id="project-nav" className={"sidebar"+(navOpen ? " mobile-open" : "")} aria-label="Project and sessions">
      <nav className="workspace-nav" aria-label="Workspace"><button className="rail-action" title="Home" aria-current={home && !materials ? "page":undefined} onClick={()=>{showMaterials(false);select("",false,true).catch(report);}}><Icon name="home"/><span>Home</span></button>
        {!project.example && <button className="rail-action" title="Research lines" aria-current={lineView==="program" ? "page":undefined}
          onClick={()=>{showMaterials(true,true);requestAnimationFrame(()=>document.getElementById("workspace")?.focus());}}><Icon name="Evidence"/><span>Research lines</span></button>}
        {!project.example && <button className="rail-action" title="Materials" aria-current={lineView==="materials" ? "page":undefined}
          onClick={()=>{showMaterials(true);requestAnimationFrame(()=>document.getElementById("workspace")?.focus());}}><Icon name="Artifacts"/><span>Materials</span></button>}
        <button className="rail-action" title="Inbox" onClick={()=>setModal({kind:"inbox"})}><Icon name="Evidence"/><span>Inbox</span></button>
        <button className="rail-action" title="Research tasks" onClick={()=>setModal({kind:"tasks"})}><Icon name="Activity"/><span>Research tasks</span></button>
        <button className="rail-action" title="Skills" onClick={()=>preferences("Skills")}><Icon name="skills"/><span>Skills</span></button>
        <button className="rail-action" title="MCP connectors" onClick={()=>preferences("MCP connectors")}><Icon name="plug"/><span>MCP connectors</span></button></nav>
      <Projects name={project.project}/>
      <div className="session-header"><h2 id="sessions-heading" tabIndex={-1}>Sessions</h2>
        <button className="icon-button" aria-label="New session" title="New session" onClick={() => setModal({kind: "new"})}><Icon name="add"/></button></div>
      <SessionSearch project={project} selected={materials ? "" : id} open={sid => {
        showMaterials(false);select(sid).then(()=>{if(sid===id) return refresh();}).catch(report);
      }}/>
      <button className="rail-action settings-link" title="Project settings" onClick={() => setModal({kind:"settings"})}><Icon name="settings"/><span>Project settings</span></button></aside>
    {!layout.compact && <Splitter side="left" value={sizes.leftWidth} min={180} max={sizes.leftMax} change={layout.setLeft}/>}
    {materials && <Materials currentSession={id} project={project} newSession={()=>setModal({kind:"new"})} overview={lineView==="program"}
      openSession={(sid,prompt)=>{select(sid,false,false,prompt,true).then(()=>setLineView(null)).catch(report);}}/>}
    <main hidden={materials} id={materials ? undefined : "workspace"} className={home ? "home-workspace" : record ? "has-session" : undefined} tabIndex={-1}><header className="record-header">
    <p className="crumb">{project.project}{!home && <> <span aria-hidden="true">/</span> Session</>}</p>
    <div className="title-row"><h1 id="session-name" tabIndex={-1}>{title}</h1>
    {id && <div className="session-tools" ref={actionsRef}><button className="icon-button" aria-label="Session actions" aria-expanded={actionsOpen} onClick={() => setActionsOpen(v => !v)}><Icon name="more"/></button>
      {actionsOpen && <div className="record-actions" onClick={() => setActionsOpen(false)}>
      <button className="quiet" onClick={() => request<Audit>(base + "/audit").then(data => setModal({kind: "audit", data})).catch(report)}>Audit log</button>
      {project.project_notes && <button className="quiet" onClick={() => request<Note>(base + "/project-note").then(data =>
        setModal({kind: "note", data})).catch(report)}>Project note</button>}
      <button className="quiet" disabled={running || !record?.answers.length} onClick={() => exportBundle().catch(report)}>Export research bundle</button>
      <button className="quiet" onClick={() => setModal({kind: "context"})}>Model context</button>
      <button className="quiet" onClick={() => setModal({kind: "usage"})}>Model usage</button>
      <button className="quiet" onClick={() => setModal({kind: "rename"})}>Rename session</button>
      <button className="quiet" disabled={!record || running || !!record.pending_plan}
        onClick={() => setModal({kind: "archive"})}>Archive session</button>
      <button className="quiet" disabled={!record || running || !!record.pending_plan} onClick={() => setModal({kind: "fork"})}>Fork session</button>
    </div>}</div>}</div></header>
    <div className="transcript" ref={transcript} onScroll={event => {
      const el = event.currentTarget;
      follow.current = el.scrollHeight - el.clientHeight - el.scrollTop < 48;
    }}>
    {!home && <Onboarding project={project}/>}
    {(record?.disconnected ?? project.onboarding?.disconnected ?? []).map(name => <p key={name} className="warning" role="status">MCP connector {name} is disconnected. Its tools are unavailable. Reconnect it in Project settings.</p>)}
    {id && !record && !error && <p role="status">Loading session record…</p>}
    {!home && project.kernel_notice && <p id="kernel-notice" className="warning">{project.kernel_notice}</p>}
    <section id="record" aria-label="Research record">{record?.has_earlier && <button className="quiet" onClick={()=>earlier().catch(report)}>Show earlier research</button>}{cards.length ? cards.map(c => c.view)
      : project.example && (!id || record) && <Intro start={() => start().catch(report)}/>}</section>
    {record && running && progress && <p role="status" className="generation-progress">{progress}</p>}
    {quote && !modal && <SelectedText add={quote}/>}
    </div>
    <div className="compose-dock">
    {(record || home && !project.example) && <Composer draftPlan={async q=>{
      const session=id || await prepareSession(q);
      return request(sessionPath(session)+"/plan-draft",{question:q});
    }} initialDraft={draft} executing={executing} connection={connection} upload={!project.example && <Upload base={base} resolveBase={home ? async()=>sessionPath(await prepareSession("Research session")) : undefined}
      classification={project.classification} disabled={!!running || attachments.length >= 8}
      onUpload={p => {attach(p);if(id) void refresh().catch(report);}}/>} openSettings={() => setModal({kind:"chat-settings"})} attachments={attachments} setAttachments={setAttachments} refs={project.references || project.first_pass ? refs : []} setRefs={setRefs} planDefault={project.plan_default} worker={worker} setWorker={setWorker} record={record ?? emptyRecord} models={project.worker_models} classification={project.classification} example={project.example} preset={preset} report={report} mutate={mutate}/>}
    <p id="message" role="status" aria-live="polite">{message || error}</p></div>{home && <>{project.kernel_notice && <p id="kernel-notice" className="warning">{project.kernel_notice}</p>}<Onboarding project={project}/></>}</main>

    {id && !collapsed && !materials && <Splitter side="right" value={sizes.rightWidth} min={280} max={sizes.rightMax} change={sizes.setRight}/>}
    <div className="margin-column" id="research-panels" hidden={collapsed || !id || materials}>{!active && <div className="inspector-title">Research inspector</div>}
      {id && !active && <nav className="panel-tabs" aria-label="Research panels">{panels.map(name =>
        <button key={name} className="quiet" aria-pressed={!active && panel===name} onClick={() => {setActiveId("");setPanel(name);}}><Icon name={name}/>{name}</button>)}</nav>}
      {!active && opened.length>0 && <div className="resume-results">{opened.map(item=><button className="quiet" key={item.id} onClick={()=>setActiveId(item.id)}>{item.name}</button>)}</div>}
      {active ? <ResultViewer comparedFigure={comparisonFigures.length===1?comparisonFigures[0].id:undefined} initial={initial} quote={quote} items={opened} active={active} base={base} products={products} select={id=>{setInitial(undefined);setActiveId(id);}} open={openResult} close={closeResult} inspector={()=>setActiveId("")}
        evidence={(answer,ref)=>evidence(answer,ref).catch(report)}/> : panel==="Evidence" && id ? <EvidenceIndex answers={record?.answers ?? []} open={(a,r) => evidence(a,r).catch(report)}/> :
       panel==="Usage" && id ? <UsageDialog inline base={base} seq={record?.last_seq ?? 0} close={close}/> :
       panel==="Context" && id ? <ContextDialog inline base={base} worker={worker} seq={record?.last_seq ?? 0} example={!!project.example}
         active={!!running || !!record?.pending_plan} close={close} fork={() => setModal({kind:"fork"})}/> :
       panel==="Artifacts" && id ? <Artifacts base={base} rows={record?.products} open={openResult} attach={attach}/> :
       <ActivityFeed currentState={awaitingPermission ? "Awaiting permission" : running ? "Research running" : record?.pending_plan ? "Awaiting approval" : "Session idle"} quote={quote} items={record?.activity ?? []} connection={connection} progress={progress}/>}</div>
    </div>
    {(modal?.kind === "new" || modal?.kind === "fork" || modal?.kind === "rename") && <NameDialog mode={modal.kind}
      initial={modal.kind === "rename" ? title : modal.kind === "fork" ? title.slice(0,152) + " · fork" : project.example ? "Metformin evidence" : ""}
      close={close} save={async name => {
        if (modal.kind === "rename") { await request(base + "/title", {title: name}); await select(id); close(); }
        else await create(name, modal.kind === "fork");
      }}/>}
    {modal?.kind === "context" && <ContextDialog base={base} worker={worker} seq={record?.last_seq ?? 0}
      active={!!running || !!record?.pending_plan} example={!!project.example} close={close} fork={() => setModal({kind: "fork"})}/>}
    {modal?.kind === "preferences" && <WorkbenchSettings initial={modal.section} base={base} newProject={()=>{close();requestAnimationFrame(()=>document.getElementById("new-project")?.click());}} value={prefs} saved={setPrefs} close={close}/>}
    {(modal?.kind === "settings" || modal?.kind === "chat-settings") && <SettingsDialog quick={modal.kind === "chat-settings"} base={base} close={close} saved={async () => {await select(id,false,!id); if (id) await refresh();}}/>}
    <InboxNotifications enabled={!!prefs.inbox_notifications} show={()=>setModal({kind:"inbox"})}/>
    {(modal?.kind === "tasks" || modal?.kind === "inbox") && <Tasks inbox={modal.kind==="inbox"} close={close} open={(sid,target)=>{
      close();showMaterials(false);
      const url=new URL(location.href);
      url.searchParams.set("session",sid);url.searchParams.delete("home");url.searchParams.delete("view");url.hash=target;
      if(sid!==id) {location.assign(url);return;}
      history.replaceState(null,"",url);
      located.current="";
      setAnchor(target);
      refresh().catch(report);
    }}/>}
    {modal?.kind === "usage" && <UsageDialog base={base} seq={record?.last_seq ?? 0} close={close}/>}
    {modal?.kind === "archive" && <Dialog id="archive-dialog" title="Archive session" close={close}>
      <p>Remove this session from the list? Its research record will be preserved.</p>
      <div className="actions"><button className="quiet" onClick={close}>Cancel</button>
        <button className="primary" onClick={async () => {
          try { await request(base + "/archive", {}); await select(""); }
          catch (e) { close(); report(e); }
        }}>Archive session</button></div></Dialog>}
    {modal?.kind === "evidence" && <EvidenceDialog claimIds={modal.claimIds} openFigure={hash=>{
      const file=products.find(p=>p.run===modal.item.id && p.hash===hash && p.preview==="figure");
      if(file){close();openResult(file);}
    }} add={quote} item={modal.item} evidence={modal.data} base={base} close={close}/>}
    {modal?.kind === "audit" && <AuditDialog initial={modal.data} path={base + "/audit"} close={close}/>}
    {modal?.kind === "note" && <NoteDialog initial={modal.data} path={base + "/project-note"} close={close}
      saved={() => {close(); setMessage("Project note saved.");}}/>}
    {modal?.kind === "replication" && <ReplicationDialog estimate={modal.estimate} close={close}
      start={body => {close(); runVerification(modal.path, body).catch(report);}}/>}
  </>;
}
