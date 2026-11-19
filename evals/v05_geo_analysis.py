"""Reanalyse the author-labelled GSE96583 batch 2 droplet counts."""

from __future__ import annotations

import gzip
import itertools
from html import escape
from pathlib import Path
from tarfile import TarFile
from tarfile import open as open_tar

import numpy as np
import pandas as pd

SAMPLES = (("GSM2560248", "2.1", "ctrl"), ("GSM2560249", "2.2", "stim"))
MIN_GENES = 200
MIN_MONOCYTES = 30
MIN_PAIR_COUNTS = 10
MIN_EXPRESSED_PAIRS = 3
MIN_DONOR_PAIRS = 3
MAX_CLUSTER_CELLS = 4000
HVG = 250
CLUSTERS = 8
SEED = 17
PALETTE = ("#276b94", "#b34e38", "#367c52", "#87578e", "#d38c17", "#117974", "#506271", "#a53e73")


def align_metadata(metadata: pd.DataFrame, barcodes: list[str], condition: str) -> pd.DataFrame:
    if (
        not metadata.index.is_unique
        or len(set(barcodes)) != len(barcodes)
        or not pd.Index(barcodes).isin(metadata.index).all()
    ):
        raise ValueError("Cell barcode mapping was missing or duplicated")
    cells = metadata.loc[barcodes].copy()
    if not cells["stim"].eq(condition).all():
        raise ValueError("Matrix condition disagrees with the author metadata")
    return cells


def author_barcodes(metadata: pd.DataFrame, barcodes: list[str], condition: str) -> list[str]:
    keys = list(metadata.index[metadata["stim"].eq(condition)])
    mapping = {key.rsplit("-", 1)[0]: key for key in keys}
    raw = [key.rsplit("-", 1)[0] for key in barcodes]
    if len(mapping) != len(keys) or len(set(raw)) != len(raw) or not set(raw) <= mapping.keys():
        raise ValueError("Raw barcode sequences do not map uniquely within the GEO library")
    return [mapping[key] for key in raw]


def paired_effects(differences: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Exact two-sided randomization of paired, donor-level log-CPM differences."""
    permutations = np.array(list(itertools.product((-1.0, 1.0), repeat=len(differences))))
    observed = np.abs(differences.sum(axis=0))
    random = np.abs(permutations @ differences)
    return differences.mean(axis=0), (random + 1e-10 >= observed).mean(axis=0)


def adjust_bh(p_values: np.ndarray) -> np.ndarray:
    order = np.argsort(p_values, kind="stable")
    correction = p_values[order] * len(order) / np.arange(1, len(order) + 1)
    out = np.empty_like(p_values)
    out[order] = np.minimum(1, np.minimum.accumulate(correction[::-1])[::-1])
    return out


def read_sample(archive: TarFile, accession: str, matrix_id: str):
    barcode_file = archive.extractfile(f"{accession}_barcodes.tsv.gz")
    matrix_file = archive.extractfile(f"{accession}_{matrix_id}.mtx.gz")
    if barcode_file is None or matrix_file is None:
        raise ValueError("The GEO archive omitted a matrix or barcode file")
    with barcode_file as stored, gzip.open(stored, "rt") as stream:
        barcodes = [line.rstrip() for line in stream]
    with matrix_file as stored, gzip.open(stored, "rt") as stream:
        matrix = np.loadtxt(stream, comments="%", dtype=np.int32, ndmin=2)
    genes, cells, nnz = matrix[0]
    g, c, counts = matrix[1:].T
    if (
        cells != len(barcodes)
        or nnz != len(counts)
        or not len(counts)
        or g.min() < 1
        or g.max() > genes
        or c.min() < 1
        or c.max() > cells
        or counts.min() <= 0
    ):
        raise ValueError("Invalid matrix dimensions, indices or counts")
    return barcodes, g - 1, c - 1, counts, genes


def kmeans(scores: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Cluster PCA scores with a seeded restart protocol."""
    best_loss = np.inf
    best_labels = np.empty(len(scores), dtype=int)
    for _ in range(5):
        centers = scores[rng.choice(len(scores), CLUSTERS, replace=False)].copy()
        for _ in range(60):
            distances = ((scores[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
            labels = distances.argmin(axis=1)
            updated = np.array(
                [
                    scores[labels == i].mean(axis=0) if (labels == i).any() else center
                    for i, center in enumerate(centers)
                ]
            )
            if np.allclose(centers, updated, rtol=0, atol=1e-8):
                break
            centers = updated
        loss = float(np.min(distances, axis=1).sum())
        if loss < best_loss:
            best_loss = loss
            best_labels = labels.copy()
    return best_labels


def scatter(x, y, groups, title: str, xlabel: str, ylabel: str) -> str:
    fig = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="820" height="510" viewBox="0 0 820 510">',
        '<rect width="820" height="510" fill="white"/>',
        '<g font-family="sans-serif" fill="#19282d">',
        f'<text x="75" y="31" font-size="21">{escape(title)}</text>',
        '<path d="M75 54V425H615" stroke="#505b62" fill="none"/>',
        f'<text x="345" y="490" text-anchor="middle">{escape(xlabel)}</text>',
        f'<text transform="translate(18 240) rotate(-90)" text-anchor="middle">{escape(ylabel)}</text>',
    ]
    xmin, xmax, ymin, ymax = float(x.min()), float(x.max()), float(y.min()), float(y.max())
    xspan, yspan = max(xmax - xmin, 1e-10), max(ymax - ymin, 1e-10)
    for t in range(5):
        xpos, ypos = 75 + 540 * t / 4, 425 - 370 * t / 4
        fig.append(
            f'<text x="{xpos:.1f}" y="446" text-anchor="middle" font-size="11">{xmin + xspan * t / 4:.2g}</text>'
        )
        fig.append(
            f'<text x="67" y="{ypos + 4:.1f}" text-anchor="end" font-size="11">{ymin + yspan * t / 4:.2g}</text>'
        )
    for i, group in enumerate(sorted(set(groups))):
        color = PALETTE[i]
        fig.append(
            f'<text x="635" y="{75 + 23 * i}" fill="{color}" font-size="13">{escape(str(group))}</text>'
        )
        group_indices = np.flatnonzero(groups == group)
        for j in group_indices:
            px = 75 + 540 * (x[j] - xmin) / xspan
            py = 425 - 370 * (y[j] - ymin) / yspan
            fig.append(
                f'<circle cx="{px:.2f}" cy="{py:.2f}" r="1.7" fill="{color}" opacity="0.52"/>'
            )
    return "".join([*fig, "</g></svg>"])


def mitochondrial_qc(counts, totals, eligible, ceiling):
    fraction = 100 * counts / np.maximum(totals, 1)
    detected = int(np.count_nonzero(counts))
    passed = fraction < ceiling if detected else np.ones(len(counts), dtype=bool)
    return (
        passed,
        fraction,
        {
            "umi": int(counts.sum()),
            "positive_cells": detected,
            "excluded": int(np.count_nonzero(eligible & ~passed)),
        },
    )


def run_geo(
    paths: list[str], max_mt: float, min_genes: int, destination: str, declare, show
) -> None:
    """Record QC, descriptive clusters and donor-paired monocyte differential expression."""
    archive, features, annotations = paths
    genes = pd.read_csv(features, sep="\t", header=None, names=["ensembl", "symbol"])
    metadata = pd.read_csv(annotations, sep="\t", index_col=0, dtype={"ind": str})
    mt = genes["symbol"].str.startswith("MT-", na=False).to_numpy()
    if not 0 < max_mt <= 100:
        raise ValueError("Invalid mitochondrial percentage threshold")
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(SEED)
    pseudobulk = {}
    detected_counts, mt_fractions, kept_cells = [], [], []
    cells_for_cluster, matrices = [], []
    mt_reports = {}
    library_sizes = []
    with open_tar(archive) as tar:
        barcodes_seen = set()
        for acc, matrix_id, condition in SAMPLES:
            raw_barcodes, g, c, counts, n_genes = read_sample(tar, acc, matrix_id)
            if n_genes != len(genes):
                raise ValueError("Gene list does not describe the count matrix")
            barcodes = author_barcodes(metadata, raw_barcodes, condition)
            barcodes_seen.update(barcodes)
            info = align_metadata(metadata, barcodes, condition)
            total = np.bincount(c, weights=counts, minlength=len(info))
            detected = np.bincount(c, minlength=len(info))
            mitochondrial_counts = np.bincount(c[mt[g]], weights=counts[mt[g]], minlength=len(info))
            eligible = (detected >= min_genes) & info["multiplets"].eq("singlet").to_numpy()
            passed, fraction, report = mitochondrial_qc(
                mitochondrial_counts, total, eligible, max_mt
            )
            mt_reports[condition] = report
            library_sizes.append(total)
            keep = eligible & passed
            detected_counts.append(detected)
            mt_fractions.append(fraction)
            kept_cells.append(keep)
            monocytes = (
                keep
                & info["cell"].eq("CD14+ Monocytes").to_numpy()
                & info["ind"].notna().to_numpy()
            )
            for donor, group in info.loc[monocytes].groupby("ind"):
                if len(group) >= MIN_MONOCYTES:
                    entry = pd.Index(info.index).isin(group.index)[c]
                    pseudobulk[donor, condition] = (
                        np.bincount(g[entry], weights=counts[entry], minlength=n_genes),
                        len(group),
                    )
            accepted = np.flatnonzero(keep)
            if not len(accepted):
                raise ValueError(f"No cells passed QC for {condition}")
            chosen = np.sort(
                rng.choice(accepted, min(len(accepted), MAX_CLUSTER_CELLS // 2), replace=False)
            )
            lookup = np.full(len(info), -1, dtype=int)
            lookup[chosen] = np.arange(len(chosen))
            take = lookup[c] >= 0
            matrices.append(
                (g[take], lookup[c[take]], np.log1p(1e4 * counts[take] / total[c[take]]))
            )
            cells_for_cluster.append(info.iloc[chosen])
    if barcodes_seen != set(metadata.index):
        raise ValueError("Metadata contains cells missing from the batch 2 matrices")

    cells = pd.concat(cells_for_cluster)
    sums = sum(np.bincount(g, weights=log, minlength=len(genes)) for g, _, log in matrices)
    sums2 = sum(np.bincount(g, weights=log**2, minlength=len(genes)) for g, _, log in matrices)
    var = sums2 / len(cells) - (sums / len(cells)) ** 2
    hvg = np.argsort(-var, kind="stable")[:HVG]
    gene_lookup = np.full(len(genes), -1, dtype=int)
    gene_lookup[hvg] = np.arange(len(hvg))
    parts = []
    for cell_frame, (g, c, log) in zip(cells_for_cluster, matrices, strict=True):
        columns = gene_lookup[g]
        mask = columns >= 0
        dense = np.zeros((len(cell_frame), len(hvg)))
        dense[c[mask], columns[mask]] = log[mask]
        parts.append(dense)
    dense = np.vstack(parts)
    dense -= dense.mean(axis=0)
    _, loadings = np.linalg.eigh(dense.T @ dense)
    loadings = loadings[:, -15:][:, ::-1]
    poles = np.argmax(np.abs(loadings), axis=0)
    loadings *= np.sign(loadings[poles, np.arange(loadings.shape[1])])
    scores = dense @ loadings
    clusters = kmeans(scores, rng) + 1
    assignments = pd.DataFrame(
        {
            "cluster": clusters,
            "donor": cells["ind"],
            "condition": cells["stim"],
            "author_cell_type": cells["cell"],
            "PC1": scores[:, 0],
            "PC2": scores[:, 1],
        }
    )
    assignments.to_csv(output / "clusters.csv", index_label="barcode", float_format="%.8g")

    pairs = sorted(d for d, c in pseudobulk if c == "ctrl" and (d, "stim") in pseudobulk)
    if len(pairs) < MIN_DONOR_PAIRS:
        raise ValueError("Insufficient paired donors for differential expression")
    control = np.vstack([pseudobulk[d, "ctrl"][0] for d in pairs])
    treated = np.vstack([pseudobulk[d, "stim"][0] for d in pairs])
    tested = (np.maximum(control, treated) >= MIN_PAIR_COUNTS).sum(axis=0) >= MIN_EXPRESSED_PAIRS
    control_cpm = 1e6 * control[:, tested] / control.sum(axis=1, keepdims=True)
    treated_cpm = 1e6 * treated[:, tested] / treated.sum(axis=1, keepdims=True)
    effect, p_value = paired_effects(np.log2(treated_cpm + 1) - np.log2(control_cpm + 1))
    q_value = adjust_bh(p_value)
    de = genes.loc[tested].assign(
        mean_paired_log2_cpm_change=effect, p_value=p_value, bh_fdr=q_value
    )
    de = de.sort_values(
        ["bh_fdr", "p_value", "mean_paired_log2_cpm_change"], ascending=[True, True, False]
    )
    de.to_csv(output / "monocyte-de.csv", index=False, float_format="%.8g")
    pd.DataFrame(
        [
            {
                "donor": d,
                "control_cells": pseudobulk[d, "ctrl"][1],
                "stimulated_cells": pseudobulk[d, "stim"][1],
            }
            for d in pairs
        ]
    ).to_csv(output / "paired-donors.csv", index=False)

    kept = np.concatenate(kept_cells)
    detected = np.concatenate(detected_counts)
    mt_percent = np.concatenate(mt_fractions)
    display_indices = rng.choice(len(kept), min(5000, len(kept)), replace=False)
    print(
        "GSE96583 batch 2: author singlet QC, log1p counts per 10,000, variance selection, PCA, k-means."
    )
    print(
        "DE: author CD14+ monocytes, raw-count donor-by-condition sums, paired exact sign-flip of log2(CPM+1), BH."
    )
    print(pd.crosstab(assignments["cluster"], assignments["author_cell_type"]).to_csv())
    print(de.head(16).to_csv(index=False, float_format="%.5g"))
    for name, value, detail in (
        ("cells_before", len(kept), "Submitted batch 2 barcodes"),
        ("cells_after", int(kept.sum()), "Author singlets passing the QC thresholds"),
        ("min_detected_genes", min_genes, "Minimum detected genes per barcode"),
        ("max_mt_percent", max_mt, "Exclusive mitochondrial UMI percentage ceiling"),
        (
            "clustered_cells",
            len(cells),
            "Seeded balanced sample across conditions for exploratory clustering",
        ),
        (
            "clusters",
            len(set(clusters)),
            "Descriptive k-means clusters in the first fifteen principal components",
        ),
        ("paired_donors", len(pairs), "Donors with eligible CD14+ monocytes in both libraries"),
        (
            "min_monocytes",
            MIN_MONOCYTES,
            "Minimum author-labelled monocytes per donor and condition",
        ),
        (
            "genes_tested",
            int(tested.sum()),
            "Genes eligible for the donor-paired differential expression analysis",
        ),
        (
            "genes_at_fdr_05",
            int((q_value <= 0.05).sum()),
            "Genes with BH-adjusted p-values at or below 0.05",
        ),
        ("random_seed", SEED, "PCA display subsampling and k-means seed"),
        (
            "normalization_method",
            "Library-size scale to 10,000 followed by log1p",
            "Normalization for exploratory clustering",
        ),
        (
            "clustering_method",
            "Variance-ranked genes, centered PCA and seeded k-means",
            "Exploratory clustering method",
        ),
        (
            "de_method",
            "Author CD14+ monocyte raw-count sum per donor and condition, paired exact sign-flip of log2(CPM+1), Benjamini-Hochberg",
            "Donor-level differential expression method",
        ),
    ):
        declare(name, value, desc=detail)
    evaluable = []
    for library, report in mt_reports.items():
        for metric, value in report.items():
            declare(
                f"mt_{library}_{metric}",
                value,
                desc=f"Mitochondrial {metric} in {library}; excluded counts otherwise eligible barcodes",
            )
        evaluable.append(
            f"{library}: "
            + ("evaluated" if report["positive_cells"] else "unavailable (no mitochondrial UMIs)")
        )
    declare("mt_qc_status", ", ".join(evaluable), desc="Mitochondrial QC observability by library")
    plot_mt = all(report["positive_cells"] for report in mt_reports.values())
    show(
        scatter(
            detected[display_indices],
            (mt_percent if plot_mt else np.concatenate(library_sizes))[display_indices],
            np.where(kept[display_indices], "Retained", "Excluded"),
            "GSE96583 batch 2 barcode QC",
            "Detected genes",
            "Mitochondrial UMI (%)" if plot_mt else "Total UMI count",
        )
    )
    show(
        scatter(
            scores[:, 0],
            scores[:, 1],
            clusters,
            "GSE96583 batch 2 exploratory clusters",
            "PC1",
            "PC2",
        )
    )
