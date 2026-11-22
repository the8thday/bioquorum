"""Drive asynchronous research while recording all user-visible transitions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from bioquorum.agent import Hooks
from bioquorum.application import recovery, research
from bioquorum.application import workspace as wsp
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, PermissionScope
from bioquorum.core.ids import new_id
from bioquorum.models import Router
from bioquorum.policy.permissions import Decision, PermissionPolicy, PermissionRequest
from bioquorum.policy.secrets import redact
from bioquorum.runtime.manager import KernelFactory, SessionKernels
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
        store: EventStore | None = None,
        demo: bool = False,
    ) -> None:
        self.ws = workspace
        self.store = store if store is not None else EventStore(workspace.database())
        self.make_client = make_client
        self.kernel_factory = kernel_factory
        self.session_kernels = SessionKernels()
        self.registry_factory = registry_factory
        self.demo = demo
        self.tasks: dict[str, LiveTurn] = {}
        self.reviewing = False

    def sessions(self, query: str = "") -> list[dict]:
        rows = [
            r for r in self.store.list_sessions(self.ws.project_id) if r["status"] != "archived"
        ]
        term = query.strip().casefold()
        if not term:
            return rows

        def matches(sid: str) -> bool:
            for event in self.store.history(sid):
                if isinstance(p := event.parsed(), ev.TurnRequested):
                    question = p.question
                elif event.actor is Actor.USER and isinstance(p, ev.UserMessage):
                    question = p.message.text()
                else:
                    continue
                if term in question.casefold():
                    return True
            return False

        return [row for row in rows if term in row["title"].casefold() or matches(row["id"])]

    def rename(self, sid: str, title: str) -> None:
        events = self.require_active_session(sid)
        title = redact(title.strip())
        if not 1 <= len(title) <= 160:
            raise ValueError("Enter a title between 1 and 160 characters")
        self._append(sid, ev.SessionTitleSet(title=title), Actor.USER, events[-1].id)

    def archive(self, sid: str) -> None:
        events = self.require_session(sid)
        if any(e.type == ev.SessionArchived.event_type for e in events):
            return
        self.ensure_idle()
        if self.pending(events):
            raise ConflictError("Decide the pending plan before archiving")
        self._append(sid, ev.SessionArchived(), Actor.USER, events[-1].id)

    def require_active_session(self, sid: str) -> list[ev.Event]:
        events = self.require_session(sid)
        if any(e.type == ev.SessionArchived.event_type for e in events):
            raise ConflictError("Session is archived")
        return events

    def check_session(self, sid: str) -> None:
        self._check_root(self.store.read(sid, to_seq=0))

    def require_session(self, sid: str) -> list[ev.Event]:
        events = self.store.read(sid)
        self._check_root(events)
        return events

    def _check_root(self, events: list[ev.Event]) -> None:
        if not events:
            raise LookupError("Session not found")
        root = events[0].parsed()
        if (
            not isinstance(root, ev.SessionCreated | ev.SessionForked)
            or root.project_id != self.ws.project_id
        ):
            raise LookupError("Session not found")

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

    def ensure_idle(self) -> None:
        if self.tasks or self.reviewing:
            raise ConflictError("A research operation is already running in this project")

    def fork(self, sid: str, title: str) -> str:
        self.ensure_idle()
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

    def worker_models(self) -> list[dict]:
        if self.demo:
            return []
        options = Router(self.ws.models_config()).worker_options(self.ws.classification)
        return [{"spec": spec, "allowed": allowed} for spec, allowed in options.items()]

    async def draft_plan(self, sid: str, question: str) -> dict:
        from bioquorum.core.enums import ModelRole
        from bioquorum.core.messages import Message
        from bioquorum.review.modeljson import ReviewModelError, parse_json

        self.ensure_idle()
        events = self.require_active_session(sid)
        if self.demo or self.pending(events):
            raise ConflictError("This session cannot draft another plan right now")
        question = redact(question.strip())
        if not 1 <= len(question) <= 12000:
            raise ValueError("Enter a research question between 1 and 12000 characters")
        self.reviewing = True
        try:
            completion = await self.make_client(self.ws, self.store).complete(
                ModelRole.PLANNER,
                [
                    Message.system(
                        'Propose a concise research plan. Return JSON {"steps":["..."]} with 1 to 12 short steps.'
                    ),
                    Message.user(question),
                ],
                session_id=sid,
                classification=self.ws.classification,
                causation_id=events[-1].id,
            )
            try:
                steps = parse_json(completion.message.text()).get("steps")
            except (ValueError, ReviewModelError):
                raise ValueError("The planner returned an invalid draft") from None
            if (
                not isinstance(steps, list)
                or not 1 <= len(steps) <= 12
                or any(
                    not isinstance(step, str) or not 1 <= len(step.strip()) <= 600 for step in steps
                )
            ):
                raise ValueError("The planner returned an invalid draft")
            draft = ev.PlanDrafted(
                question=question,
                model=str(completion.route.spec),
                steps=[redact(step.strip()) for step in steps],
            )
            self._append(sid, draft, Actor.AGENT, events[-1].id)
            return draft.model_dump()
        finally:
            self.reviewing = False

    def submit(
        self,
        sid: str,
        question: str,
        *,
        steps: list[str] | None = None,
        worker_model: str | None = None,
        references: list[ev.TurnReference] | None = None,
        attachments: list[str] | None = None,
    ) -> ev.Event:
        self.ensure_idle()
        events = self.require_active_session(sid)
        if self.pending(events):
            raise ConflictError("Decide the pending plan before submitting a question")
        if worker_model is not None:
            if self.demo:
                raise ValueError("The offline example uses its recorded models")
            Router(self.ws.models_config()).select_worker(worker_model, self.ws.classification)
        if attachments:
            available = {
                e.payload["file_id"]
                for e in self.store.history(sid)
                if e.type == ev.FileRegistered.event_type
            }
            if self.demo or len(attachments) > 8 or set(attachments) - available:
                raise ValueError("Choose up to eight registered files from this session")
        if references:
            from bioquorum.agent.context import reference_text
            from bioquorum.application.first_pass import valid_followup

            history = self.store.history(sid)
            options = self.ws.agent_options()
            if len(references) > 8 or not (
                options.references
                or (
                    options.first_pass and all(valid_followup(history, item) for item in references)
                )
            ):
                raise ValueError("Enable references and select at most eight items")
            from bioquorum.application.artifacts import resolve_selection

            references = [resolve_selection(self, sid, item) for item in references]
            for item in references:
                reference_text(history, item, load_event=self.store.get)
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
            sid,
            ev.TurnRequested(
                question=question,
                plan_id=plan_id,
                worker_model=worker_model,
                references=references or [],
                attachments=list(dict.fromkeys(attachments or [])),
            ),
            Actor.USER,
            events[-1].id,
        )
        history = self.store.history(sid) if references else events
        answered = {e.payload["request_id"] for e in history if e.type == "clarification_answered"}
        for item in references or ():
            if (
                item.kind == "activity"
                and item.id not in answered
                and any(e.id == item.id and e.type == "clarification_requested" for e in history)
            ):
                self._append(
                    sid,
                    ev.ClarificationAnswered(request_id=item.id, turn_id=turn.id),
                    Actor.USER,
                    turn.id,
                )
                answered.add(item.id)
        if plan_id and steps:
            self._append(sid, ev.PlanProposed(plan_id=plan_id, steps=steps), Actor.USER, turn.id)
        else:
            self._start(sid, turn.id, question, None, worker_model)
        return turn

    async def decide_plan(self, sid: str, turn_id: str, *, approve: bool, reason: str = "") -> None:
        self.ensure_idle()
        pending = self.pending(self.require_session(sid))
        if pending is None or pending[0].id != turn_id:
            raise ConflictError("That plan is no longer pending")
        turn, plan = pending
        payload = turn.parsed()
        assert isinstance(payload, ev.TurnRequested)
        if approve:
            self._append(sid, ev.PlanApproved(plan_id=plan.plan_id), Actor.USER, turn_id)
            self._start(sid, turn_id, payload.question, plan, payload.worker_model)
        else:
            self._append(
                sid, ev.PlanAbandoned(plan_id=plan.plan_id, reason=reason), Actor.USER, turn_id
            )
            self._finish(sid, turn_id, "declined")

    def resume(self, sid: str, turn_id: str, checked_calls: list[str] | None = None) -> None:
        self.ensure_idle()
        point = recovery.checkpoint(self.require_active_session(sid))
        if point is None or point.request.id != turn_id:
            raise ConflictError("Only the latest interrupted research turn can be resumed")
        if set(checked_calls or []) != point.uncertain.keys():
            raise ConflictError("Verify the outcome of the interrupted tools before continuing")
        request = point.request.parsed()
        assert isinstance(request, ev.TurnRequested)
        self._append(
            sid,
            ev.TurnResumed(turn_id=turn_id, checked_calls=sorted(point.uncertain)),
            Actor.USER,
            turn_id,
        )
        task = asyncio.create_task(
            self._run(sid, turn_id, request.question, point.plan, request.worker_model, point)
        )
        self.tasks[sid] = LiveTurn(task, turn_id)

    def _start(
        self,
        sid: str,
        turn_id: str,
        question: str,
        plan: ev.PlanProposed | None,
        worker_model: str | None,
    ) -> None:
        self._append(sid, ev.TurnStarted(turn_id=turn_id), Actor.SYSTEM, turn_id)
        task = asyncio.create_task(self._run(sid, turn_id, question, plan, worker_model))
        self.tasks[sid] = LiveTurn(task, turn_id)

    async def _run(
        self,
        sid: str,
        turn_id: str,
        question: str,
        plan: ev.PlanProposed | None,
        worker_model: str | None,
        point: recovery.Checkpoint | None = None,
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
            if worker_model is not None:
                client = client.with_worker(worker_model, self.ws.classification)
            answer_id = point.answer_id if point else None
            if answer_id is None:
                turn = await research.execute(
                    self.ws,
                    self.store,
                    sid,
                    question,
                    client=client,
                    plan=plan,
                    resume_events=point.events if point else None,
                    hooks=Hooks(
                        on_model_delta=on_delta if self.ws.agent_options().stream else None
                    ),
                    permissions=lambda servers: wsp.default_permissions(
                        self.ws,
                        allow=[],
                        servers=servers,
                        ask=ask,
                    ),
                    kernel_factory=self.kernel_factory,
                    session_kernels=self.session_kernels,
                    registry_factory=self.registry_factory,
                )
                answer_id = turn.answer_event_id
            if not point or not point.reviewed:
                try:
                    reviewed = await research.audit(
                        self.ws, self.store, sid, client, answer_event_id=answer_id
                    )
                    self._check_failures(sid, answer_id, reviewed.errors)
                except Exception as exc:
                    self._check_failures(sid, answer_id, [str(exc)])
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
        self.ensure_idle()
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

        await self.session_kernels.close()

    async def recover(self) -> None:
        """Close incomplete turns left by an earlier process, without re-executing anything."""
        for row in self.sessions():
            sid = row["id"]
            events = self.require_session(sid)
            pending = self.pending(events)
            ended = set()
            for item in events:
                payload = item.parsed()
                if isinstance(payload, ev.TurnFinished):
                    ended.add(payload.turn_id)
                elif isinstance(payload, ev.TurnResumed):
                    ended.discard(payload.turn_id)
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
                        (
                            ev.SubtaskFinished
                            if isinstance(payload, ev.SubtaskStarted)
                            else ev.LiteratureFinished
                        )(
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
        project_history = self.store.read(
            None,
            types=(
                "recompute_started",
                "recompute_finished",
                "tool_call_finished",
                "tool_call_failed",
            ),
        )
        terminal = {
            e.payload["recompute_id"] for e in project_history if e.type == "recompute_finished"
        }
        closed_calls = {
            e.payload["call_id"]
            for e in project_history
            if e.type in ("tool_call_finished", "tool_call_failed")
        }
        for event in project_history:
            if event.type != ev.RecomputeStarted.event_type:
                continue
            start = ev.RecomputeStarted.model_validate(event.payload)
            if start.project_id != self.ws.project_id or start.recompute_id in terminal:
                continue
            history = self.require_session(start.session_id)
            await self._recover_calls(start.session_id, history)
            note = "The previous process exited before completing the recalculation."
            endings: list[ev.EventPayload] = [
                ev.RecomputeFinished(
                    project_id=start.project_id,
                    program_id=start.program_id,
                    material_version_id=start.material_version_id,
                    recompute_id=start.recompute_id,
                    status="failed",
                    reason=note,
                )
            ]
            requested = self.store.get(history[0].causation_id) if history[0].causation_id else None
            call = requested.parsed() if requested else None
            if (
                isinstance(call, ev.ToolCallRequested)
                and call.tool == "recompute_material"
                and call.call_id not in closed_calls
            ):
                endings.append(ev.ToolCallFailed(call_id=call.call_id, tool=call.tool, error=note))
            self.store.append_chain(
                endings, session_id=None, actor=Actor.SYSTEM, causation_id=event.id
            )
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
