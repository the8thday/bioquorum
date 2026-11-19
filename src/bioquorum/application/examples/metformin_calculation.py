"""Reproduce the offline example's recorded count and plot from the input file."""

from __future__ import annotations

import json
from collections import Counter
from html import escape
from pathlib import Path

LABELS = {
    "Human clinical study": "Clinical study",
    "Primary human hepatocyte experiment": "Hepatocyte experiment",
    "Randomized delayed-release trials": "Randomized trials",
}


def chart(rows: list[dict[str, str]]) -> str:
    counts = Counter(row["setting"] for row in rows)
    height = 68 + 32 * len(counts)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 {height}">',
        f'<rect width="600" height="{height}" fill="#f8f7f0"/>',
        '<text x="28" y="35" font-family="sans-serif" font-size="19" fill="#182f29">'
        "Evidence excerpts by source type</text>",
        '<g font-family="sans-serif" font-size="15" fill="#182f29">',
    ]
    for index, (setting, count) in enumerate(counts.items()):
        y = 72 + index * 32
        width = round(200 * count / max(counts.values()))
        label = escape(LABELS.get(setting, setting))
        parts.extend(
            [
                f'<text x="28" y="{y}">{label}</text>',
                f'<rect x="208" y="{y - 16}" width="{width}" height="18" fill="#32765a"/>',
                f'<text x="{225 + width}" y="{y}">{count}</text>',
            ]
        )
    return "".join(parts) + "</g></svg>"


def summarize(rows: list[dict[str, str]]) -> str:
    return "\n".join(f"PMID {row['pmid']}: {row['setting']}" for row in rows)


if __name__ == "__main__":
    from IPython.display import SVG, display

    rows = json.loads(Path("metformin-evidence.json").read_text())
    print(summarize(rows))
    globals()["bq"].value("excerpts", len(rows), unit="excerpts")
    display(SVG(data=chart(rows)))
