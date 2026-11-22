"""Map heavy-atom contacts in the pinned 1N8Z model to its deposited FASTA records."""

import csv
import itertools
import math
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

CUTOFF = 4.0
RESIDUES = dict(
    zip(
        [
            "ALA",
            "CYS",
            "ASP",
            "GLU",
            "PHE",
            "GLY",
            "HIS",
            "ILE",
            "LYS",
            "LEU",
            "MET",
            "ASN",
            "PRO",
            "GLN",
            "ARG",
            "SER",
            "THR",
            "VAL",
            "TRP",
            "TYR",
        ],
        "ACDEFGHIKLMNPQRSTVWY",
        strict=True,
    )
)


class Site(NamedTuple):
    chain: str
    position: int
    author: str
    residue: str


def atoms(cif, sequences):
    # This deposited loop uses one whitespace-delimited row per atom. Only this
    # checksummed entry is supported; do not use this as a general mmCIF parser.
    fields = []
    for line in cif.read_text().splitlines():
        if line.startswith("_atom_site."):
            fields.append(line.strip().removeprefix("_atom_site."))
        elif fields:
            if line.startswith("#"):
                break
            row = dict(zip(fields, line.split(), strict=True))
            chain = row["label_asym_id"]
            if (
                row["group_PDB"] != "ATOM"
                or chain not in sequences
                or row["pdbx_PDB_model_num"] != "1"
                or row["type_symbol"] in {"H", "D"}
                or row["label_alt_id"] not in {".", "A"}
                or float(row["occupancy"]) <= 0
            ):
                continue
            pos = int(row["label_seq_id"])
            residue = row["label_comp_id"]
            if sequences[chain][1][pos - 1] != RESIDUES[residue] or row["auth_asym_id"] != chain:
                raise ValueError(f"Chain or sequence mismatch at {chain} {pos}")
            ins = row["pdbx_PDB_ins_code"]
            site = Site(
                chain, pos, row["auth_seq_id"] + ("" if ins in {".", "?"} else ins), residue
            )
            yield site, tuple(float(row["Cartn_" + axis]) for axis in "xyz")


def analyze(cif: Path, fasta: Path, output: Path) -> dict[str, int]:
    sequences = {}
    for number, record in enumerate(fasta.read_text().strip().split(">")[1:], 1):
        header, *lines = record.splitlines()
        chain = header.split("|")[1].removeprefix("Chain ")
        sequences[chain] = (number, "".join(lines))
    if set(sequences) != {"A", "B", "C"}:
        raise ValueError("Expected the deposited Fab light, Fab heavy and HER2 chains")

    # Only atoms in adjacent spatial bins can lie within the cutoff.
    def cell(xyz):
        return tuple(math.floor(x / CUTOFF) for x in xyz)

    antigen = defaultdict(list)
    fab_atoms = []
    for site, xyz in atoms(cif, sequences):
        if site.chain == "C":
            antigen[cell(xyz)].append((site, xyz))
        else:
            fab_atoms.append((site, xyz))
    minima = {}
    for fab, xyz in fab_atoms:
        x, y, z = cell(xyz)
        for dx, dy, dz in itertools.product((-1, 0, 1), repeat=3):
            for her2, other in antigen.get((x + dx, y + dy, z + dz), ()):
                distance = math.dist(xyz, other)
                if distance <= CUTOFF:
                    pair = (fab, her2)
                    minima[pair] = min(distance, minima.get(pair, math.inf))
    fab_sites = {fab for fab, _ in minima}
    her2_sites = {her2 for _, her2 in minima}
    with (output / "contacts.tsv").open("w", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "fab_chain",
                "fab_position",
                "fab_author",
                "fab_residue",
                "her2_chain",
                "her2_position",
                "her2_author",
                "her2_residue",
                "distance_A",
            ]
        )
        for (fab, her2), distance in sorted(minima.items()):
            writer.writerow([*fab, *her2, f"{distance:.4f}"])
    (output / "interface.fasta").write_bytes(fasta.read_bytes())
    with (output / "interface.fasta.annotations.tsv").open("w", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(["record", "start", "end", "label"])
        for site in sorted(fab_sites | her2_sites):
            record = sequences[site.chain][0]
            label = f"Interface · {site.chain} {site.residue} {site.author} (author)"
            writer.writerow([record, site.position, site.position, label])
    return dict(fab_sites=len(fab_sites), her2_sites=len(her2_sites), residue_pairs=len(minima))
