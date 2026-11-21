import json

import pytest

from bioquorum.application import artifacts
from bioquorum.application.controls import ResearchControls
from bioquorum.application.workspace import init_project
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification, OutputStream
from bioquorum.core.hashing import sha256_bytes
from bioquorum.core.messages import DataRef


@pytest.mark.anyio
async def test_csv_upload_is_scoped_versioned_and_paged(tmp_path):
    c = ResearchControls(init_project(tmp_path, classification=Classification.PUBLIC))
    sid = c.new_session("Uploads")
    raw = b"sample,count\na,3\nb,4\n"

    async def stream():
        yield raw

    await artifacts.upload(c, sid, "counts.csv", Classification.PUBLIC, stream())
    row = await artifacts.upload(c, sid, "counts.csv", Classification.PUBLIC, stream())
    assert row["version"] == 2 and row["hash"] == sha256_bytes(raw)
    assert artifacts.table(c, sid, row["id"], 2, 1)["rows"] == [["b", "4"]]
    assert (tmp_path / row["path"]).read_bytes() == raw
    assert artifacts.notebook(c, sid)["nbformat"] == 4
    with pytest.raises(LookupError):
        artifacts.download(c, c.new_session("Another session"), row["id"])
    with pytest.raises(ValueError):
        await artifacts.upload(c, sid, "counts.csv", Classification.CONFIDENTIAL, stream())
    assert all("sample,count" not in json.dumps(e.payload) for e in c.store.iter_all())


def test_catalog_records_turn_number_for_turn_products():
    payloads: list[ev.EventPayload] = [
        ev.FileRegistered(
            file_id="f1",
            project_id="p1",
            filename="before.csv",
            path="uploads/before.csv",
            ref=DataRef(uri="cas://a", content_hash="a" * 64, size=12),
            classification=Classification.PUBLIC,
        ),
        ev.TurnStarted(turn_id="t1"),
        ev.TurnFinished(turn_id="t1", outcome="completed"),
        ev.TurnStarted(turn_id="t2"),
        ev.RunStarted(
            run_id="r2", kernel_id="k1", language="python", code="1+1", code_hash="c" * 64
        ),
        ev.RunOutput(
            run_id="r2",
            stream=OutputStream.DISPLAY,
            ref=DataRef(uri="cas://b", content_hash="b" * 64, size=20),
        ),
        ev.TurnFinished(turn_id="t2", outcome="completed"),
    ]
    parsed = [
        (ev.Event.build(p, session_id="s1", seq=i, actor=Actor.SYSTEM), p)
        for i, p in enumerate(payloads, 1)
    ]
    rows = artifacts.catalog_records(parsed)
    assert rows[0]["turn"] is None and rows[0]["turn_number"] is None
    assert rows[1]["turn"] == "t2" and rows[1]["turn_number"] == 2


def test_notebooks_keep_r_cells_out_of_python(tmp_path):
    c = ResearchControls(init_project(tmp_path, classification=Classification.PUBLIC))
    sid = c.new_session("Mixed")
    for lang in ("python", "r"):
        c.store.append(
            ev.RunStarted(
                run_id=lang, language=lang, kernel_id=lang, code=lang, code_hash="f" * 64
            ),
            session_id=sid,
            actor=Actor.TOOL,
        )
    for lang in ("python", "r"):
        notebook = artifacts.notebook(c, sid, lang)
        assert notebook["metadata"]["kernelspec"]["language"].lower() == lang
        assert [cell["source"] for cell in notebook["cells"]] == [[lang]]
