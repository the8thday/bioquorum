"""Provider and role configuration (ARCHITECTURE.md §6.1, ADR-004, ADR-013).

Read from ``~/.bioquorum/config.toml`` merged with the project's ``bioquorum.toml``::

    [providers.openai]                      # kind defaults to the provider name if known
    api = "responses"                       # newer OpenAI models need it for tools
    [providers.deepseek]
    [providers.lab]
    kind = "openai-compatible"
    base_url = "http://localhost:8000/v1"   # localhost => allowed for every classification
    family = "qwen"

    [roles]
    worker = "openai:gpt-6.1-sol"
    reviewer = "deepseek:deepseek-v4-pro"   # or "auto": best review_score (ADR-013)

    [models."deepseek:deepseek-v4-pro"]
    review_score = 94.4                     # from docs/research/reviewer-experiment.md

Secrets: ``api_key = "env:NAME"`` (default ``<KIND>_API_KEY``). The value is resolved only
when the client is built and is registered for redaction; it never enters events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from bioquorum.core.enums import Classification, ModelRole

# kind -> (default base_url, default family, default api)
KNOWN_KINDS: dict[str, tuple[str | None, str, str]] = {
    "openai": (None, "openai", "responses"),
    "deepseek": ("https://api.deepseek.com", "deepseek", "chat"),
    "qwen": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen", "chat"),
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter", "chat"),
    "ollama": ("http://localhost:11434/v1", "ollama", "chat"),
    "openai-compatible": (None, "", "chat"),
}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
REMOTE_DEFAULT = (Classification.PUBLIC, Classification.INTERNAL)


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    kind: str
    base_url: str | None
    family: str
    api: str  # "chat" | "responses"
    api_key: str  # reference, e.g. "env:DEEPSEEK_API_KEY"; never the value itself
    allowed_classifications: tuple[Classification, ...]

    @property
    def is_local(self) -> bool:
        return bool(self.base_url) and urlparse(self.base_url).hostname in _LOCAL_HOSTS

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> ProviderConfig:
        kind = data.get("kind", data.get("type", name))
        if kind not in KNOWN_KINDS:
            raise ConfigError(
                f"provider '{name}': unknown kind '{kind}' ({', '.join(KNOWN_KINDS)})"
            )
        default_url, default_family, default_api = KNOWN_KINDS[kind]
        base_url = data.get("base_url", default_url)
        if kind == "openai-compatible" and not base_url:
            raise ConfigError(f"provider '{name}': openai-compatible needs base_url")
        family = data.get("family") or default_family or name
        key_default = (
            "none" if kind == "ollama" else f"env:{kind.upper().replace('-', '_')}_API_KEY"
        )
        api_key = data.get("api_key", key_default)
        if api_key not in ("none",) and not str(api_key).startswith(("env:", "keyring:")):
            raise ConfigError(
                f"provider '{name}': api_key must be a reference like env:NAME, not a literal key"
            )
        probe = cls(name, kind, base_url, family, data.get("api", default_api), api_key, ())
        allowed = data.get("allowed_classifications")
        classes = (
            tuple(Classification(c) for c in allowed)
            if allowed is not None
            else (tuple(Classification) if probe.is_local else REMOTE_DEFAULT)
        )
        return cls(name, kind, base_url, family, probe.api, api_key, classes)


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str

    @classmethod
    def parse(cls, text: str) -> ModelSpec:
        provider, sep, model = text.partition(":")
        if not sep or not provider or not model:
            raise ConfigError(f"model spec must look like provider:model, got {text!r}")
        return cls(provider, model)

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass
class ModelsConfig:
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    roles: dict[ModelRole, list[ModelSpec]] = field(default_factory=dict)
    """Each role maps to a fallback chain, first entry preferred."""
    review_scores: dict[str, float] = field(default_factory=dict)
    prefer_different_family: bool = False
    reviewer_auto: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelsConfig:
        providers = {
            name: ProviderConfig.from_dict(name, p) for name, p in data.get("providers", {}).items()
        }
        roles: dict[ModelRole, list[ModelSpec]] = {}
        reviewer_auto = False
        for role_name, value in data.get("roles", {}).items():
            if role_name == "prefer_different_family":
                continue
            role = ModelRole(role_name)
            if role is ModelRole.REVIEWER and value == "auto":
                reviewer_auto = True
                continue
            chain = [value] if isinstance(value, str) else list(value)
            roles[role] = [ModelSpec.parse(v) for v in chain]
        for chain in roles.values():
            for spec in chain:
                if spec.provider not in providers:
                    raise ConfigError(f"{spec}: provider '{spec.provider}' is not configured")
        scores = {
            k: float(v["review_score"])
            for k, v in data.get("models", {}).items()
            if isinstance(v, dict) and "review_score" in v
        }
        return cls(
            providers=providers,
            roles=roles,
            review_scores=scores,
            prefer_different_family=bool(
                data.get("roles", {}).get("prefer_different_family", False)
            ),
            reviewer_auto=reviewer_auto,
        )
