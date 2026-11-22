from bioquorum.application.scientific_viewers import bed_features, mol_records


def test_bed_coordinates_and_molecule_record_numbers(tmp_path):
    path = tmp_path / "a.bed"
    path.write_text("track name=test\nchr2\t0\t5\tabc\nchr2\t8\t10\n")
    assert bed_features(path)[1] == {"chr": "chr2", "start": 8, "end": 10}
    path.write_text("entry1\n  1\nM  END\n$$$$\n\n  Mol\nM  END\n$$$$\n")
    assert mol_records(path)[1].startswith("\n  Mol")
