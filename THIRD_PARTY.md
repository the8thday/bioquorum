# Third-party software and data

The BioQuorum wheel ships BioQuorum source, its static workbench, and a small
offline replay. It declares its Python dependencies by package name. The workbench build uses React, React DOM and Scheduler.

| Dependency | License | Usage |
| --- | --- | --- |
| [FastAPI](https://github.com/fastapi/fastapi/blob/master/LICENSE) | MIT | Local HTTP application |
| [Starlette](https://github.com/Kludex/starlette/blob/main/LICENSE.md) | BSD-3-Clause | ASGI requests, responses and streaming |
| [Uvicorn](https://github.com/Kludex/uvicorn/blob/main/LICENSE.md) | BSD-3-Clause | Loopback server |
| [Pydantic](https://github.com/pydantic/pydantic/blob/main/LICENSE) and [Pydantic AI Slim](https://github.com/pydantic/pydantic-ai/blob/main/LICENSE) | MIT | Schemas and model protocol |
| [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy/blob/main/LICENSE) and [Alembic](https://github.com/sqlalchemy/alembic/blob/main/LICENSE) | MIT | Record storage and migrations |
| [Jupyter Client](https://github.com/jupyter/jupyter_client/blob/main/COPYING.md) and [ipykernel](https://github.com/ipython/ipykernel/blob/main/COPYING.md) | BSD-3-Clause | Python kernel |
| [MCP SDK](https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE) | MIT | MCP client and server |
| [Typer](https://github.com/fastapi/typer/blob/master/LICENSE), [Rich](https://github.com/Textualize/rich/blob/master/LICENSE), and [python-ulid](https://github.com/mdomke/python-ulid/blob/main/LICENSE) | MIT | Terminal UI and record identifiers |
| [HTTPX2](https://pypi.org/project/httpx2/) | BSD-3-Clause | HTTP client for tools |
| [PyYAML](https://github.com/yaml/pyyaml/blob/main/LICENSE) | MIT | Agent Skills frontmatter |
| [React](https://github.com/facebook/react/tree/v19.3.1/packages/react) | MIT | Workbench components |
| [React DOM](https://github.com/facebook/react/tree/v19.3.1/packages/react-dom) | MIT | Render components into the browser document |
| [Scheduler](https://github.com/facebook/react/tree/v19.3.1/packages/scheduler) | MIT | React DOM runtime dependency |
| [Playwright](https://github.com/microsoft/playwright/blob/main/LICENSE) | Apache-2.0 | Browser tests only |

See [uv.lock](uv.lock), [workbench lock](web/package-lock.json) and [browser test lock](tests/e2e/package-lock.json) for
exact resolved package versions, including transitive dependencies. Each
dependency is distributed under its own license.

The metformin walkthrough includes bibliographic metadata and short verbatim
excerpts credited to the original authors in [PubMed 11118008](https://pubmed.ncbi.nlm.nih.gov/11118008/),
[21947382](https://pubmed.ncbi.nlm.nih.gov/21947382/) and
[27216492](https://pubmed.ncbi.nlm.nih.gov/27216492/). No paper or full abstract is
bundled. Example scientific content and the synthetic replay are described in
[the v0.3 walkthrough](docs/demo-v0.3.md).

## React, React DOM and Scheduler

MIT License

Copyright (c) Meta Platforms, Inc. and affiliates.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
