"""Drive asynchronous research while recording all user-visible transitions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from bioquorum.agent import Hooks
from bioquorum.application import research
from bioquorum.application import workspace as wsp
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, PermissionScope
from bioquorum.core.ids import new_id
from bioquorum.policy.permissions import Decision, PermissionPolicy, PermissionRequest
from bioquorum.policy.secrets import redact
from bioquorum.runtime.manager import KernelFactory
from bioquorum.storage.events import EventStore
from bioquorum.tools import ToolContext, ToolRegistry, default_registry
from bioquorum.tools.invoke import invoke_by_name

Outcome = Literal["completed", "failed", "interrupted", "declined"]


class ConflictError(ValueError):
    pass


@dataclass
class LiveTurn:
    task: asyncio.Task[None]
    turn_id: str
    stopping: bool = False
    characters: int = 0
    permissions: dict[str, asyncio.Future[Decision]] = field(default_factory=dict)


class ResearchControls:
    def __init__(
        self,
        workspace: wsp.Workspace,
        *,
        make_client: research.ClientFactory = research.model_client,
        kernel_factory: KernelFactory | None = None,
        registry_factory: Callable[[], ToolRegistry] = default_registry,
        demo: bool = False,
    ) -> None:
        self.ws = workspace
        self.store = EventStore(workspace.database())
        self.make_client = make_client
        self.kernel_factory = kernel_factory
        self.registry_factory = registry_factory
        self.demo = demo
        self.tasks: dict[str, LiveTurn] = {}
        self.reviewing = False

    def sessions(self) -> list[dict]:
        return self.store.list_sessions(self.ws.project_id)

    def require_session(self, sid: str) -> list[ev.Event]:
        events = self.store.read(sid)
        if not events:
            raise LookupError("Session not found")
        root = events[0].parsed()
        if (
            not isinstance(root, ev.SessionCreated | ev.SessionForked)
            or root.project_id != self.ws.project_id
        ):
            raise LookupError("Session not found")
        return events

    def new_session(self, title: str) -> str:
        return wsp.start_session(self.ws, self.store, title)

    def _append(self, sid: str, payload: ev.EventPayload, actor: Actor, cause: str) -> ev.Event:
        return self.store.append(payload, session_id=sid, actor=actor, causation_id=cause)

    def _finish(self, sid: str, turn_id: str, outcome: Outcome, error: str = "") -> None:
        self._append(
            sid,
            ev.TurnFinished(turn_id=turn_id, outcome=outcome, error=error),
            Actor.SYSTEM,
            turn_id,
        )

    @staticmethod
    def pending(events: list[ev.Event]) -> tuple[ev.Event, ev.PlanProposed] | None:
        turns = {}
        pending = None
        for event in events:
            payload = event.parsed()
            if isinstance(payload, ev.TurnRequested):
                turns[event.id] = event
            elif isinstance(payload, ev.PlanProposed) and event.causation_id in turns:
                pending = (turns[event.causation_id], payload)
            elif pending and (
                (
                    isinstance(payload, (ev.PlanApproved, ev.PlanAbandoned))
                    and payload.plan_id == pending[1].plan_id
                )
                or (
                    isinstance(payload, (ev.TurnStarted, ev.TurnFinished))
                    and payload.turn_id == pending[0].id
                )
            ):
                pending = None
        return pending

    def _ensure_idle(self) -> None:
        if self.tasks or self.reviewing:
            raise ConflictError("A research operation is already running in this project")

    def fork(self, sid: str, title: str) -> str:
        self._ensure_idle()
        events = self.require_session(sid)
        if self.pending(events):
            raise ConflictError("Decide the pending plan before forking")
        child = new_id()
        self.store.append(
            ev.SessionForked(
                project_id=self.ws.project_id,
                parent_session_id=sid,
                forked_at_seq=events[-1].seq,
                title=title,
            ),
            session_id=child,
            actor=Actor.USER,
        )
        return child

    def submit(self, sid: str, question: str, *, steps: list[str] | None = None) -> ev.Event:
        self._ensure_idle()
        events = self.require_session(sid)
        if self.pending(events):
            raise ConflictError("Decide the pending plan before submitting a question")
        question = question.strip()
        if not question or len(question) > 12000:
            raise ValueError("Enter a question between 1 and 12000 characters")
        if research.literature_request(question) is not None:
            if steps is not None:
                raise ValueError("Use /literature in a direct turn")
            if not self.ws.agent_options().literature_subtasks:
                raise ValueError("Enable literature_subtasks before using /literature")
        question = redact(question)
        if steps is not None:
            steps = [redact(step.strip()) for step in steps]
            if not 1 <= len(steps) <= 12 or any(not step or len(step) > 600 for step in steps):
                raise ValueError("Enter 1 to 12 plan steps, each at most 600 characters")
        if self.demo:
            from bioquorum.application.demo import check_request

            check_request(self.store.history(sid), question, steps)
        plan_id = new_id() if steps else None
        turn = self._append(
            sid, ev.TurnRequested(question=question, plan_id=plan_id), Actor.USER, events[-1].id
        )
        if plan_id and steps:
            self._append(sid, ev.PlanProposed(plan_id=plan_id, steps=steps), Actor.USER, turn.id)
        else:
            self._start(sid, turn.id, question, None)
        return turn

    async def decide_plan(self, sid: str, turn_id: str, *, approve: bool, reason: str = "") -> None:
        self._ensure_idle()
        pending = self.pending(self.require_session(sid))
        if pending is None or pending[0].id != turn_id:
            raise ConflictError("That plan is no longer pending")
        turn, plan = pending
        payload = turn.parsed()
        assert isinstance(payload, ev.TurnRequested)
        if approve:
            self._append(sid, ev.PlanApproved(plan_id=plan.plan_id), Actor.USER, turn_id)
            self._start(sid, turn_id, payload.question, plan)
        else:
            self._append(
                sid, ev.PlanAbandoned(plan_id=plan.plan_id, reason=reason), Actor.USER, turn_id
            )
            self._finish(sid, turn_id, "declined")

    def _start(self, sid: str, turn_id: str, question: str, plan: ev.PlanProposed | None) -> None:
        self._append(sid, ev.TurnStarted(turn_id=turn_id), Actor.SYSTEM, turn_id)
        task = asyncio.create_task(self._run(sid, turn_id, question, plan))
        self.tasks[sid] = LiveTurn(task, turn_id)

    async def _run(
        self, sid: str, turn_id: str, question: str, plan: ev.PlanProposed | None
    ) -> None:
        live = self.tasks[sid]

        async def ask(req: PermissionRequest) -> Decision:
            assert req.request_id is not None
            future: asyncio.Future[Decision] = asyncio.get_running_loop().create_future()
            live.permissions[req.request_id] = future
            try:
                return await future
            finally:
                live.permissions.pop(req.request_id, None)

        def on_delta(text: str) -> None:
            live.characters += len(text)

        try:
            client = self.make_client(self.ws, self.store)
            turn = await research.execute(
                self.ws,
                self.store,
                sid,
                question,
                client=client,
                plan=plan,
                hooks=Hooks(on_model_delta=on_delta if self.ws.agent_options().stream else None),
                permissions=lambda servers: wsp.default_permissions(
                    self.ws,
                    allow=[],
                    servers=servers,
                    ask=ask,
                ),
                kernel_factory=self.kernel_factory,
                registry_factory=self.registry_factory,
            )
            try:
                reviewed = await research.audit(
                    self.ws, self.store, sid, client, answer_event_id=turn.answer_event_id
                )
                self._check_failures(sid, turn.answer_event_id, reviewed.errors)
            except Exception as exc:
                self._check_failures(sid, turn.answer_event_id, [str(exc)])
            self._finish(sid, turn_id, "completed")
        except asyncio.CancelledError:
            self._finish(sid, turn_id, "interrupted")
            raise
        except Exception as exc:
            self._finish(sid, turn_id, "failed", redact(str(exc))[:600])
        finally:
            self.tasks.pop(sid, None)

    def _check_failures(self, sid: str, cause: str, errors: list[str]) -> None:
        for message in errors:
            self._append(
                sid,
                ev.ErrorOccurred(where="review", error=redact(message)[:600]),
                Actor.REVIEWER,
                cause,
            )

    async def review(self, sid: str, answer_id: str) -> None:
        self._ensure_idle()
        events = self.require_session(sid)
        if not any(
            e.id == answer_id
            and isinstance(p := e.parsed(), ev.AssistantMessage)
            and p.message.text().strip()
            and not p.message.tool_calls()
            for e in events
        ):
            raise LookupError("Answer not found in this session")
        self.reviewing = True
        try:
            result = await research.audit(
                self.ws,
                self.store,
                sid,
                self.make_client(self.ws, self.store),
                answer_event_id=answer_id,
            )
            self._check_failures(sid, answer_id, result.errors)
        except Exception as exc:
            self._check_failures(sid, answer_id, [str(exc)])
            raise ConflictError("Review failed. See the activity log.") from None
        finally:
            self.reviewing = False

    def decide_permission(self, sid: str, request_id: str, grant: bool) -> None:
        self.require_session(sid)
        live = self.tasks.get(sid)
        future = live.permissions.get(request_id) if live else None
        if future is None or future.done():
            raise ConflictError("That permission is no longer pending")
        future.set_result(Decision(granted=grant, scope=PermissionScope.ONCE if grant else None))

    async def stop(self, sid: str) -> None:
        self.require_session(sid)
        live = self.tasks.get(sid)
        if live is None or live.stopping:
            raise ConflictError("No running research turn to stop")
        live.stopping = True
        live.task.cancel()
        await asyncio.gather(live.task, return_exceptions=True)
        if self.tasks.get(sid) is live:
            del self.tasks[sid]
            # A task cancelled before its first instruction never reaches _run's finally.
            self._finish(sid, live.turn_id, "interrupted")

    async def close(self) -> None:
        for sid in list(self.tasks):
            if sid in self.tasks and not self.tasks[sid].stopping:
                await self.stop(sid)

    async def recover(self) -> None:
        """Close incomplete turns left by an earlier process, without re-executing anything."""
        for row in self.sessions():
            sid = row["id"]
            events = self.require_session(sid)
            pending = self.pending(events)
            ended = {p.turn_id for e in events if isinstance(p := e.parsed(), ev.TurnFinished)}
            for e in events:
                if (
                    isinstance(e.parsed(), ev.TurnRequested)
                    and e.id not in ended
                    and (pending is None or pending[0].id != e.id)
                ):
                    await self._recover_calls(sid, events[e.seq + 1 :])
                    self._finish(sid, e.id, "interrupted", "The previous process exited.")

    async def recover_literature(self) -> None:
        """Close an interrupted child log without running the literature tools again."""
        from bioquorum.agent.literature import usage

        for row in self.sessions():
            sid = row["id"]
            events = self.require_session(sid)
            closed = {
                p.task_id for e in events if isinstance(p := e.parsed(), ev.LiteratureFinished)
            }
            for event in events:
                payload = event.parsed()
                if isinstance(payload, ev.LiteratureStarted) and payload.task_id not in closed:
                    child = self.store.read(payload.task_id)
                    await self._recover_calls(payload.task_id, child)
                    self._append(
                        sid,
                        ev.LiteratureFinished(
                            task_id=payload.task_id,
                            parent_call_id=payload.parent_call_id,
                            outcome="cancelled",
                            prefetched=payload.prefetched,
                            reason="Previous process exited.",
                            **usage(self.store.read(payload.task_id)),
                        ),
                        Actor.SYSTEM,
                        event.id,
                    )

    async def recover_verification(self) -> None:
        """Record an inconclusive result when a local verification process has exited."""
        for row in self.sessions():
            sid = row["id"]
            events = self.require_session(sid)
            reruns = {p.rerun_id for e in events if isinstance(p := e.parsed(), ev.RerunFinished)}
            replications = {
                p.replication_id
                for e in events
                if isinstance(p := e.parsed(), ev.ReplicationFinished)
            }
            for event in events:
                p = event.parsed()
                note = "The previous process exited before completing the comparison."
                if isinstance(p, ev.RerunStarted) and p.rerun_id not in reruns and p.claim_id:
                    await self._recover_calls(sid, events[event.seq + 1 :])
                    self._append(
                        sid,
                        ev.RerunFinished(
                            rerun_id=p.rerun_id,
                            matched=False,
                            details={
                                "claim_id": p.claim_id,
                                "outcome": "inconclusive",
                                "runs": [{"reason": note}],
                            },
                        ),
                        Actor.SYSTEM,
                        event.id,
                    )
                elif isinstance(p, ev.ReplicationStarted) and p.replication_id not in replications:
                    await self._recover_calls(
                        p.blind_session_id, self.require_session(p.blind_session_id)
                    )
                    self._append(
                        sid,
                        ev.ReplicationFinished(
                            replication_id=p.replication_id,
                            claim_id=p.claim_id,
                            outcome="inconclusive",
                            note=note,
                        ),
                        Actor.SYSTEM,
                        event.id,
                    )

    async def _recover_calls(self, sid: str, events: list[ev.Event]) -> None:
        reason = "The previous process exited."
        models: dict[str, tuple[str, ev.ModelCallStarted]] = {}
        tools: dict[str, tuple[str, str]] = {}
        runs: dict[str, str] = {}
        requests: dict[str, tuple[str, ev.PermissionRequested]] = {}
        for e in events:
            p = e.parsed()
            if isinstance(p, ev.ModelCallStarted):
                models[p.call_id] = (e.id, p)
            elif isinstance(p, ev.ModelCallFinished | ev.ModelCallFailed):
                models.pop(p.call_id, None)
            elif isinstance(p, ev.AssistantMessage):
                for call in p.message.tool_calls():
                    tools[call.call_id] = (e.id, call.name)
            elif isinstance(p, ev.ToolCallRequested):
                tools[p.call_id] = (e.id, p.tool)
            elif isinstance(p, ev.ToolCallFinished | ev.ToolCallFailed):
                tools.pop(p.call_id, None)
            elif isinstance(p, ev.RunStarted):
                runs[p.run_id] = e.id
            elif isinstance(p, ev.RunFinished | ev.RunFailed | ev.RunCancelled | ev.RunTimedOut):
                runs.pop(p.run_id, None)
            elif isinstance(p, ev.PermissionRequested):
                requests[p.request_id] = (e.id, p)
            elif isinstance(p, ev.PermissionDecided):
                requests.pop(p.request_id, None)
        for start, model in models.values():
            self._append(
                sid,
                ev.ModelCallFailed(
                    call_id=model.call_id,
                    provider=model.provider,
                    model=model.model,
                    role=model.role,
                    error=reason,
                ),
                Actor.SYSTEM,
                start,
            )
        for run_id, start in runs.items():
            self._append(sid, ev.RunCancelled(run_id=run_id), Actor.SYSTEM, start)
        for start, request in requests.values():
            self._append(
                sid,
                ev.PermissionDecided(
                    request_id=request.request_id,
                    permission=request.permission,
                    target=request.target,
                    granted=False,
                ),
                Actor.SYSTEM,
                start,
            )
        if tools:
            ctx = ToolContext(store=self.store, session_id=sid, permissions=PermissionPolicy())
            registry = ToolRegistry()
            for call_id, (start, name) in tools.items():
                await invoke_by_name(
                    registry,
                    name,
                    {},
                    ctx,
                    call_id=call_id,
                    causation_id=start,
                    skip_reason=reason,
                )
