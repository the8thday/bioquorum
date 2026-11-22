import pytest

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor
from bioquorum.storage.rebuild import snapshot


@pytest.mark.parametrize(
    ("actor", "stream"),
    [(Actor.AGENT, None), (Actor.USER, "session")],
)
def test_non_project_or_non_researcher_write_rolls_back(store, actor, stream):
    store.append(
        ev.ProjectCreated(project_id="project", name="Project", root_dir="."),
        actor=Actor.USER,
        session_id=None,
    )
    before, count = snapshot(store.db), store.count()
    with pytest.raises(ValueError, match="project-level researcher"):
        store.append(
            ev.ProgramCreated(project_id="project", program_id="program", name="Program"),
            actor=actor,
            session_id=stream,
        )
    assert snapshot(store.db) == before
    assert store.count() == count
