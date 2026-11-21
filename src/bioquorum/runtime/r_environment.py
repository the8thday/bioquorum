"""Files supplied by the project's IRkernel installation."""

from pathlib import Path


def r_spec_path(prefix: Path) -> Path | None:
    executable = prefix / "bin/R"
    if not executable.is_file() or not executable.resolve().is_relative_to(prefix.resolve()):
        return None
    for name in (
        "share/jupyter/kernels/ir/kernel.json",
        "lib/R/library/IRkernel/kernelspec/kernel.json",
    ):
        path = prefix / name
        if path.is_file() and path.resolve().is_relative_to(prefix.resolve()):
            return path
    return None


R_INVENTORY = """
local({
    p <- utils::installed.packages(noCache = TRUE)[, c("Package", "Version"), drop = FALSE]
    p <- p[order(p[, 1], p[, 2], method = "radix"), , drop = FALSE]
    cat(as.character(jsonlite::toJSON(list(R.version.string, R.version$platform, unname(p)),
                                      auto_unbox = TRUE)))
})
"""
