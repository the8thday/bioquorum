"""Build the static handbook from repository Markdown with no network requests."""

import argparse
import html
import os
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

REPO_URL = "https://github.com/the8thday/bioquorum/blob/main/"
CSS = """
:root{font:17px/1.68 system-ui,sans-serif;color:#1e3440;background:#fafaf7}
body{margin:auto;max-width:62rem;padding:2rem clamp(1rem,5vw,4rem) 5rem}
header{border-bottom:1px solid #bfcac7;padding-bottom:.8rem} header a{font-weight:650}
h1,h2,h3{line-height:1.22;color:#173b38}h2{margin-top:2.2rem}
a{color:#12645b;text-underline-offset:.16em}pre,code{font-family:ui-monospace,monospace}
pre{padding:1rem;background:#eaf0ee;overflow:auto}code{overflow-wrap:anywhere}
table{display:block;overflow:auto;border-collapse:collapse}td,th{border:1px solid #cdd6d3;padding:.4rem .7rem;text-align:left}
img{max-width:100%}h1 img{height:1.2em;width:auto;vertical-align:-.22em}blockquote{margin-left:0;padding-left:1rem;border-left:3px solid #8db4aa}
"""


def build(repo: Path, output: Path) -> None:
    docs = repo / "docs"
    pages = {p: p.relative_to(docs).with_suffix(".html") for p in docs.rglob("*.md")}
    for name in ("README.md", "ROADMAP.md", "SECURITY.md", "CHANGELOG.md", "bundle-spec/README.md"):
        path = repo / name
        if path.exists():
            pages[path] = Path("project") / Path(name).with_suffix(".html")
    engine = MarkdownIt("commonmark", {"html": False}).enable("table")
    for src, page in pages.items():
        dest = output / page
        dest.parent.mkdir(parents=True, exist_ok=True)
        tokens = engine.parse(src.read_text(encoding="utf-8"))
        headings: set[str] = set()
        title = "BioQuorum"
        for i, token in enumerate(tokens):
            if token.type == "heading_open":
                heading = engine.renderer.renderInlineAsText(
                    tokens[i + 1].children, engine.options, {}
                ).strip()
                if token.tag == "h1":
                    title = heading
                base = re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
                slug, count = base, 0
                while slug in headings:
                    count += 1
                    slug = f"{base}-{count}"
                headings.add(slug)
                token.attrSet("id", slug)
            for child in token.children or ():
                key = "src" if child.type == "image" else "href"
                url = child.attrGet(key)
                if not url:
                    continue
                parts = urlsplit(url)
                if parts.scheme or parts.netloc or not parts.path:
                    continue
                target = (src.parent / parts.path).resolve()
                if not target.is_relative_to(repo):
                    continue
                if target in pages:
                    link = os.path.relpath(output / pages[target], dest.parent)
                elif target.is_file() and target.suffix in {
                    ".json",
                    ".csv",
                    ".gif",
                    ".png",
                    ".svg",
                    ".webp",
                    ".mp4",
                    ".zip",
                }:
                    asset = output / "assets" / target.relative_to(repo)
                    asset.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(target, asset)
                    link = os.path.relpath(asset, dest.parent)
                else:
                    base = REPO_URL.replace("/blob/", "/tree/") if target.is_dir() else REPO_URL
                    link = base + target.relative_to(repo).as_posix()
                child.attrSet(key, link + ("#" + parts.fragment if parts.fragment else ""))
        body = engine.renderer.render(tokens, engine.options, {})
        home = os.path.relpath(output / "index.html", dest.parent)
        dest.write_text(
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{html.escape(title)} · BioQuorum</title><style>{CSS}</style>"
            f'<header><a href="{home}">BioQuorum · 使用手册</a></header><main>{body}</main></html>',
            encoding="utf-8",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("build/docs"))
    build(Path(__file__).resolve().parents[1], parser.parse_args().output.resolve())
