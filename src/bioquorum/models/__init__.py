"""Model layer: providers, roles, routing, conversion (ARCHITECTURE.md §6.1).

The only package allowed to import pydantic_ai (ADR-003).
"""

from bioquorum.models.cassette import CassetteMissError, CassetteMode, ModelCassette
from bioquorum.models.client import Completion, ModelClient, ModelUnavailableError
from bioquorum.models.config import ConfigError, ModelsConfig, ModelSpec, ProviderConfig
from bioquorum.models.routing import Route, Router

__all__ = [
    "CassetteMissError",
    "CassetteMode",
    "Completion",
    "ConfigError",
    "ModelCassette",
    "ModelClient",
    "ModelSpec",
    "ModelUnavailableError",
    "ModelsConfig",
    "ProviderConfig",
    "Route",
    "Router",
]
