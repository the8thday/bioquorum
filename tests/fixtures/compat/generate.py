"""Build public upgrade fixtures with each historical checkout's actual project writer."""

import os
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

COMMITS = {
    6: "2f3b3a2",
    7: "22428a0",
    8: "9b63a48",
    9: "a5058bd",
    10: "9bb81f2",
    11: "255bf33",
    12: "fe2d8b9",
}
WRITER = """
from pathlib import Path
import sys
from bioquorum.application import workspace
from bioquorum.core import events as ev
from bioquorum.core.enums import Actor, Classification
from bioquorum.core.ids import new_id
from bioquorum.storage.events import EventStore
assert Path(workspace.__file__).resolve().is_relative_to(Path(sys.argv[1]))
root, title = Path(sys.argv[2]), sys.argv[3]
ws = workspace.init_project(root, name=title, classification=Classification.PUBLIC)
(root/'bioquorum.toml').write_text('[providers.ollama]\\n[roles]\\nworker = "ollama:fixture"\\n')
store = EventStore(ws.database())
sid = new_id()
store.append(ev.SessionCreated(project_id=ws.project_id, title=title), session_id=sid, actor=Actor.USER)
options = {}
if hasattr(ev, "ArtifactSelection"):
    options["references"] = [ev.TurnReference(kind="artifact", id=new_id(), selection={"type": "table", "row": 1})]
store.append(ev.TurnRequested(question="Continue "+title+" research", **options), session_id=sid, actor=Actor.USER)
if hasattr(ev, "ProgramCreated"):
    store.append(ev.ProgramCreated(project_id=ws.project_id, program_id=new_id(), name="Legacy line"), session_id=None, actor=Actor.USER)
if hasattr(ev, "SubtaskStarted"):
    task, call = new_id(), new_id()
    store.append(ev.SubtaskStarted(task_id=task, parent_call_id=call, question="Check the source"), session_id=sid, actor=Actor.AGENT)
    store.append(ev.SubtaskFinished(task_id=task, parent_call_id=call, outcome="completed"), session_id=sid, actor=Actor.AGENT)
if hasattr(ev, "CoveragePlanned"):
    store.append(ev.CoveragePlanned(topics={"source": "Read the paper"}), session_id=sid, actor=Actor.AGENT)
"""


def main() -> None:
    for minor, commit in COMMITS.items():
        with TemporaryDirectory(dir="/tmp", prefix="bq-compat-") as tmp:
            base = Path(tmp).resolve()
            files = subprocess.check_output(["git", "archive", commit, "src"])
            with tarfile.open(fileobj=BytesIO(files)) as tree:
                tree.extractall(base, filter="data")
            project = base / "project"
            src = str(base / "src")
            subprocess.run(
                [sys.executable, "-c", WRITER, src, str(project), f"v0.{minor}"],
                env={**os.environ, "PYTHONPATH": src},
                cwd=base,
                check=True,
            )
            target = Path(__file__).with_name(f"v0.{minor}.zip")
            with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
                for name in (
                    "bioquorum.toml",
                    ".bioquorum/project.json",
                    ".bioquorum/bioquorum.db",
                ):
                    archive.write(project / name, name)


if __name__ == "__main__":
    main()
