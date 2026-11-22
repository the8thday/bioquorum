import pytest

from bioquorum.application.scientific_viewers import fasta_records, point_map, sequence_tracks
from bioquorum.core.selections import StructureSelection


def test_sequence_ranges_and_duplicate_record_names(tmp_path):
    path = tmp_path / "s.faa"
    path.write_text(">same\nACD\nE\n>same\nMN\n")
    assert fasta_records(path) == [
        {"record": 1, "name": "same", "sequence": "ACDE"},
        {"record": 2, "name": "same", "sequence": "MN"},
    ]
    path.write_text("record\tstart\tend\tlabel\n1\t2\t4\tdomain\n")
    assert sequence_tracks(path, [4, 2])[0]["label"] == "domain"
    with pytest.raises(ValueError):
        sequence_tracks(path, [2])


def test_point_map_rejects_nonfinite_coordinates_and_duplicate_ids(tmp_path):
    path = tmp_path / "points.tsv"
    for rows in ["one\tnan\t0.1\n", "one\t0.1\t0\none\t1\t1\n"]:
        path.write_text("sample_id\tx\ty\n" + rows)
        with pytest.raises(ValueError):
            point_map(path)


def test_structure_keeps_author_label_and_insertion_code():
    selected = StructureSelection(
        type="structure",
        model=7,
        chain="A",
        label_chain="LA",
        residue=12345,
        insertion="A",
        label_residue=50,
    )
    assert selected.model_dump()["insertion"] == "A"
