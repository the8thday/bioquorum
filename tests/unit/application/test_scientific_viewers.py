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


def test_python_figure_points_rejects_booleans_and_nonfinite_coords(tmp_path):
    from bioquorum.runtime.values import PRELUDE

    ns: dict = {}
    exec(PRELUDE, ns)
    bq = ns["bq"]
    for bad_x, bad_y in [([True], [0.5]), ([0.5], [False]), ([float("nan")], [0.5])]:
        with pytest.raises(ValueError):
            bq.figure_points(tmp_path / "plot.png", ["s1"], bad_x, bad_y)


def test_kernel_instructions_document_viewer_sidecar_helpers():
    from bioquorum.agent.prompts import kernel_instructions

    text = "\n".join(
        kernel_instructions(r_available=True, inventory={"packages": [], "r_packages": []})
    )
    assert "bq.figure_points" in text
    assert "bq_figure_points" in text
    assert ".annotations.tsv" in text
