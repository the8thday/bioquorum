from bioquorum.application import workspace


def test_builtin_evidence_skills_are_opt_in_and_replaceable(tmp_path):
    ws = workspace.init_project(tmp_path)
    assert not ws.project_skills()
    ws.config["agent"] = {"evidence_skills": True}
    catalog = ws.project_skills()
    assert set(catalog) == {"target-evidence", "omics-evidence", "evidence-synthesis"}
    assert "donor" in catalog["omics-evidence"].load().text
    ws.config["extensions"] = {"skills": {"omics-evidence": {"enabled": False}}}
    assert "omics-evidence" not in ws.project_skills()
    assert "omics-evidence" in ws.project_skills(include_disabled=True)
