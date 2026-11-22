from bioquorum.application import workspace
from bioquorum.skills.loader import DOMAIN_SKILLS


def test_domain_library_is_independent_and_records_licenses(tmp_path):
    ws = workspace.init_project(tmp_path)
    ws.config["agent"] = {"domain_skills": True}
    assert set(ws.project_skills()) == DOMAIN_SKILLS
    for skill in ws.project_skills().values():
        assert skill.license
        text = skill.load().text
        assert "Tools" in text and "license" in text.lower()
