# v0.4 example entry points

| Scenario | Entry point | Inputs |
| --- | --- | --- |
| A · Target diligence | Ask about TYK2 and psoriasis in a configured public workbench. See the source and evidence steps in [the demo](../../docs/demo-v0.3.md). | User-configured literature and target-data tools |
| B · Compute and verify | Run the real Jupyter cell-count example in [the v0.4 demo](../../docs/demo-v0.4.md). The [v0.2 PBMC walkthrough](../../docs/demo-v0.2.md) records PBMC QC from real data. | A built-in synthetic count table, or the published PBMC dataset for the earlier workflow |
| C · Literature evidence | Run `bioquorum` outside a BioQuorum project and choose the metformin example. | Packaged PubMed excerpts with offline cassette and FakeKernel |

`pixi.toml` specifies a Linux environment for the reproducibility example. Use
pixi to generate its lock and provision it before selecting the pixi runtime.
