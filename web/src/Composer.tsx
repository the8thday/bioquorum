import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import type { DraftReference, PermissionMode, Project, ReasoningEffort, Snapshot } from "./types";

import { Icon } from "./Icon";
import type { Product } from "./Artifacts";

function shortModel(spec: string) {
  const idx = spec.indexOf(":");
  return idx >= 0 ? spec.slice(idx + 1) : spec;
}

export const permissionLabels: Record<string, string> = {
  read_fs: "Read files",
  write_fs: "Write files",
  execute: "Run code",
  network: "Network",
  connector: "Database connector",
  external_mcp: "MCP server",
};

const modeDescriptions: Record<PermissionMode, string> = {
  ask: "Ask before network requests, database connectors, and writes",
  auto: "Auto-approve safe public database and network lookups; ask on plan deviations",
  full: "Auto-approve all permitted tools (classification, sandbox, and revocations still apply)",
};

export function Composer({
  draftPlan,
  initialDraft,
  executing,
  connection,
  upload,
  openSettings,
  chooseModel,
  attachments,
  setAttachments,
  refs,
  setRefs,
  literatureEnabled,
  planDefault,
  record,
  example,
  worker,
  setWorker,
  effort,
  setEffort,
  permissionMode = "auto",
  setPermissionMode,
  defaultWorker,
  models,
  classification,
  preset,
  mutate,
  report,
}: {
  chooseModel?: () => void;
  draftPlan: (q: string) => Promise<{ steps: string[]; model: string }>;
  initialDraft?: string;
  executing: boolean;
  connection: string;
  upload: ReactNode;
  openSettings: () => void;
  attachments: Product[];
  setAttachments: (p: Product[]) => void;
  refs: DraftReference[];
  setRefs: (items: DraftReference[]) => void;
  literatureEnabled?: boolean;
  planDefault?: boolean;
  worker: string;
  setWorker: (value: string) => void;
  effort: ReasoningEffort | "";
  setEffort: (value: ReasoningEffort | "") => void;
  permissionMode?: PermissionMode;
  setPermissionMode?: (value: PermissionMode) => void;
  defaultWorker?: string | null;
  models: Project["worker_models"];
  classification: string;
  record: Snapshot;
  example: Project["example"];
  preset: number;
  mutate: (suffix: string, body: unknown) => Promise<void>;
  report: (error: unknown) => void;
}) {
  const [question, setQuestion] = useState(initialDraft ?? "");
  useEffect(() => {
    if (initialDraft) {
      setQuestion(initialDraft);
      requestAnimationFrame(() => questionRef.current?.focus());
    }
  }, [initialDraft]);
  const [steps, setSteps] = useState("");
  const [literature, setLiterature] = useState(false);
  const [plan, setPlan] = useState(!!planDefault);
  useEffect(() => setPlan(!!planDefault), [planDefault]);
  const saved = record.plan_draft;
  const restored = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (saved && restored.current !== saved.question) {
      restored.current = saved.question;
      if (!question.trim()) {
        setQuestion(saved.question);
        setSteps(saved.steps.join("\n"));
        setPlan(true);
      }
    }
  }, [saved, question]);

  const [busy, setBusy] = useState(false);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const stepsRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (preset && example) {
      setQuestion(example.question);
      setSteps(example.steps.join("\n"));
      setPlan(true);
      questionRef.current?.focus();
    }
  }, [preset, example]);

  // Auto-grow textarea height smoothly up to 200px
  useEffect(() => {
    const el = questionRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [question]);

  const effectiveModel = worker || defaultWorker || "";
  const activeModelInfo = models.find((m) => m.spec === effectiveModel);
  const supportsReasoning = !example && !!activeModelInfo?.reasoning;
  useEffect(() => {
    if (!supportsReasoning && effort) setEffort("");
  }, [supportsReasoning, effort, setEffort]);

  const running = record.turns.some((t) => t.seq >= 0 && t.status === "running");
  const interrupted = record.turns.find((t) => t.seq >= 0 && t.resume_checks !== undefined);
  const checks = Object.entries(interrupted?.resume_checks ?? {});
  const demoDone = !!example && !running && record.turns.some((t) => t.status !== "declined" && t.status !== "queued");
  const disabled = busy || running || !!record.pending_plan || demoDone;

  const focused = useRef<DraftReference | undefined>(undefined);
  useEffect(() => {
    const newest = refs.at(-1);
    if (newest && newest !== focused.current && !disabled) questionRef.current?.focus();
    if (!disabled) focused.current = newest;
  }, [refs, disabled]);

  const proposal = record.turns.find((t) => t.id === record.pending_plan);
  const planPath = "/turns/" + record.pending_plan + "/plan";

  async function act(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } catch (e) {
      report(e);
    } finally {
      setBusy(false);
    }
  }

  const notes = refs.flatMap((r, i) =>
    r.note ? [refs.length === 1 && !question.trim() ? r.note : "Reference " + (i + 1) + ": " + r.note] : []
  );
  const message = [question.trim(), ...notes].filter(Boolean).join("\n\n");
  const lookup = literatureEnabled && !example && !plan && literature;

  function submitQuestion() {
    if (disabled) return;
    if (chooseModel) {
      chooseModel();
      return;
    }
    if (!message || message.length > 12000) {
      report(new Error("Enter a question of up to 12,000 characters, including reference notes."));
      return;
    }
    const query = lookup ? message.replace(/^\/literature(?:\s+|$)/, "").trim() : message;
    if (lookup && (!query || [...query].length > 1200)) {
      report(new Error("Enter a literature question of 1 to 1,200 characters."));
      return;
    }
    void act(async () => {
      await mutate("/turns", {
        question: lookup ? "/literature " + query : message,
        attachments: attachments.map((a) => a.id),
        references: refs.map((r) => r.item),
        worker_model: worker || null,
        reasoning_effort: supportsReasoning && effort ? effort : null,
        permission_mode: example ? null : permissionMode,
        steps: plan ? steps.split("\n").map((s) => s.trim()).filter(Boolean) : null,
      });
      setAttachments([]);
      setRefs([]);
      setQuestion("");
      setLiterature(false);
    });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    submitQuestion();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submitQuestion();
    }
  }

  return (
    <form id="composer" className={demoDone ? "finished-example" : undefined} onSubmit={submit}>
      <div id="controls">
        {interrupted && (
          <article className="panel control-card">
            <h2>Interrupted research</h2>
            {checks.length > 0 && (
              <>
                <p>Verify the outcome of these operations before continuing.</p>
                <ul>
                  {checks.map(([id, tool]) => (
                    <li key={id}>
                      {tool} · {id}
                    </li>
                  ))}
                </ul>
              </>
            )}
            <button
              type="button"
              className="quiet"
              disabled={busy || running}
              onClick={() =>
                act(() => mutate("/turns/" + interrupted.id + "/resume", { checked_calls: checks.map(([id]) => id) }))
              }
            >
              {checks.length ? "I checked these operations — resume research" : "Resume research"}
            </button>
          </article>
        )}
        {running && (
          <article className="panel run-status">
            <p role="status" className={executing ? "running-indicator" : ""}>
              {record.permissions.length
                ? "Awaiting your permission"
                : executing
                  ? "Research running"
                  : connection || "Checking session status…"}
            </p>
            <button type="button" className="quiet stop-button" disabled={busy} onClick={() => act(() => mutate("/stop", {}))}>
              <Icon name="stop" />
              <span>Stop research</span>
            </button>
          </article>
        )}
        {record.pending_plan && (
          <article className="panel control-card">
            <p>Approve the recorded plan to start the analysis.</p>
            <div className="actions">
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() => act(() => mutate(planPath, { approve: true }))}
              >
                Approve and run
              </button>
              {proposal?.plan && !example && (
                <button
                  type="button"
                  className="quiet"
                  disabled={busy}
                  onClick={() =>
                    act(async () => {
                      await mutate(planPath, { approve: false, reason: "Researcher requested a revision" });
                      setQuestion(proposal.question);
                      setSteps(proposal.plan!.steps.join("\n"));
                      setPlan(true);
                      requestAnimationFrame(() => stepsRef.current?.focus());
                    })
                  }
                >
                  Revise plan
                </button>
              )}
              <button
                type="button"
                className="quiet"
                disabled={busy}
                onClick={() => act(() => mutate(planPath, { approve: false, reason: "Declined by researcher" }))}
              >
                Decline plan
              </button>
            </div>
          </article>
        )}
        {record.permissions.map((p) => (
          <article className="panel permission-card" id={"record-" + p.request_id} key={p.request_id}>
            <div className="permission-head">
              <Icon name="shield" />
              <h2>Permission requested</h2>
              <span className="badge proposed">{permissionLabels[p.permission] ?? p.permission}</span>
            </div>
            <p className="muted">
              Tool: <strong>{p.reason}</strong> · {p.permission}
            </p>
            <p className="permission-target">{p.target}</p>
            <div className="actions">
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() => act(() => mutate("/permissions/" + p.request_id, { grant: true, remember: true }))}
              >
                Allow for this session
              </button>
              <button
                type="button"
                className="quiet"
                disabled={busy}
                onClick={() => act(() => mutate("/permissions/" + p.request_id, { grant: true, remember: false }))}
              >
                Allow once
              </button>
              <button
                type="button"
                className="quiet danger"
                disabled={busy}
                onClick={() => act(() => mutate("/permissions/" + p.request_id, { grant: false }))}
              >
                Deny
              </button>
            </div>
          </article>
        ))}
      </div>

      {attachments.length > 0 && (
        <ul className="file-tray" aria-label="Attached files">
          {attachments.map((a) => (
            <li key={a.id}>
              <Icon name="paperclip" />
              <span>{a.name}</span>
              <button
                className="icon-button"
                title="Remove attachment"
                aria-label="Remove attachment"
                type="button"
                onClick={() => setAttachments(attachments.filter((p) => p.id !== a.id))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      {refs.length > 0 && (
        <ul className="reference-tray" aria-label="References">
          {refs.map((r, i) => (
            <li key={i}>
              <span className="reference-kind">{r.item.kind}</span>
              <div className="reference-copy">
                <span className="reference-label" title={r.label}>
                  {r.label}
                </span>
                {r.note && <span className="reference-note">{r.note}</span>}
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label="Remove reference"
                onClick={() => setRefs(refs.filter((_, j) => j !== i))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      <label htmlFor="question" className="sr-only">
        Research question
      </label>
      <textarea
        id="question"
        ref={questionRef}
        rows={2}
        maxLength={12000}
        required={!notes.length}
        disabled={disabled}
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={
          demoDone
            ? "Start a new session to run the example again."
            : lookup
              ? "Enter a focused literature query… (Enter to run, Shift+Enter for new line)"
              : "Ask a research question… (Enter to run, Shift+Enter for new line)"
        }
      />

      <div className="composer-footer">
        <div className="toolbar-left">
          {upload}
          <label
            className={"composer-pill" + (plan ? " active" : "") + (disabled || !!lookup ? " is-disabled" : "")}
            title="Review and approve an analysis plan before running"
          >
            <input
              type="checkbox"
              className="sr-only"
              aria-label="Review a plan first"
              checked={plan}
              disabled={disabled || !!lookup}
              onChange={(e) => setPlan(e.target.checked)}
            />
            <Icon name="list" />
            <span>Plan</span>
          </label>
          {literatureEnabled && !example && (
            <label
              className={"composer-pill" + (lookup ? " active" : "") + (disabled || plan ? " is-disabled" : "")}
              title="Run a dedicated literature search subtask"
            >
              <input
                type="checkbox"
                className="sr-only"
                aria-label="Dedicated literature lookup"
                checked={!!lookup}
                disabled={disabled || plan}
                onChange={(e) => setLiterature(e.target.checked)}
              />
              <Icon name="book" />
              <span>Literature</span>
            </label>
          )}
          {!example && (
            <label
              className={"composer-select perm-mode-picker" + (permissionMode === "full" ? " perm-full" : "")}
              title={modeDescriptions[permissionMode]}
            >
              <Icon name="shield" />
              <span className="sr-only">Permission mode</span>
              <select
                aria-label="Permission mode"
                title={modeDescriptions[permissionMode]}
                value={permissionMode}
                disabled={disabled}
                onChange={(e) => setPermissionMode?.(e.target.value as PermissionMode)}
              >
                <option value="ask">Ask</option>
                <option value="auto">Auto</option>
                <option value="full">Full access</option>
              </select>
              <Icon name="chevron" />
            </label>
          )}
          <button
            type="button"
            className="icon-button composer-icon chat-settings"
            onClick={openSettings}
            title="Chat settings"
            aria-label="Chat settings"
          >
            <Icon name="settings" />
          </button>
        </div>

        <div className="toolbar-right">
          {models.length > 0 && (
            <label className="composer-select model-picker" title="Worker model">
              <span className="sr-only">Worker model</span>
              <select
                aria-label="Worker model"
                title="Worker model"
                value={worker}
                disabled={disabled}
                onChange={(e) => setWorker(e.target.value)}
              >
                <option value="">
                  {defaultWorker ? shortModel(defaultWorker) : "Role default"}
                </option>
                {models.map((m) => (
                  <option key={m.spec} value={m.spec} disabled={!m.allowed}>
                    {m.spec}
                    {!m.allowed && " · unavailable for " + classification + " data"}
                  </option>
                ))}
              </select>
              <Icon name="chevron" />
            </label>
          )}
          {supportsReasoning && (
            <label className="composer-select effort-picker" title="Reasoning effort for this turn">
              <Icon name="sparkles" />
              <span className="sr-only">Reasoning effort</span>
              <select
                aria-label="Reasoning effort"
                title="Reasoning effort"
                value={effort}
                disabled={disabled}
                onChange={(e) => setEffort(e.target.value as ReasoningEffort | "")}
              >
                <option value="">Default</option>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
              <Icon name="chevron" />
            </label>
          )}
          <button
            type="submit"
            className="primary send-button"
            disabled={disabled}
            aria-label={plan ? "Submit plan" : "Run research"}
            title={plan ? "Submit plan" : "Run research"}
          >
            <Icon name="send" />
          </button>
        </div>
      </div>

      {plan && (
        <div id="plan-editor">
          {!example && (
            <button
              type="button"
              className="quiet"
              disabled={disabled || !question.trim()}
              onClick={() =>
                act(async () => {
                  const draft = await draftPlan(question);
                  setSteps(draft.steps.join("\n"));
                })
              }
            >
              Draft steps with planner
            </button>
          )}
          <label htmlFor="steps">
            Analysis plan <span className="muted">One step per line</span>
          </label>
          <textarea
            id="steps"
            ref={stepsRef}
            rows={3}
            maxLength={7320}
            value={steps}
            disabled={disabled}
            onChange={(e) => setSteps(e.target.value)}
            placeholder={"Identify the evidence needed\nCompare the findings and limits"}
          />
        </div>
      )}
    </form>
  );
}
