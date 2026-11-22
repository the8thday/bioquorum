---
name: pdb-record-lookup
description: Locate the deposited record and coordinate file for a known PDB entry before inspecting its structure in BioQuorum.
license: Apache-2.0
---
Begin with a PDB accession, the molecule of interest and the question about the record. For public data, use an available RCSB connector for entry metadata. When the accession is known, download_file can retrieve the entry metadata from https://data.rcsb.org/rest/v1/core/entry/{id} and the deposited coordinates from https://files.rcsb.org/download/{id}.cif. Substitute the actual entry ID. Use an imported coordinate file for a project that cannot query public databases.

Check the entry title, experimental method, entity and chain against the question. Use the deposited asymmetric unit or the identified biological assembly as appropriate and state which one was inspected. Preserve the accession, assembly and chain when handing the file to the structure viewer. Report resolution only where the method and the record supply it. A catalog match or viewer rendering alone does not establish biological function.
