"""Compare the published rounded response rates in POETYK PSO-1."""

import csv
from decimal import Decimal

from IPython.display import SVG, display

with open("pso1.csv") as stream:
    rates = {row["arm"]: Decimal(row["pasi75_percent"]) for row in csv.DictReader(stream)}
gap = rates["deucravacitinib"] - rates["placebo"]
bq.value("pasi_gap", float(gap), unit="percentage points")  # noqa: F821
bars = []
for i, (arm, percent) in enumerate(rates.items()):
    y = 30 + i * 40
    bars.append(
        f'<text x="8" y="{y + 13}">{arm}</text><rect x="154" y="{y}" width="{percent * 3}" height="18" fill="#276f66"/><text x="{160 + percent * 3}" y="{y + 13}">{percent}%</text>'
    )
display(
    SVG(
        '<svg xmlns="http://www.w3.org/2000/svg" width="430" height="160"><title>Week 16 PASI 75 response</title><text x="8" y="17">Week 16 · PASI 75 response</text>'
        + "".join(bars)
        + "</svg>"
    )
)
