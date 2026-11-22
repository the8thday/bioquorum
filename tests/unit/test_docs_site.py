from pathlib import Path

from scripts.build_docs import build


def test_offline_guide_links_resolve_under_a_subpath(tmp_path: Path):
    repo = tmp_path / "repo"
    docs = repo / "docs"
    docs.mkdir(parents=True)
    (docs / "index.md").write_text("# Guide\n\n[Install](install.md#setup)\n")
    (docs / "install.md").write_text("# Install\n\n## Setup\n\nDetails.\n")
    logo = repo / "web/public/logo.svg"
    logo.parent.mkdir(parents=True)
    logo.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (repo / "README.md").write_text("# ![](web/public/logo.svg) BioQuorum")
    out = tmp_path / "site"
    build(repo, out)
    assert 'href="install.html#setup"' in (out / "index.html").read_text()
    assert 'id="setup"' in (out / "install.html").read_text()
    readme = (out / "project/README.html").read_text()
    assert (
        '<h1 id="bioquorum"><img src="../assets/web/public/logo.svg" alt="" /> BioQuorum</h1>'
        in readme
    )
    assert "<title>BioQuorum · BioQuorum</title>" in readme
    assert (out / "assets/web/public/logo.svg").read_bytes() == logo.read_bytes()
