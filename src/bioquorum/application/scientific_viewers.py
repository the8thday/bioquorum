"""Read local scientific viewer data; all coordinates refer to immutable file versions."""

import csv
from pathlib import Path


def fasta_records(path: Path) -> list[dict]:
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Sequence preview supports up to 4 MiB. Download the original.")
    entries = []
    name = None
    parts: list[str] = []

    def finish():
        if not parts:
            raise ValueError("Empty FASTA record")
        entries.append(dict(record=len(entries) + 1, name=name, sequence="".join(parts)))

    with path.open(encoding="utf-8-sig") as stream:
        for raw in stream:
            line = raw.strip()
            if not line or line.startswith(";"):
                continue
            if line.startswith(">"):
                if name is not None:
                    finish()
                name, parts = line[1:].strip(), []
            elif name is None:
                raise ValueError("Expected a FASTA header")
            else:
                parts.append("".join(line.split()))
    if name is None:
        raise ValueError("No FASTA records found")
    finish()
    return entries


def _records(path: Path, fields: list[str]) -> list[dict]:
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Viewer annotation file exceeds 2 MiB")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if reader.fieldnames != fields:
            raise ValueError("Unexpected annotation columns")
        rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise ValueError("Malformed annotation row")
    return rows


def point_map(path: Path) -> list[dict]:
    rows = _records(path, ["sample_id", "x", "y"])
    seen = set()
    points = []
    for row in rows:
        sample = row["sample_id"]
        x, y = float(row["x"]), float(row["y"])
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < len(sample) <= 128) or sample in seen:
            raise ValueError("Invalid or duplicate mapped point")
        points.append(dict(sample_id=sample, x=x, y=y))
        seen.add(sample)
    return points


def sequence_tracks(path: Path, lengths: list[int]) -> list[dict]:
    tracks = []
    for row in _records(path, ["record", "start", "end", "label"]):
        record, start, end = (int(row[key]) for key in ("record", "start", "end"))
        if not 1 <= record <= len(lengths) or not 1 <= start <= end <= lengths[record - 1]:
            raise ValueError("Annotation outside the sequence")
        tracks.append(dict(record=record, start=start, end=end, label=row["label"]))
    return tracks


def mol_records(path: Path) -> list[str]:
    import re

    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Molecule preview supports up to 4 MiB")
    records = re.split(r"^\$\$\$\$[ \t]*\n?", path.read_text(encoding="utf-8-sig"), flags=re.M)
    return [record for record in records if record.strip()]


def bed_features(path: Path) -> list[dict]:
    import re

    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("BED preview supports up to 4 MiB")
    features = []
    with path.open(encoding="utf-8-sig") as stream:
        for line in stream:
            if not line.strip() or line.startswith(("#", "track ", "browser ")):
                continue
            cols = line.rstrip("\r\n").split("\t")
            if len(cols) < 3 or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", cols[0]):
                raise ValueError("Expected a BED chromosome, start and end")
            start, end = int(cols[1]), int(cols[2])
            if not 0 <= start < end <= 2**53 - 1:
                raise ValueError("Invalid BED interval")
            features.append(dict(chr=cols[0], start=start, end=end))
    if not features:
        raise ValueError("No BED intervals")
    return features
