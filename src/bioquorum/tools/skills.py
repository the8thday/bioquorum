"""Open a catalogued skill through the standard permission and audit path."""

from pydantic import BaseModel, Field, JsonValue, create_model

from bioquorum.core.enums import PermissionClass
from bioquorum.policy.secrets import redact
from bioquorum.skills.loader import Skill
from bioquorum.tools.context import ToolContext
from bioquorum.tools.spec import ToolResult, ToolSpec

SKILL_TOOL_NAME = "open_skill"


class OpenSkill(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    resource: str | None = Field(default=None, min_length=1, max_length=200)


def skill_tool(catalog: dict[str, Skill]) -> ToolSpec:
    activated: set[str] = set()

    async def open_skill(args: OpenSkill, ctx: ToolContext) -> ToolResult:
        skill = catalog.get(args.name)
        if skill is None:
            return ToolResult.text(
                "Use an exact name listed in the project skill catalog.", is_error=True
            )
        if args.resource and args.name not in activated:
            return ToolResult.text("Open this skill's SKILL.md first.", is_error=True)
        content = skill.load(args.resource)
        if redact(content.text) != content.text:
            return ToolResult.text(
                "This skill may contain a credential. Remove it before loading.", is_error=True
            )
        if args.resource is None:
            activated.add(args.name)
        return ToolResult.text(
            f"Skill: {args.name}\nResource: {content.resource}\nSHA-256: {content.sha256}\n\n"
            + content.text
        )

    names: list[JsonValue] = [name for name in sorted(catalog)]
    argument_model = create_model(
        "CataloguedSkill",
        __base__=OpenSkill,
        name=(str, Field(min_length=1, max_length=64, json_schema_extra={"enum": names})),
    )
    return ToolSpec(
        SKILL_TOOL_NAME,
        "Read a project skill by exact catalog name. Omit resource to open SKILL.md; "
        "then request only linked references/ or assets/ text files needed for the task.",
        open_skill,
        PermissionClass.READ_FS,
        args_model=argument_model,
        target=lambda *_: "project-skills",
    )
