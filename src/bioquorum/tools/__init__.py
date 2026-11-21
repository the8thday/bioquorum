"""Tool layer: the registry and the invoke() chokepoint (ARCHITECTURE.md §6.4, ADR-005/006/007)."""

from bioquorum.tools.builtin import BUILTIN_TOOLS
from bioquorum.tools.context import ToolContext
from bioquorum.tools.invoke import invoke, invoke_by_name
from bioquorum.tools.registry import ToolRegistry
from bioquorum.tools.spec import ToolResult, ToolSpec


def default_registry(*, r_available: bool = True) -> ToolRegistry:
    return ToolRegistry(tool for tool in BUILTIN_TOOLS if r_available or tool.name != "r_execute")


__all__ = [
    "BUILTIN_TOOLS",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "default_registry",
    "invoke",
    "invoke_by_name",
]
