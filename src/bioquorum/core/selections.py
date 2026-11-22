"""Immutable coordinates inside a recorded artifact version."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class TableSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["table"]
    row: int = Field(ge=1, le=2**32)
    column: int | None = Field(default=None, ge=0, le=65535)


class SequenceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["sequence"]
    record: int = Field(ge=1)
    start: int = Field(ge=1)
    end: int = Field(ge=1)


class StructureSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["structure"]
    model: int
    chain: str = Field(max_length=128)
    label_chain: str = Field(min_length=1, max_length=128)
    residue: int | None = None
    label_residue: int | None = None
    insertion: str = Field(default="", max_length=8)


class FigureSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["figure"]
    points: tuple[int, ...] = Field(min_length=1, max_length=64)


class ReportSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["report"]
    start: int = Field(ge=1)
    end: int = Field(ge=1)


class RecordSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["molecule", "genome"]
    record: int = Field(ge=1)


ArtifactSelection = Annotated[
    TableSelection
    | SequenceSelection
    | FigureSelection
    | StructureSelection
    | RecordSelection
    | ReportSelection,
    Field(discriminator="type"),
]
