"""Dependency-free QC for the Cell Ranger PBMC 3k filtered matrix."""

from collections.abc import Iterable
from io import TextIOWrapper
from pathlib import Path, PurePosixPath
from tarfile import TarFile
from tarfile import open as open_tar

Entry = tuple[int, int, int]


def cell_metrics(entries: Iterable[Entry], n_cells: int, mt_indices: set[int]):
    genes = [0] * n_cells
    counts = [0] * n_cells
    mito = [0] * n_cells
    for gene, cell, umi in entries:
        index = cell - 1
        genes[index] += umi > 0
        counts[index] += umi
        if gene in mt_indices:
            mito[index] += umi
    return genes, counts, mito


def filter_counts(
    entries: Iterable[Entry],
    genes: list[int],
    counts: list[int],
    mito: list[int],
    n_genes: int,
    *,
    min_genes: int,
    max_genes: int,
    max_mt_percent: int,
    min_cells: int,
) -> tuple[list[bool], int]:
    keep = [
        min_genes <= detected <= max_genes and mt * 100 < total * max_mt_percent
        for detected, total, mt in zip(genes, counts, mito, strict=True)
    ]
    observations = [0] * n_genes
    for gene, cell, umi in entries:
        if keep[cell - 1] and umi > 0:
            observations[gene - 1] += 1
    return keep, sum(detections >= min_cells for detections in observations)


def _member(archive: TarFile, name: str) -> TextIOWrapper:
    matches = [m for m in archive.getmembers() if m.isfile() and PurePosixPath(m.name).name == name]
    if len(matches) != 1:
        raise ValueError(f"Expected one {name} in the Cell Ranger archive")
    handle = archive.extractfile(matches[0])
    assert handle is not None
    return TextIOWrapper(handle, encoding="utf-8")


def _entries(matrix: TextIOWrapper) -> Iterable[Entry]:
    for line in matrix:
        gene, cell, umis = map(int, line.split())
        yield gene, cell, umis


def _dimensions(matrix: TextIOWrapper) -> tuple[int, int]:
    header = matrix.readline().strip()
    if header not in (
        "%%MatrixMarket matrix coordinate integer general",
        "%%MatrixMarket matrix coordinate real general",
    ):
        raise ValueError("Expected a Matrix Market coordinate matrix")
    for line in matrix:
        if not line.startswith("%"):
            genes, cells, _ = map(int, line.split())
            return genes, cells
    raise ValueError("Missing matrix dimensions")


def _qc_svg(
    genes: list[int],
    counts: list[int],
    mito: list[int],
    keep: list[bool],
    bounds: tuple[int, int, int],
) -> str:
    x_max = max(1000, 500 * ((max(genes) + 499) // 500))
    fractions = [100 * m / n if n else 0.0 for m, n in zip(mito, counts, strict=True)]
    y_max = max(5.0, max(fractions))
    points = []
    for n, frac, retained in zip(genes, fractions, keep, strict=True):
        x, y = 65 + 565 * n / x_max, 360 - 305 * frac / y_max
        color = "#207f83" if retained else "#a84e40"
        points.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2" fill="{color}" opacity=".55"/>')
    ticks = []
    for value in (0, 5, 10, 20, 40, 60, 80, 100):
        if value <= y_max:
            y = 360 - 305 * value / y_max
            ticks.append(f'<text x="55" y="{y + 4:.1f}" text-anchor="end">{value}</text>')
    for value in range(0, x_max + 1, 500):
        x = 65 + 565 * value / x_max
        ticks.append(f'<text x="{x:.1f}" y="379" text-anchor="middle">{value:,}</text>')
    min_genes, max_genes, max_mt = bounds
    ymin = 360 - 305 * max_mt / y_max
    guides = [f"M65 {ymin:.1f}H630"]
    for limit in (min_genes, max_genes):
        if limit <= x_max:
            guides.append(f"M{65 + 565 * limit / x_max:.1f} 50V360")
    path = " ".join(guides)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="675" height="430" '
        'viewBox="0 0 675 430" role="img" aria-label="PBMC 3k quality control">'
        '<rect width="675" height="430" fill="white"/>'
        '<g font-family="sans-serif" font-size="12" fill="#202b32">'
        '<text x="65" y="30" font-size="20">PBMC 3k quality control</text>'
        '<path d="M65 50V360H630" fill="none" stroke="#68727a"/>'
        f'<path d="{path}" fill="none" stroke="#a0a8ae" stroke-dasharray="4 4"/>'
        + "".join(points)
        + "".join(ticks)
        + '<text x="340" y="400" text-anchor="middle">Detected genes per cell</text>'
        '<text transform="translate(18 205) rotate(-90)" text-anchor="middle">Mitochondrial UMI (%)</text>'
        '<text x="515" y="30" fill="#207f83">Retained</text>'
        '<text x="579" y="30" fill="#a84e40">Filtered</text></g></svg>'
    )


def run_qc(path: str, declare) -> str:
    """Record all numerical claims using the kernel's bq.value callback."""
    min_genes, max_genes, max_mt_percent, min_cells = 200, 2500, 5, 3
    with open_tar(Path(path), "r:gz") as archive:
        with _member(archive, "genes.tsv") as features:
            symbols = [line.rstrip("\n").split("\t")[1] for line in features]
        with _member(archive, "matrix.mtx") as matrix:
            n_genes, n_cells = _dimensions(matrix)
            if n_genes != len(symbols):
                raise ValueError("Feature list and sparse matrix disagree")
            mitochondrial = {
                i for i, name in enumerate(symbols, 1) if name.upper().startswith("MT-")
            }
            genes, counts, mito = cell_metrics(_entries(matrix), n_cells, mitochondrial)
        with _member(archive, "matrix.mtx") as matrix:
            _dimensions(matrix)
            keep, genes_after = filter_counts(
                _entries(matrix),
                genes,
                counts,
                mito,
                n_genes,
                min_genes=min_genes,
                max_genes=max_genes,
                max_mt_percent=max_mt_percent,
                min_cells=min_cells,
            )
    for name, value in {
        "cells_before": n_cells,
        "genes_before": n_genes,
        "cells_after": sum(keep),
        "genes_after": genes_after,
        "min_genes": min_genes,
        "max_genes": max_genes,
        "max_mt_percent": max_mt_percent,
        "min_cells": min_cells,
    }.items():
        declare(name, value)
    return _qc_svg(genes, counts, mito, keep, (min_genes, max_genes, max_mt_percent))
