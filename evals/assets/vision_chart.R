# Deterministic QC-style chart for the v0.5 remote vision check (evals/v05_remote_vision_eval.py).
# Values are not printed on the figure, so answering requires reading the bars and the threshold.
args <- commandArgs(trailingOnly = TRUE)
out <- if (length(args)) args[[1]] else "vision_chart.png"
samples <- c("D1-ctrl", "D1-stim", "D2-ctrl", "D2-stim", "D3-ctrl", "D3-stim")
retained <- c(91, 86, 62, 88, 79, 94)
png(out, width = 900, height = 600, res = 110, type = "cairo")
par(mar = c(5, 5, 4, 2))
bars <- barplot(retained, names.arg = samples, ylim = c(0, 100), col = "grey70",
                border = NA, ylab = "Cells retained after QC (%)", las = 1,
                main = "Synthetic QC: retained cells per library")
abline(h = 80, lty = 2, lwd = 2, col = "firebrick")
text(max(bars) + 0.4, 82.5, "80% threshold", col = "firebrick", adj = 1, cex = 0.8)
abline(h = seq(0, 100, 10), col = "grey90", lwd = 0.5)
invisible(dev.off())
