---
name: literature-record-lookup
description: Retrieve primary literature and clinical registry records for a public target overview, and trace the findings to their source IDs.
license: Apache-2.0
---
For a broad target question, search the gene symbol and common name with search_papers. Record the terms and select relevant titles. Pass the returned PMIDs to get_abstract before using a paper to support a claim. citation_lookup checks whether an identifier exists; its result alone supplies no biological findings.

For a clinical pipeline question, use clinical_trials with the target and then with a known intervention name where available. Record the registry ID, intervention, condition, phase and recruitment status as distinct fields. A trial registration establishes that a study was registered. Report outcomes only from posted results or a publication linked to that study.

Use only sources enabled for the current project. If a query is blocked, report the tool and the reason; if it returns no rows, report the database and exact terms. Neither outcome establishes that no studies exist. In a restricted project, work from imported abstracts or trial records and identify their provenance.
