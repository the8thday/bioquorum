const $ = (id) => document.getElementById(id);
const node = (tag, text, cls) => {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (cls) el.className = cls;
  return el;
};
const button = (text, action, cls = "quiet") => {
  const el = node("button", text, cls);
  el.type = "button";
  el.addEventListener("click", () => action().catch(showError));
  return el;
};
const labels = {
  proposed: "Awaiting approval", approved: "Approved", declined: "Declined",
  running: "Running", queued: "Queued", completed: "Completed", failed: "Failed",
  interrupted: "Interrupted", unverified: "Assertion only", grounded: "Grounded",
  reviewed: "Reviewed", reproduced: "Reproduced", replicated: "Independently replicated",
  disputed: "Disputed",
};
const badge = (state) => node("span", labels[state] ?? state, "badge " + state);
const panel = (cls = "") => node("article", undefined, "panel " + cls);
let project, sid, stream, current, timer, generation = 0, refreshNumber = 0;
let naming = "session", blindPath, noteEdit;
const sessionURL = () => "/api/sessions/" + encodeURIComponent(sid);
const figureURL = (hash) => sessionURL() + "/figures/" + encodeURIComponent(hash);
const sessionParam = () => new URL(location.href).searchParams.get("session");

async function request(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  });
  if (res.status === 204) return;
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw Error(data.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

function showError(err) {
  $("message").textContent = err.message;
  $("message").className = "error";
}
function setMessage(text = "") {
  $("message").textContent = text;
  $("message").className = "";
}

async function list() {
  project = await request("/api/sessions");
  $("project").textContent = project.project;
  $("kernel-notice").textContent = project.kernel_notice;
  $("kernel-notice").hidden = !project.kernel_notice;
  $("mode").textContent = project.example ? "Offline example" : project.classification + " project";
  const items = project.sessions.map((row) => {
    const item = button(row.title, () => openSession(row.id));
    if (row.id === sid) item.setAttribute("aria-current", "page");
    return item;
  });
  $("sessions").replaceChildren(...items);
  $("no-sessions").hidden = items.length > 0;
}

async function openSession(id) {
  await select(id);
  if (sid === id) $("session-name").focus();
}

function coalesce() {
  clearTimeout(timer);
  timer = setTimeout(() => refresh().catch(showError), 100);
}

async function select(id, {updateURL = true} = {}) {
  if (stream) stream.close();
  clearTimeout(timer);
  generation++;
  current = null;
  sid = id;
  const selectedGeneration = generation;
  if (updateURL) {
    const url = new URL(location.href);
    if (url.hash === "#sessions-heading") url.hash = "";
    if (id) url.searchParams.set("session", id);
    else url.searchParams.delete("session");
    history.replaceState(null, "", url);
  }
  $("question").value = "";
  $("steps").value = "";
  $("plan-mode").checked = false;
  $("plan-editor").hidden = true;
  $("submit").textContent = "Run research";
  $("progress").textContent = "";
  $("record").replaceChildren();
  $("activity").replaceChildren();
  $("composer").hidden = true;
  setMessage();
  await list();
  if (selectedGeneration !== generation) return;
  const row = project.sessions.find((item) => item.id === sid);
  $("session-name").textContent = row?.title ?? "Start a session";
  $("fork").hidden = !row;
  $("audit").hidden = !row;
  if ($("audit-dialog").open) $("audit-dialog").close();
  $("export").hidden = !row;
  $("project-note").hidden = !row || !project.project_notes;
  if (!row) {
    $("connection").textContent = "";
    $("no-activity").hidden = false;
    $("record").replaceChildren(intro());
    return;
  }
  await refresh();
  if (selectedGeneration !== generation || !current) return;
  const active = new EventSource(sessionURL() + "/events?after=" + current.last_seq);
  stream = active;
  active.addEventListener("open", () => {
    if (stream === active) $("connection").textContent = "Connected";
  });
  active.addEventListener("error", () => {
    if (stream === active) $("connection").textContent = "Reconnecting…";
  });
  active.addEventListener("change", coalesce);
  active.addEventListener("progress", (event) => {
    if (stream !== active) return;
    const data = JSON.parse(event.data);
    $("progress").textContent = data ? `Generating · ${data[1].toLocaleString()} characters` : "";
  });
}

let auditBefore = null;
async function showAudit(append = false) {
  const selected = generation;
  const address = sessionURL() + "/audit" + (append ? `?before=${auditBefore}` : "");
  $("audit-more").disabled = true;
  try {
    const result = await request(address);
    if (selected !== generation) return;
    const content = $("audit-entries");
    if (!append) content.replaceChildren();
    $("audit-classification").textContent = `Data classification · ${result.classification}`;
    for (const item of result.entries) {
      const row = node("li");
      const details = Object.entries(item.details).filter(([, value]) => value !== null);
      row.append(node("strong", item.type.replaceAll("_", " ")),
        node("div", `${new Date(item.time).toLocaleString()} · ${item.actor}`, "muted"));
      if (details.length) row.append(node("div", details.map(([key, value]) => `${key.replaceAll("_", " ")} ${value}`).join(" · ")));
      content.append(row);
    }
    if (!content.children.length) content.append(node("li", "No audit events yet."));
    auditBefore = result.next_before;
    $("audit-more").hidden = auditBefore === null;
    if (!$("audit-dialog").open) $("audit-dialog").showModal();
  } finally {
    $("audit-more").disabled = false;
  }
}
$("audit").addEventListener("click", () => showAudit().catch(showError));
$("audit-more").addEventListener("click", () => showAudit(true).catch(showError));

async function refresh() {
  if (!sid) return;
  const selected = generation;
  const number = ++refreshNumber;
  const result = await request(sessionURL());
  if (selected !== generation || number !== refreshNumber) return;
  current = result;
  render(result);
}

function intro() {
  const card = panel("intro");
  const copy = node("div", undefined, "intro-copy");
  const heading = node("h2", "Follow a finding back to its ");
  heading.append(node("em", "evidence."));
  copy.append(heading);
  copy.append(node("p", project.example
    ? "Walk through a saved metformin research session. The example replays model and kernel results using excerpts from PubMed."
    : "Start a research session, then open a figure, calculation or citation directly from the record."));
  copy.append(button(project.example ? "Try the metformin example" : "New research session", async () => {
    if (!project.example) {
      nameDialog("session");
      return;
    }
    if (!sid) {
      const row = await request("/api/sessions", {title: "Metformin evidence"});
      await select(row.id);
    }
    fillExample();
  }, "primary"));
  const plate = node("figure", undefined, "cover-plate");
  const art = node("img");
  art.src = "/assets/radiolaria.webp";
  art.alt = "";
  art.width = 1000;
  art.height = 1040;
  plate.append(art, node("figcaption", "Radiolaria · Illustration"));
  card.append(copy, plate);
  return card;
}

function fillExample() {
  $("question").value = project.example.question;
  $("plan-mode").checked = true;
  $("steps").value = project.example.steps.join("\n");
  $("plan-editor").hidden = false;
  $("submit").textContent = "Submit plan";
  $("question").focus();
}

function segments(container, parts, answerId) {
  for (const part of parts) {
    if (part.ref) {
      const label = part.ref.type === "quote" ? "[" + part.ref.source + "]" : part.text;
      const control = button(label, () => openEvidence(answerId, part.ref), "evidence-link");
      control.title = part.ref.type === "quote" ? "Open cited source" : "Open calculation for " + part.ref.name;
      container.append(control);
    } else {
      const span = node("span", part.text, "segment" + (part.problem ? " error" : ""));
      if (part.problem) span.title = part.problem;
      container.append(span);
    }
  }
}

function paragraph(item, answerId, cls = "") {
  // Build text and buttons as DOM nodes. Model/source content never becomes HTML.
  const el = node("p", undefined, cls);
  segments(el, item.segments, answerId);
  return el;
}

function answerCard(answer) {
  const card = panel("answer");
  card.append(node("h2", "Findings"));
  for (const reason of answer.review_limitations) card.append(node("p", "Review incomplete · " + reason));
  const claims = answer.claims;
  const matched = new Set();
  const key = (parts) => parts.map((part) => part.raw ?? part.text).join("").trim();
  for (const item of answer.paragraphs) {
    const text = key(item.segments).replace(/^(?:[-*•]|\d+[.)])\s+/, "");
    const claim = claims.find((c) => key(c.segments) === text);
    if (claim) matched.add(claim);
    const status = claim?.status ?? "unverified";
    const row = node("div", undefined, "claim " + status);
    row.append(paragraph(item, answer.id));
    row.append(badge(status));
    if (item.unbound.length) {
      const warning = node("span", " · Unbound values or citations", "muted");
      warning.title = item.unbound.join(", ");
      row.append(warning);
    }
    for (const finding of (claim?.findings ?? [])) row.append(findingLine(finding));
    if (claim) addVerification(row, claim, answer.inherited);
    card.append(row);
  }
  const additional = claims.filter((c) => !matched.has(c));
  if (additional.length) {
    const details = node("details", undefined, "claims");
    details.append(node("summary", "Review by claim"));
    for (const claim of additional) {
      const row = node("div", undefined, "claim " + claim.status);
      row.append(paragraph(claim, answer.id), badge(claim.status));
      for (const finding of claim.findings) row.append(findingLine(finding));
      addVerification(row, claim, answer.inherited);
      details.append(row);
    }
    card.append(details);
  }
  if (!answer.inherited && !project.example) {
    const actions = node("div", undefined, "actions");
    actions.append(button("Run review", () => mutate(sessionURL() + "/answers/" + answer.id + "/review", {})));
    card.append(actions);
  }
  return card;
}

function addVerification(row, claim, inherited) {
  const results = current.verification.filter((entry) => entry.result.claim_id === claim.id);
  for (const entry of results) {
    const result = entry.result;
    const readings = entry.type === "rerun_finished"
      ? result.runs.flatMap((run) => run.values || []) : result.comparisons;
    const card = node("details", undefined, "verification");
    const heading = entry.type === "rerun_finished" ? "Rerun" : "Independent replication";
    card.append(node("summary", heading + " · " + result.outcome));
    if (entry.type === "rerun_finished" && !result.status_eligible && result.outcome !== "inconclusive") {
      card.append(node("p", "Local comparison. A locked, isolated run is required for a status upgrade.", "muted"));
    }
    for (const note of result.runs?.map((run) => run.reason).filter(Boolean) || []) {
      card.append(node("p", note, "muted"));
    }
    if (result.note) card.append(node("p", result.note, "muted"));
    if (readings.length) {
      const table = node("table");
      const tr = node("tr");
      for (const label of ["Result", "Recorded", "New", "Comparison"]) tr.append(node("th", label));
      table.append(tr);
      for (const value of readings) {
        const line = node("tr");
        const display = (value, unit) => value == null ? "Unavailable" : value + (unit ? " " + unit : "");
        for (const text of [value.name, display(value.original, value.unit),
          display(value.observed, value.observed_unit),
          value.matched == null ? "Inconclusive" : value.matched ? "Match" : "Different"]) {
          line.append(node("td", text));
        }
        table.append(line);
      }
      card.append(table);
    }
    row.append(card);
  }
  if (inherited || project.example || !claim.has_calculation ||
      !["reviewed", "reproduced", "replicated"].includes(claim.status)) return;
  const actions = node("div", undefined, "actions");
  const base = sessionURL() + "/claims/" + claim.id;
  actions.append(button("Rerun calculation", () => runVerification(base + "/rerun", {})));
  if (["reproduced", "replicated"].includes(claim.status)) {
    actions.append(button("Independent replication", () => openBlind(base + "/replication")));
  }
  row.append(actions);
}

async function runVerification(path, payload) {
  setMessage("Verification running…");
  await request(path, payload);
  await refresh();
  setMessage("Verification complete. Open the comparison under the claim.");
}

async function openBlind(path) {
  const quote = await request(path);
  if (!quote.isolated_execution_available) throw Error("Independent replication requires Linux kernel isolation.");
  blindPath = path;
  $("blind-form").reset();
  $("blind-estimate").textContent = "Estimated cost: USD " + quote.estimated_usd.toFixed(6) + " · " + quote.basis;
  $("blind-budget").value = Math.max(.001, Math.ceil(quote.estimated_usd * 1.25 * 1e6) / 1e6);
  $("blind-inputs").textContent = "Inputs: " + (quote.inputs.map((item) => item.path).join(", ") || "None");
  $("blind-targets").replaceChildren();
  for (const target of quote.targets) {
    const label = node("label", target.name + (target.unit ? " (" + target.unit + ")" : ""));
    const input = node("input");
    input.name = target.name;
    input.required = true;
    input.maxLength = 500;
    label.append(input);
    $("blind-targets").append(label);
  }
  $("blind-dialog").showModal();
}

$("close-blind").addEventListener("click", () => $("blind-dialog").close());
$("blind-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const definitions = Object.fromEntries(
    [...$("blind-targets").querySelectorAll("input")].map((input) => [input.name, input.value])
  );
  const payload = {
    question: $("blind-question").value, definitions,
    tolerance: {absolute: Number($("blind-absolute").value), relative: Number($("blind-relative").value)},
    max_estimated_usd: Number($("blind-budget").value),
  };
  $("blind-dialog").close();
  runVerification(blindPath, payload).catch(showError);
});
async function loadProjectNote() {
  const path = sessionURL() + "/project-note";
  const info = await request(path);
  if (path !== sessionURL() + "/project-note") return;
  noteEdit = {path, revision: info.revision, draft: null};
  $("note-body").value = info.body;
  $("note-status").textContent = "";
  $("note-reload").hidden = true;
  const drafts = $("note-drafts");
  drafts.replaceChildren();
  if (info.drafts.length) {
    const label = node("label", "Drafts from this session");
    label.htmlFor = "note-draft";
    const select = node("select");
    select.id = "note-draft";
    const placeholder = node("option", "Start with the saved note");
    placeholder.value = "";
    select.append(placeholder);
    for (const [index, draft] of info.drafts.entries()) {
      const choice = node("option", `Draft ${index + 1} · ${draft.body.slice(0, 44)}`);
      choice.value = draft.id;
      select.append(choice);
    }
    select.addEventListener("change", () => {
      const selected = info.drafts.find((draft) => draft.id === select.value);
      noteEdit.draft = selected?.id ?? null;
      $("note-body").value = selected?.body ?? info.body;
    });
    drafts.append(label, select);
  }
  if (!$("note-dialog").open) $("note-dialog").showModal();
}
$("project-note").addEventListener("click", () => loadProjectNote().catch(showError));
$("note-close").addEventListener("click", () => $("note-dialog").close());
$("note-reload").addEventListener("click", () => loadProjectNote().catch((error) => {
  $("note-status").textContent = error.message;
}));
$("note-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!noteEdit) return;
  $("note-save").disabled = true;
  try {
    await request(noteEdit.path, {
      body: $("note-body").value, prior_revision: noteEdit.revision, draft_event_id: noteEdit.draft,
    });
    $("note-dialog").close();
    setMessage("Project note saved.");
  } catch (error) {
    $("note-status").textContent = error.message;
    $("note-reload").hidden = false;
  } finally {
    $("note-save").disabled = false;
  }
});

$("export").addEventListener("click", async () => {
  const path = sessionURL() + "/bundles";
  try {
    setMessage("Packing the selected session…");
    const result = await request(path, {});
    const anchor = node("a", "Download research bundle");
    anchor.href = path + "/" + result.id;
    $("message").replaceChildren(anchor);
  } catch (err) {
    showError(err);
  }
});

function findingLine(finding) {
  return node("p", finding.check + " · " + finding.evidence,
    "muted " + (finding.severity === "error" ? "error" : "warning"));
}

function turnCard(turn) {
  const card = panel("turn");
  card.append(node("span", "Question", "kicker"));
  card.append(node("p", turn.question, "question"));
  if (turn.status) card.append(badge(turn.status));
  if (turn.error) card.append(node("p", turn.error, "error"));
  if (turn.plan) {
    const plan = node("section", undefined, "plan");
    const title = node("h2", "Analysis plan ");
    title.append(badge(turn.plan.status));
    plan.append(title);
    const steps = node("ol");
    turn.plan.steps.forEach((step, index) => {
      const done = turn.plan.done.includes(index);
      steps.append(node("li", (done ? "Completed · " : "") + step, done ? "done" : ""));
    });
    plan.append(steps);
    if (turn.status === "completed" && turn.plan.status === "approved" &&
        turn.plan.done.length !== turn.plan.steps.length) {
      plan.append(node("p", "Some plan steps were not reported complete.", "warning"));
    }
    if (turn.plan.reason) plan.append(node("p", turn.plan.reason));
    for (const d of turn.plan.deviations) {
      plan.append(node("p", "Approved change: " + d.change + " · " + d.reason, "warning"));
    }
    card.append(plan);
  }
  return card;
}

function figure(hash, alt) {
  const image = node("img");
  image.src = figureURL(hash);
  image.alt = alt;
  image.loading = "lazy";
  return image;
}

function figureCard(item) {
  const card = panel("figure");
  card.append(node("h2", "Recorded figure"), figure(item.hash, "Figure from the recorded calculation"));
  return card;
}

function activityLabel(item) {
  switch (item.type) {
    case "tool_call_requested": return "Running " + item.tool;
    case "tool_call_finished": return item.empty_result ? item.tool + " · No items returned" : (item.is_error ? "Failed " : "Completed ") + item.tool;
    case "tool_call_failed": return item.tool + " · " + item.error;
    case "model_call_started": return item.role + " · " + item.model;
    case "model_call_finished": return `${item.role} · ${item.input_tokens + item.output_tokens} tokens` +
      (item.cost_known ? ` · est. $${item.cost_usd.toFixed(6)}` : " · Price unavailable");
    case "model_call_failed": return item.model + " · " + item.error;
    case "permission_decided": return (item.granted ? "Allowed " : "Denied ") + item.permission;
    case "plan_step_done": return "Completed plan step " + (item.step_index + 1);
    case "plan_deviation": return "Approved plan deviation";
    case "run_finished": return "Calculation recorded";
    case "run_failed": return "Calculation failed · " + item.error;
    case "run_cancelled": return "Calculation interrupted";
    case "run_timed_out": return "Calculation timed out";
    case "rerun_started": return "Rerun started";
    case "rerun_finished": return "Rerun comparison recorded";
    case "replication_started": return "Independent replication started";
    case "replication_finished": return "Replication · " + item.outcome;
    case "bundle_exported": return "Research bundle saved";
    case "literature_started": return "Literature lookup · " + item.question;
    case "literature_finished": return "Literature lookup · " + item.outcome +
      (item.reason ? " · " + item.reason : "") +
      " · " + (item.input_tokens + item.output_tokens) + " tokens";
    case "review_started": return "Review started";
    case "review_finished": return (item.limitations?.length ? "Review incomplete · " : "Review finished · ") + item.finding_count + " findings";
    case "error": return item.where + " · " + item.error;
    case "exploration_stopped": return "Exploration stopped · " + item.reason;
    default: return item.type;
  }
}

function render(data) {
  const questionCards = data.questions.map((q) => ({seq: q.seq, el: turnCard({question: q.text})}));
  const cards = [
    ...questionCards,
    ...data.turns.map((t) => ({seq: t.seq, el: turnCard(t)})),
    ...data.figures.map((f) => ({seq: f.seq, el: figureCard(f)})),
    ...data.answers.map((a) => ({seq: a.seq, el: answerCard(a)})),
  ].sort((a, b) => a.seq - b.seq);
  $("record").replaceChildren(...(cards.length ? cards.map((item) => item.el) : [intro()]));
  $("no-activity").hidden = data.activity.length > 0;
  $("activity").replaceChildren(...data.activity.slice().reverse().map((item) => {
    const li = node("li", activityLabel(item));
    const time = node("time", new Date(item.time).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"}), "muted");
    time.dateTime = item.time;
    li.append(time);
    return li;
  }));
  const running = data.turns.some((t) => t.seq >= 0 && t.status === "running");
  const pending = data.pending_plan;
  const actions = [];
  if (running) {
    const card = panel();
    card.append(node("p", "Research is running. Stop it to send a new direction in this session."));
    card.append(button("Stop research", () => mutate(sessionURL() + "/stop", {})));
    actions.push(card);
  }
  if (pending) {
    const card = panel();
    card.append(node("p", "Approve the recorded plan to start the analysis."));
    const buttons = node("div", undefined, "actions");
    const path = sessionURL() + "/turns/" + pending + "/plan";
    buttons.append(button("Approve and run", () => mutate(path, {approve: true}), "primary"));
    const proposal = data.turns.find((t) => t.id === pending);
    if (proposal?.plan && !project.example) {
      buttons.append(button("Revise plan", async () => {
        await mutate(path, {approve: false, reason: "Researcher requested a revision"});
        $("question").value = proposal.question;
        $("steps").value = proposal.plan.steps.join("\n");
        $("plan-mode").checked = true;
        $("plan-editor").hidden = false;
        $("submit").textContent = "Submit plan";
        $("steps").focus();
      }));
    }
    buttons.append(button("Decline plan", () => mutate(path, {approve: false, reason: "Declined by researcher"})));
    card.append(buttons);
    actions.push(card);
  }
  for (const permission of data.permissions) {
    const card = panel();
    card.append(node("h2", "Permission requested"), node("p", permission.reason + " · " + permission.permission),
      node("p", permission.target, "segment"));
    const row = node("div", undefined, "actions");
    const path = sessionURL() + "/permissions/" + permission.request_id;
    row.append(button("Allow once", () => mutate(path, {grant: true}), "primary"),
      button("Deny", () => mutate(path, {grant: false})));
    card.append(row);
    actions.push(card);
  }
  $("controls").replaceChildren(...actions);
  $("fork").disabled = running || !!pending;
  $("export").disabled = running || !data.answers.length;
  $("composer").hidden = false;
  const demoDone = project.example && data.turns.some((t) => t.status !== "declined" && t.status !== "queued") && !running;
  $("question").disabled = running || !!pending || demoDone;
  $("steps").disabled = $("question").disabled;
  $("plan-mode").disabled = $("question").disabled;
  $("submit").disabled = $("question").disabled;
  if (demoDone) $("question").placeholder = "Start a new session to run the example again.";
  else $("question").placeholder = "What would you like to investigate?";
}

async function mutate(path, body) {
  setMessage();
  await request(path, body);
  await refresh();
}

function highlight(source, quote) {
  const text = node("pre");
  const escape = (term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(quote.split(/\s+/).map(escape).join("\\s+"));
  const match = pattern.exec(source);
  if (!match) {
    text.append(node("span", "Verified quote from decoded source: "), node("mark", quote), document.createTextNode("\n\n" + source));
    return text;
  }
  text.append(document.createTextNode(source.slice(0, match.index)), node("mark", match[0]),
    document.createTextNode(source.slice(match.index + match[0].length)));
  return text;
}

async function openEvidence(answerId, ref) {
  const base = sessionURL();
  const target = ref.type === "quote" ? ref.call_id : ref.run_id;
  const data = await request(base + "/answers/" + answerId + "/" + ref.type + "/" + target);
  if (base !== sessionURL()) return;
  const body = $("evidence-body");
  body.replaceChildren();
  const section = (label, value) => body.append(node("h3", label), node("pre", value));
  if (ref.type === "quote") {
    $("evidence-title").textContent = ref.source;
    if (/^PMID:\d+$/.test(ref.source)) {
      const link = node("a", "Open full record in PubMed");
      link.href = "https://pubmed.ncbi.nlm.nih.gov/" + ref.source.slice(5) + "/";
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      body.append(link);
    }
    if (data.task_id) body.append(node("p", data.scope || "Retrieved by a literature subtask", "muted"));
    body.append(node("h3", "Recorded source · " + data.tool), highlight(data.source, ref.quote));
  } else {
    $("evidence-title").textContent = ref.name;
    const value = data.values.find((item) => item.name === ref.name);
    if (value) body.append(node("p", value.value + (value.unit ? " " + value.unit : "")));
    if (data.previous_cells?.length) {
      section("Earlier cells in this calculation", data.previous_cells.map((cell) => cell.code).join("\n\n"));
    }
    section("Code", data.code);
    section("Output", data.outputs.map((item) => item.text).join("\n") || "No text output recorded.");
    if (data.figures.length) {
      body.append(node("h3", "Figures"));
      for (const hash of data.figures) body.append(figure(hash, "Figure from this calculation"));
    }
    section("Input hashes", data.inputs.map((input) => input.content_hash + "\n" + (input.path || input.uri)).join("\n\n") || "No file inputs declared.");
    section("Code hash", data.code_hash);
    if (data.stdout_hash) section("Output hash", data.stdout_hash);
    if (data.lineage?.length) {
      section("Observed I/O (post-run)", data.lineage.map((item) =>
        item.direction + " · " + item.function + " · " + item.path +
        (item.post_run_hash ? "\n" + item.post_run_hash : "")).join("\n"));
    }
    if (data.isolation) body.append(node("p", "Kernel isolation: " + data.isolation, "muted"));
  }
  if (!$("evidence").open) $("evidence").showModal();
}

function nameDialog(mode) {
  naming = mode;
  $("dialog-title").textContent = mode === "fork" ? "Fork session" : "New session";
  $("session-title").value = mode === "fork" ? $("session-name").textContent.slice(0, 152) + " · fork" :
    project.example ? "Metformin evidence" : "";
  $("name-dialog").showModal();
}
$("new").addEventListener("click", () => nameDialog("session"));
$("fork").addEventListener("click", () => nameDialog("fork"));
$("cancel-name").addEventListener("click", () => $("name-dialog").close());
$("name-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const path = naming === "fork" ? sessionURL() + "/fork" : "/api/sessions";
  try {
    const row = await request(path, {title: $("session-title").value});
    $("name-dialog").close();
    await openSession(row.id);
  } catch (err) {
    $("name-dialog").close();
    showError(err);
  }
});
$("plan-mode").addEventListener("change", (event) => {
  $("plan-editor").hidden = !event.target.checked;
  $("submit").textContent = event.target.checked ? "Submit plan" : "Run research";
});
$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const steps = $("plan-mode").checked ? $("steps").value.split("\n").map((s) => s.trim()).filter(Boolean) : null;
  try {
    await mutate(sessionURL() + "/turns", {question: $("question").value, steps});
    $("question").value = "";
  } catch (err) {
    showError(err);
  }
});
window.addEventListener("pagehide", () => stream?.close());
window.addEventListener("pageshow", (event) => {
  if (event.persisted) select(sessionParam()).catch(showError);
});
list().then(() => {
  const wanted = sessionParam();
  return select(project.sessions.find((row) => row.id === wanted)?.id ?? project.sessions[0]?.id, {updateURL: true});
}).catch(showError);
