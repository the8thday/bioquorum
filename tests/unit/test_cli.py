from typer.testing import CliRunner

from bioquorum import __version__
from bioquorum.cli.main import app

runner = CliRunner()


def test_version():
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0 and __version__ in r.output


def test_db_init_and_rebuild(tmp_path):
    db = tmp_path / "bq.db"
    assert runner.invoke(app, ["db", "init", "--db", str(db)]).exit_code == 0
    assert db.exists()
    r = runner.invoke(app, ["db", "rebuild", "--db", str(db)])
    assert r.exit_code == 0 and "replayed 0 events" in r.output
    r = runner.invoke(app, ["db", "events", "--db", str(db)])
    assert r.exit_code == 0
