"""Regenerate bundle-spec/manifest-v<N>.schema.json from bioquorum.core.bundle (ADR-010)."""

import json
from pathlib import Path

from bioquorum.core.bundle import SPEC_VERSION, json_schema

out = Path(__file__).resolve().parents[1] / "bundle-spec" / f"manifest-v{SPEC_VERSION}.schema.json"
out.write_text(json.dumps(json_schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"wrote {out}")
