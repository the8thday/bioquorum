"""Read public search envelopes without interpreting a malformed response as zero hits."""


def _records(value: object) -> list[dict]:
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("Invalid search result list")
    return value


def trial_studies(payload: dict) -> list[dict]:
    return _records(payload.get("studies"))


def gwas_associations(payload: dict) -> list[dict]:
    embedded = payload.get("_embedded")
    if embedded is None and payload.get("page", {}).get("totalElements") == 0:
        return []
    if not isinstance(embedded, dict):
        raise ValueError("Invalid GWAS search envelope")
    return _records(embedded.get("associations"))
