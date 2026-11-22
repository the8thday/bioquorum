import { useEffect, useState } from "react";
import { Dialog } from "./Dialog";
import { errorMessage, request } from "./api";

interface Item {
  id: string;
  session: string;
  title: string;
  kind: string;
  text: string;
  program?: string;
}

interface Child {
  id: string;
  question: string;
  specialist?: string;
  status: string;
  input_tokens: number;
  output_tokens: number;
  model_calls: number;
  priced_calls: number;
  cost_usd: number;
}

interface Task {
  id: string;
  session: string;
  title: string;
  question: string;
  status: string;
  children: Child[];
}

interface Props {
  inbox?: boolean;
  open: (session: string, anchor: string) => void;
  close: () => void;
}

function decisionLink(program: string, id: string) {
  const url = new URL(location.href);
  url.searchParams.delete("home");
  url.searchParams.set("view", "program");
  url.searchParams.set("program", program);
  url.hash = "decision-" + id;
  return url.href;
}

export function Tasks({open, close, inbox = false}: Props) {
  const [tasks, setTasks] = useState<Task[]>();
  const [items, setItems] = useState<Item[]>();
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        if (inbox) setItems(await request<Item[]>("/api/inbox", undefined, controller.signal));
        else setTasks(await request<Task[]>("/api/tasks", undefined, controller.signal));
        setError("");
      } catch (e) {
        if (!controller.signal.aborted) setError(errorMessage(e));
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 3000);
      }
    }
    void refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [inbox]);

  return <Dialog id="tasks" title={inbox ? "Inbox" : "Research tasks"} close={close} closeLabel="Close list">
    {error && <p role="alert">{error}</p>}
    {items?.length === 0 && <p>No pending items.</p>}
    {items?.map(item => <article key={item.id}>
      <p>{item.title} · {item.kind}</p>
      {item.program
        ? <a href={decisionLink(item.program, item.id)} onClick={close}>{item.text}</a>
        : <button className="quiet" onClick={() => open(item.session,
            (item.kind === "Review" ? "claim-" : "record-") + item.id)}>{item.text}</button>}
    </article>)}
    {tasks?.length === 0 && <p>No research tasks yet.</p>}
    {tasks?.map(task => <article key={task.id}>
      <p>{task.title} · {task.status}</p>
      <button className="quiet" onClick={() => open(task.session, "record-" + task.id)}>{task.question}</button>
      {task.children.length > 0 && <ul>{task.children.map(child => <li key={child.id}>
        {child.question} · {child.status}{child.specialist && " · " + child.specialist}
        <small> · {child.model_calls} model calls · {child.input_tokens + child.output_tokens} tokens
          {child.priced_calls > 0 && " · $" + child.cost_usd.toFixed(6)}
          {child.model_calls > child.priced_calls && " · incomplete pricing"}</small>
      </li>)}</ul>}
    </article>)}
  </Dialog>;
}
