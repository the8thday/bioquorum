"""Enumerations shared across the domain model (ARCHITECTURE.md §5)."""

from __future__ import annotations

from enum import StrEnum


class Classification(StrEnum):
    """Project data classification. Drives model routing (ARCHITECTURE.md §6.1)."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"


CLASSIFICATION_ORDER: dict[Classification, int] = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
}


class ModelRole(StrEnum):
    WORKER = "worker"
    REVIEWER = "reviewer"
    PLANNER = "planner"
    SUMMARIZER = "summarizer"


class Actor(StrEnum):
    USER = "user"
    AGENT = "agent"
    REVIEWER = "reviewer"
    SYSTEM = "system"
    TOOL = "tool"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Trust(StrEnum):
    """Whether content may be treated as instructions (ADR-007)."""

    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class DataKind(StrEnum):
    FILE = "file"
    DATAFRAME = "dataframe"
    ANNDATA = "anndata"
    FIGURE = "figure"
    OTHER = "other"


class ArtifactKind(StrEnum):
    FIGURE = "figure"
    TABLE = "table"
    REPORT = "report"
    DATASET = "dataset"
    STRUCTURE = "structure"
    NOTEBOOK = "notebook"


class ClaimKind(StrEnum):
    NUMERIC = "numeric"
    CITATION = "citation"
    EXECUTION = "execution"
    CAUSAL = "causal"
    PLAN = "plan"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class FindingStatus(StrEnum):
    OPEN = "open"
    ADDRESSED = "addressed"
    DISMISSED = "dismissed"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class PermissionClass(StrEnum):
    """What a tool needs permission for (ARCHITECTURE.md §6.4)."""

    READ_FS = "read_fs"
    WRITE_FS = "write_fs"
    EXECUTE = "execute"
    NETWORK = "network"
    CONNECTOR = "connector"
    EXTERNAL_MCP = "external_mcp"


class PermissionScope(StrEnum):
    ONCE = "once"
    SESSION = "session"
    PROJECT = "project"
    GLOBAL = "global"


class OutputStream(StrEnum):
    STDOUT = "stdout"
    STDERR = "stderr"
    DISPLAY = "display"
