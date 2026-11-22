---
name: omics-workflows
description: Inspect measured omics tables and sample designs before choosing a contrast.
license: Apache-2.0
---
Use the recorded table and metadata to state the measurement scale, assay, rows, experimental unit and independent sample counts. Cross-tabulate condition against donor and batch. If a contrast is estimable, keep paired samples together and show replicate-level sensitivity. If it is confounded, describe which additional samples resolve it. List exclusions and denominators. Report effects, uncertainty and multiplicity at the level of inference.

Tools and packages: use python_execute or r_execute with declared input/output files. Check installed packages before proposing DESeq2, limma or anndata. Retrieve accession metadata using enabled connectors only.

Data license and limits: record the terms attached to each dataset or publication. The Skill license covers these instructions. Project data retain their own access and reuse conditions. Mark missing metadata as unknown.
