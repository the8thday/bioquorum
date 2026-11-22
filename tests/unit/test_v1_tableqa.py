from pathlib import Path

import pytest
from evals.v04_public_run import stage
from evals.v04_public_subset import ALLOW
from evals.v1_tableqa import select_tasks, tally

from bioquorum.core.hashing import sha256_bytes


def row(uid: str, paper: str, *tables: str) -> dict:
    return {
        "id": uid,
        "version": "1.0",
        "source": paper,
        "table-path": list(tables),
        "question": "Which option?",
        "ideal": "right",
        "distractors": ["wrong"],
    }


def test_selection_excludes_pilot_papers_and_tables_and_hides_grading_key():
    pilot = next(iter(ALLOW["lab-bench-tableqa-v1"]))
    rows = [
        row(pilot, "pilot", "p.png"),
        row("a", "pilot", "new.png"),
        row("b", "other", "p.png"),
        row("c", "multi", "one.png", "two.png"),
        row("d", "one", "1.png"),
        row("e", "one", "2.png"),
        row("f", "two", "3.png"),
    ]
    tasks, key = select_tasks(rows, 2)
    assert select_tasks(rows[::-1], 2) == (tasks, key)
    assert set(key) in ({"d", "f"}, {"e", "f"})
    assert all(
        set(task) == {"id", "benchmark", "source", "question", "inputs", "choices"}
        for task in tasks
    )
    assert all(task["choices"].count("right") == 1 for task in tasks)
    with pytest.raises(ValueError, match="distinct"):
        select_tasks(rows, 3)


def test_tally_preserves_missing_and_unanswered_in_denominator():
    refs = {"a": "Good", "b": "Yes", "c": "No", "d": "Option"}
    result = tally(refs, {"a": " GOOD ", "b": "no", "c": None})
    assert result["total"] == 4
    assert result["correct"] == 1
    assert result["items"] == {"a": "correct", "b": "wrong", "c": "unanswered", "d": "missing"}
    with pytest.raises(ValueError, match="unknown"):
        tally(refs, {"stray": "Good"})


def test_runner_refuses_a_changed_table_before_a_model_call(tmp_path: Path):

    source = tmp_path / "inputs"
    project = tmp_path / "project"
    source.mkdir()
    task = {"inputs": ["a.png"], "image_sha256": sha256_bytes(b"original")}
    (source / "a.png").write_bytes(b"original")
    assert stage(task, source, project, None) == ["a.png"]
    (source / "a.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="frozen"):
        stage(task, source, project, None)


def test_campaign_rejects_a_modified_manifest_before_creating_a_budget(tmp_path):
    from evals.v1_tableqa_run import load_tasks

    questions = tmp_path / "questions.json"
    questions.write_text('{"tasks":[]}')
    with pytest.raises(ValueError, match="frozen"):
        load_tasks(questions)


def test_campaign_has_one_writer_and_releases_after_failure(tmp_path):
    from evals.v1_tableqa_run import exclusive_campaign

    with (
        pytest.raises(RuntimeError, match="already running"),
        exclusive_campaign(tmp_path),
        exclusive_campaign(tmp_path),
    ):
        pytest.fail("A second campaign acquired the lock")
    with exclusive_campaign(tmp_path):
        pass
