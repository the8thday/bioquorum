import json
from pathlib import Path

from bioquorum.core.bundle import SPEC_VERSION, json_schema

SCHEMA_FILE = (
    Path(__file__).resolve().parents[3] / "bundle-spec" / f"manifest-v{SPEC_VERSION}.schema.json"
)


def test_schema_file_matches_model():
    """bundle-spec/ is generated from core.bundle.Manifest; regenerate with scripts/gen_bundle_schema.py."""
    assert SCHEMA_FILE.exists(), f"missing {SCHEMA_FILE}"
    on_disk = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert on_disk == json_schema()
