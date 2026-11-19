"""Regenerate both supported manifest schemas from their Pydantic models (ADR-010)."""

import json
from pathlib import Path

from bioquorum.core.bundle import json_schema
from bioquorum.core.bundle_v1 import ManifestV1

directory = Path(__file__).resolve().parents[1] / "bundle-spec"
for version, schema in (("0", json_schema()), ("1", ManifestV1.model_json_schema())):
    path = directory / f"manifest-v{version}.schema.json"
    path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path}")
