from pathlib import Path

import pytest

from bioquorum.skills.loader import discover


def skill(root: Path, name: str, *, body: str = "Load only when requested.") -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    file = directory / "SKILL.md"
    file.write_text(
        f"---\nname: {name}\ndescription: Guide for instrument calibration.\n---\n{body}"
    )
    return file


def test_discover_metadata_and_load_when_requested(tmp_path):
    file = skill(tmp_path, "calibration")
    guide = discover(tmp_path)["calibration"]
    assert guide.description == "Guide for instrument calibration."
    assert "Load only" not in repr(guide)
    assert guide.load().text == "Load only when requested."
    refs = file.parent / "references"
    refs.mkdir()
    (refs / "limits.txt").write_text("The acceptance band is specified in the sample plan.")
    assert "acceptance band" in guide.load("references/limits.txt").text


def test_changed_and_linked_skills_are_not_loaded(tmp_path):
    root = tmp_path / "skills"
    file = skill(root, "calibration")
    (root / "link").symlink_to(file.parent, target_is_directory=True)
    guide = discover(root)["calibration"]
    assert "link" not in discover(root)
    file.write_text(file.read_text() + "\nDifferent instructions.")
    with pytest.raises(ValueError, match="changed"):
        guide.load()
    with pytest.raises(ValueError):
        guide.load("../../calibration/SKILL.md")


def test_links_inside_reference_directory_are_rejected(tmp_path):
    file = skill(tmp_path / "skills", "calibration")
    external = tmp_path / "external.txt"
    external.write_text("Unrelated")
    directory = file.parent / "references"
    directory.mkdir()
    (directory / "alias.txt").symlink_to(external)
    with pytest.raises(ValueError):
        discover(tmp_path / "skills")["calibration"].load("references/alias.txt")
