# BioQuorum Research Bundle Specification

A research bundle is a directory that lets anyone rerun an analysis **without installing BioQuorum** (only `pixi`). See `docs/ARCHITECTURE.md §6.3` and ADR-010.

- `manifest-v0.schema.json`: JSON Schema of `manifest.json`, generated from `bioquorum.core.bundle.Manifest`. Fields are only ever added, never removed.
- Layout, `rerun.sh` semantics and v1.0 of the spec arrive with ROADMAP v0.3.
