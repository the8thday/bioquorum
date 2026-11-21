"""Select a bounded page of research-line background without granting cross-project access."""

from bioquorum.application import workspace
from bioquorum.storage import programs
from bioquorum.storage.events import EventStore
from bioquorum.tools.context import ToolContext
from bioquorum.tools.program_context import ReadContext, context_tool
from bioquorum.tools.spec import ToolSpec


def for_session(ws: workspace.Workspace, store: EventStore, sid: str) -> ToolSpec | None:
    pid = programs.for_session(store.db, ws.project_id, sid)
    if pid is None:
        return None

    def read(ctx: ToolContext, request: ReadContext) -> dict:
        from bioquorum.application.controls import ResearchControls
        from bioquorum.application.program_record import handoff

        if ctx.store is not store or ctx.project_id != ws.project_id or ctx.session_id != sid:
            raise ValueError("Research line context is scoped to the attached session")
        record = handoff(ResearchControls(ws, store=store), pid)
        rows = record[request.section]
        fields = (
            "id",
            "session_id",
            "source_event_id",
            "event_id",
            "text",
            "status",
            "refs",
            "revision",
            "needs_review",
        )
        items = []
        for row in rows[request.offset : request.offset + 10]:
            item = {key: row[key] for key in fields if key in row}
            if request.section == "decisions":
                item["claim_versions"] = [
                    {key: claim[key] for key in ("id", "session_id", "revision") if key in claim}
                    for claim in row.get("claims", [])
                ]
            items.append(item)
        end = request.offset + len(items)
        return {
            "program_id": pid,
            "question": record["question"],
            "items": items,
            "next_offset": end if end < len(rows) else None,
        }

    return context_tool(read)
