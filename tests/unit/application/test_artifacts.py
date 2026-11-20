import json

import pytest

from bioquorum.application import artifacts
from bioquorum.application.controls import ResearchControls
from bioquorum.application.workspace import init_project
from bioquorum.core.enums import Classification
from bioquorum.core.hashing import sha256_bytes


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
