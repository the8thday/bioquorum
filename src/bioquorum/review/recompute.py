"""Recognize pure measurement statements that a corrected calculation can settle."""

import re

from bioquorum.core.refs import NamedValue


def measurement_statement(text: str, values: list[NamedValue]) -> bool:
    """Every clause must only name a bound measurement, its recorded unit and value."""
    clauses = re.split(r"\s*;\s*", text.strip().removesuffix("."))
    if len(values) != len(clauses):
        return False
    for clause, value in zip(clauses, values, strict=True):
        label = r"(?:_|\s+)".join(re.escape(part) for part in value.name.split("_"))
        marker = r"\{\{" + re.escape(value.name) + r"\}\}"
        unit = r"\s+" + re.escape(value.unit) if value.unit else ""
        if not re.fullmatch(rf"(?i:{label})(?:\s+(?:was|is)\s+|\s*[=:]\s*){marker}{unit}", clause):
            return False
    return bool(values)
