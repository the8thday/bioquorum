# Installed inside the project kernel and included in portable research bundles.
options(repr.matrix.max.rows = 6, repr.matrix.max.cols = 8,
        jupyter.plot_mimetypes = "image/png")

bq_value <- function(name, value, unit = NULL, desc = NULL) {
    short_string <- function(x, limit) {
        is.character(x) && length(x) == 1L && !is.na(x) && nchar(x) <= limit
    }
    stopifnot(short_string(name, 64L), grepl("^[A-Za-z][A-Za-z0-9_]*$", name))
    numeric_scalar <- (is.integer(value) || is.double(value)) &&
        !is.object(value) && is.null(dim(value)) && length(value) == 1L && is.finite(value)
    stopifnot(numeric_scalar || short_string(value, 200L),
              is.null(unit) || short_string(unit, 40L),
              is.null(desc) || short_string(desc, 500L))
    record <- list(name = name, value = unname(value), unit = unit, desc = desc)
    payload <- as.character(jsonlite::toJSON(record, auto_unbox = TRUE, null = "null", digits = NA))
    data <- list("application/vnd.bioquorum.value+json" = payload,
                 "text/plain" = paste("bq_value", name, "=", as.character(value)))
    # IRdisplay publishes through the callback installed by IRkernel.
    publish <- getOption("jupyter.base_display_func")
    if (!is.function(publish)) stop("IRkernel display channel is unavailable")
    publish(data, NULL)
    invisible(value)
}

bq_summary <- function(x) {
    if (is.data.frame(x)) {
        preview <- utils::head(x[, seq_len(min(ncol(x), 8L)), drop = FALSE], 5L)
        return(list(class = class(x)[1L], rows = nrow(x), columns = ncol(x), head = preview))
    }
    if (inherits(x, "Seurat") || methods::is(x, "SummarizedExperiment")) {
        return(list(class = class(x)[1L], features = nrow(x), samples_or_cells = ncol(x)))
    }
    stop("Supply a data.frame, tibble, Seurat, SummarizedExperiment or DESeqDataSet")
}

# Coordinates in the saved image, relative to the top-left corner.
bq_figure_points <- function(path, sample_ids, x, y) {
    stopifnot(is.character(sample_ids), !anyNA(sample_ids), !anyDuplicated(sample_ids),
              all(nchar(sample_ids) >= 1L & nchar(sample_ids) <= 128L),
              length(x) == length(y), length(x) == length(sample_ids),
              all(is.finite(x) & x >= 0 & x <= 1), all(is.finite(y) & y >= 0 & y <= 1))
    utils::write.table(data.frame(sample_id=sample_ids, x=x, y=y),
                       paste0(path, ".points.tsv"), sep="\t", quote=TRUE,
                       row.names=FALSE, qmethod="double", fileEncoding="UTF-8")
}
