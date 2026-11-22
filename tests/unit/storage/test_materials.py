import pytest

from bioquorum.core import events as ev
from bioquorum.core.enums import Actor


@pytest.mark.parametrize("actor,sid", [(Actor.AGENT, None), (Actor.USER, "other-session")])
def test_material_events_require_researcher_and_project_stream(store, actor, sid):
    with pytest.raises(ValueError, match="Only a researcher"):
        store.append(
            ev.MaterialWithdrawn(project_id="P", program_id="G", version_id="V", reason="Recall"),
            session_id=sid,
            actor=actor,
        )
    assert store.count() == 0
