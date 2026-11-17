"""The single tool registry (ADR-005). Agent and MCP tool lists are both generated here."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from bioquorum.tools.spec import ToolSpec


class ToolRegistry:
    def __init__(self, specs: Iterable[ToolSpec] = ()) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._specs:
            raise ValueError(f"tool '{spec.name}' registered twice")
        self._specs[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise KeyError(f"unknown tool '{name}'") from None

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __iter__(self) -> Iterator[ToolSpec]:
        return iter(sorted(self._specs.values(), key=lambda s: s.name))

    def __len__(self) -> int:
        return len(self._specs)

    def definitions(self) -> list[dict[str, Any]]:
        """Provider-neutral tool definitions, in stable order (cache-friendly, §6.6).

        Both ``models/`` (agent exit) and ``mcp/server.py`` (MCP exit) build from this list.
        """
        return [
            {"name": s.name, "description": s.description, "input_schema": s.input_schema}
            for s in self
        ]
