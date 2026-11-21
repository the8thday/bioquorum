# BioQuorum research bundle

- [Manifest v0](manifest-v0.schema.json) is the pre-export record schema.
- [Manifest v1](manifest-v1.schema.json) is the portable archive schema. It keeps
  v0 fields and adds per-run input paths, prior cell IDs, stdout hashes,
  environment fingerprints, and the locations of claim bindings and the pixi lock.

All filenames in the manifest are archive-relative. Objects are addressed as
`objects/<sha256>`. A replay restores declared inputs into a fresh directory,
executes prior successful cells from the same kernel, and compares named values,
stdout and captured image hashes. It records environment identity separately.
A manifest verifies bytes against its own checksums. Authenticity and distribution
permissions remain the responsibility of the sender and receiver.

An evidence-only archive can have empty `runs` and `run_records`. Run `sh rerun.sh --verify-only`
to check integrity. `Methods.md` is an editable draft listed in the file hashes when present.

`conversation.jsonl` is a scoped export, omitting raw provider payloads and
unrelated event streams. See [the verification walkthrough](../docs/demo-v0.4.md)
for instructions and environment constraints. The schema files are generated
with `uv run python scripts/gen_bundle_schema.py`.

Bilingual archives may also carry `cell_inputs`, `written_files`, and
`kernel_group` in each run record. The runner starts one kernel per recorded
group, executes the preceding Python and R cells in dependency order, and checks
each declared handoff before the consumer reads it. Declared output files are
included as content-addressed objects. Archives with R cells include the R value
helper and the R inventory command.
