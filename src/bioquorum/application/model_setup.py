"""Workbench model choices and process-local credential diagnostics."""

import os
import re

from bioquorum.core.enums import Classification
from bioquorum.models.config import ModelsConfig, ProviderConfig
from bioquorum.models.routing import Router

OPENAI_MODEL = "openai:gpt-6.2-sol"


def provider(config: ModelsConfig, name: str) -> ProviderConfig:
    return config.providers.get(name) or ProviderConfig.from_dict(name, {})


def starter(config: ModelsConfig, level: Classification, spec: str) -> bool:
    name, _, tag = spec.partition(":")
    if spec != OPENAI_MODEL and not (
        name == "ollama" and re.fullmatch(r"[\w./-]+(?::[\w.-]+)?", tag) and len(tag) <= 160
    ):
        return False
    p = provider(config, name)
    return p.kind == name and level in p.allowed_classifications


def choices(config: ModelsConfig, level: Classification) -> dict[str, bool]:
    options = Router(config).worker_options(level)
    if starter(config, level, OPENAI_MODEL):
        options.setdefault(OPENAI_MODEL, True)
    return options


def credential(p: ProviderConfig) -> dict[str, str]:
    if p.kind == "chatgpt":
        from bioquorum.models.chatgpt_plan import status

        return {
            "name": "ChatGPT plan",
            "state": "connected" if status()["connected"] else "sign_in",
        }
    ref = p.api_key
    if ref == "none":
        return {"name": "", "state": "not_required"}
    if not re.fullmatch(r"env:[A-Za-z_][A-Za-z_0-9]*", ref):
        return {"name": "", "state": "unsupported"}
    name = ref[4:]
    return {"name": name, "state": "set" if os.environ.get(name) else "missing"}


def connection_patch(config: ModelsConfig, level: Classification, entry: dict) -> dict:
    """Convert a researcher selection into ordinary provider, model and role settings."""
    from urllib.parse import urlsplit

    from bioquorum.core.enums import ModelRole
    from bioquorum.models.config import KNOWN_KINDS
    from bioquorum.policy.secrets import redact

    kind = entry.get("kind", "")
    name = entry.get("name", kind)
    model = entry.get("model", "")
    if (
        any(not isinstance(value, str) for value in entry.values())
        or entry.keys() - {"kind", "name", "model", "role", "base_url", "api"}
        or kind not in KNOWN_KINDS
        or not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", name)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]{0,199}", model)
        or redact(model) != model
        or redact(name) != name
    ):
        raise ValueError("Enter a valid connection and model ID")
    config_data = {"kind": kind}
    existing = config.providers.get(name)
    if kind == "openai-compatible" and (not existing or "base_url" in entry):
        if name in KNOWN_KINDS:
            raise ValueError("Choose a separate name for this endpoint")
        endpoint = entry.get("base_url", "").rstrip("/")
        url = urlsplit(endpoint)
        if (
            not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or redact(endpoint) != endpoint
            or len(endpoint) > 500
            or not (
                url.scheme == "https"
                or (url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1"))
            )
        ):
            raise ValueError(
                "Enter an HTTPS endpoint or a loopback HTTP endpoint without credentials"
            )
        api = entry.get("api", "chat")
        if api not in ("responses", "chat"):
            raise ValueError("Select a supported API")
        config_data.update(
            base_url=endpoint, api=api, api_key="env:BQ_" + name.upper() + "_API_KEY"
        )
        if existing and (existing.base_url != endpoint or existing.api != api):
            raise ValueError("Use a different connection name for a different endpoint")
    elif "base_url" in entry or "api" in entry or (name != kind and not existing):
        raise ValueError("Select an existing provider or a separate compatible endpoint")
    provider_config = existing or ProviderConfig.from_dict(name, config_data)
    if provider_config.kind != kind or level not in provider_config.allowed_classifications:
        raise ValueError("This provider is unavailable for the project classification")
    roles = (
        list(ModelRole) if entry.get("role") == "all" else [ModelRole(entry.get("role", "worker"))]
    )
    spec = f"{name}:{model}"
    patch: dict = {"roles": {r.value: spec for r in roles}, "models": {spec: {}}}
    if not existing:
        patch["providers"] = {name: config_data}
    return patch
