from evals.v02_pbmc3k import cell_metrics, filter_counts


def test_qc_uses_mitochondrial_umis_and_counts_genes_after_cell_filtering():
    entries = [(1, 1, 100), (2, 1, 1), (1, 2, 9), (2, 2, 4), (3, 3, 20)]
    genes, counts, mito = cell_metrics(entries, 3, {2})
    assert (genes, counts, mito) == ([2, 2, 1], [101, 13, 20], [1, 4, 0])
    keep, gene_count = filter_counts(
        entries,
        genes,
        counts,
        mito,
        3,
        min_genes=2,
        max_genes=5,
        max_mt_percent=5,
        min_cells=1,
    )
    assert keep == [True, False, False]
    assert gene_count == 2
