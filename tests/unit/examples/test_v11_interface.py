import csv
import runpy
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[3] / "examples/v11"


def test_contact_pairs_and_fasta_mapping(tmp_path):
    import gzip

    coordinates = tmp_path / "structure.cif"
    coordinates.write_bytes(gzip.decompress((EXAMPLE / "data/1N8Z.cif.gz").read_bytes()))
    analyze = runpy.run_path(str(EXAMPLE / "interface.py"))["analyze"]
    counts = analyze(coordinates, EXAMPLE / "data/1N8Z.fasta", tmp_path)
    # Independent exhaustive comparison of the legacy PDB coordinates.
    assert counts == {"fab_sites": 14, "her2_sites": 17, "residue_pairs": 32}
    with (tmp_path / "contacts.tsv").open() as stream:
        pairs = list(csv.DictReader(stream, delimiter="\t"))
    assert len(pairs) == 32
    assert all(0 < float(row["distance_A"]) <= 4 for row in pairs)
    with (tmp_path / "interface.fasta.annotations.tsv").open() as stream:
        tracks = list(csv.DictReader(stream, delimiter="\t"))
    assert len(tracks) == 31
    assert [t["record"] for t in tracks].count("2") == 17  # FASTA order A, C, B
    assert {row["her2_author"] for row in pairs} == set(
        [
            "557",
            "558",
            "560",
            "561",
            "569",
            "570",
            "571",
            "572",
            "573",
            "591",
            "593",
            "596",
            "598",
            "600",
            "602",
            "603",
            "604",
        ]
    )
