import pytest

from bioquorum.agent.context import reference_text
from bioquorum.application import artifacts
from bioquorum.application.controls import ResearchControls
from bioquorum.application.workspace import init_project
from bioquorum.core import events as ev
from bioquorum.core.enums import Classification


@pytest.mark.anyio
async def test_sorted_filtered_cell_resolves_original_record_and_version(tmp_path):
    controls = ResearchControls(init_project(tmp_path, classification=Classification.PUBLIC))
    sid = controls.new_session("Viewer")

    async def contents():
        yield b'name,count\n"keep, A",10\nlose,0\nkeep B,2\nkeep C,2\n'

    item = await artifacts.upload(controls, sid, "counts.csv", Classification.PUBLIC, contents())
    page = artifacts.table(controls, sid, item["id"], 1, 2, query="keep", sort=1, stats=1)
    assert page["row_numbers"] == [3, 4]
    assert page["more"] and page["rows"][0] == ["keep B", "2"]
    assert page["statistics"] == {"count": 3, "nonempty": 3, "numeric": 3, "min": 2, "max": 10}
    ref = ev.TurnReference.model_validate(
        {
            "kind": "artifact",
            "id": item["id"],
            "selection": {"type": "table", "row": 3, "column": 1},
        }
    )
    resolved = artifacts.resolve_selection(controls, sid, ref)
    assert resolved.excerpt == '["count","2"]'
    text = reference_text(controls.store.history(sid), resolved)
    assert item["hash"] in text and "count" in text and "keep, A" not in text
    with pytest.raises(LookupError):
        artifacts.resolve_selection(controls, controls.new_session("Other"), ref)
    assert ref.selection is not None
    with pytest.raises(ValueError):
        artifacts.resolve_selection(
            controls,
            sid,
            ref.model_copy(update={"selection": ref.selection.model_copy(update={"row": 5})}),
        )


def test_million_rows_sorted_with_stable_ids(tmp_path):
    from bioquorum.application.table_view import read_page

    path = tmp_path / "large.csv"
    with path.open("w") as f:
        f.write("count\n")
        f.writelines(f"{i}\n" for i in range(1_000_000))
    page = read_page(path, ",", 2, 2, sort=0, descending=True, stats=0)
    assert page["rows"] == [["999997"], ["999996"]]
    assert page["row_numbers"] == [999998, 999997]
    assert page["statistics"]["count"] == 1_000_000


@pytest.mark.anyio
async def test_report_quote_uses_original_line_range(tmp_path):
    controls = ResearchControls(init_project(tmp_path, classification=Classification.PUBLIC))
    sid = controls.new_session("Report")

    async def contents():
        yield b"# Heading\r\noriginal result\r\nnext line"

    item = await artifacts.upload(controls, sid, "analysis.md", Classification.PUBLIC, contents())
    ref = ev.TurnReference.model_validate(
        dict(
            kind="artifact",
            id=item["id"],
            excerpt="forged",
            selection=dict(type="report", start=2, end=3),
        )
    )
    assert (
        artifacts.resolve_selection(controls, sid, ref).excerpt == '"original result\\nnext line"'
    )
    with pytest.raises(ValueError):
        artifacts.resolve_selection(
            controls,
            sid,
            ev.TurnReference.model_validate(
                dict(kind="artifact", id=item["id"], selection=dict(type="report", start=2, end=4))
            ),
        )
