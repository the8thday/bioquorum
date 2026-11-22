"""Bounded CSV pages with original record numbers and optional disk-backed sorting."""

import csv
import json
import math
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path


def _number(text: str) -> float | None:
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def read_page(
    path: Path,
    delimiter: str,
    page: int,
    size: int,
    *,
    query: str = "",
    sort: int | None = None,
    descending: bool = False,
    stats: int | None = None,
) -> dict:
    if page < 1 or page > 2**32 or not 1 <= size <= 100 or len(query) > 256:
        raise ValueError("Invalid table query")
    offset = (page - 1) * size
    query = query.casefold()
    rows = []
    numbers = []
    matched = nonempty = numeric = 0
    minimum = maximum = None
    more = False
    with ExitStack() as stack:
        stream = stack.enter_context(path.open(encoding="utf-8-sig", newline=""))
        reader = csv.reader(stream, delimiter=delimiter)
        header = next(reader, [])
        for column in (sort, stats):
            if column is not None and not 0 <= column < len(header):
                raise ValueError("Column is outside the table")
        db = None
        if sort is not None:
            # Empty filename creates a connection-private, automatically removed temp database.
            db = stack.enter_context(closing(sqlite3.connect("")))
            db.execute("CREATE TABLE sorted (row_no INTEGER, number REAL, text TEXT, cells TEXT)")
        for row_no, cells in enumerate(reader, 1):
            if query and not any(query in cell.casefold() for cell in cells):
                continue
            matched += 1
            if stats is not None:
                value = cells[stats] if stats < len(cells) else ""
                nonempty += bool(value)
                num = _number(value)
                if num is not None:
                    numeric += 1
                    minimum = num if minimum is None else min(minimum, num)
                    maximum = num if maximum is None else max(maximum, num)
            if db is not None and sort is not None:
                key = cells[sort] if sort < len(cells) else ""
                db.execute(
                    "INSERT INTO sorted VALUES (?,?,?,?)",
                    (row_no, _number(key), key.casefold(), json.dumps(cells)),
                )
            elif offset < matched <= offset + size:
                numbers.append(row_no)
                rows.append(cells)
            elif matched > offset + size:
                more = True
                if stats is None:
                    break
        if db is not None:
            direction = "DESC" if descending else "ASC"
            order = f"number IS NULL, number {direction}, CASE WHEN number IS NULL THEN text END {direction}, row_no"
            for row_no, raw in db.execute(
                f"SELECT row_no, cells FROM sorted ORDER BY {order} LIMIT ? OFFSET ?",
                (size, offset),
            ):
                numbers.append(row_no)
                rows.append(json.loads(raw))
            more = matched > offset + size
    statistics = None
    if stats is not None:
        statistics = dict(
            count=matched, nonempty=nonempty, numeric=numeric, min=minimum, max=maximum
        )
    return dict(
        header=header, rows=rows, page=page, more=more, row_numbers=numbers, statistics=statistics
    )
