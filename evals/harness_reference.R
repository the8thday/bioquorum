# Export the official datasets and an independent reference answer for each task.
args <- commandArgs(trailingOnly = TRUE)
stopifnot(length(args) == 1L)
out <- args[[1]]
dir.create(out, recursive = TRUE, showWarnings = FALSE)
for (name in c("PlantGrowth", "ToothGrowth", "CO2", "ChickWeight", "sleep")) {
  write.csv(as.data.frame(get(name, asNamespace("datasets"))),
            file.path(out, paste0(name, ".csv")), row.names = FALSE, na = "")
}

plant <- with(PlantGrowth, tapply(weight, group, mean))
tooth <- with(ToothGrowth, tapply(len, list(supp, dose), mean))
co2 <- reshape(subset(as.data.frame(CO2), Type == "Quebec" & conc %in% c(175, 1000)),
               idvar = c("Plant", "Treatment"), timevar = "conc", direction = "wide")
co2$change <- co2$uptake.1000 - co2$uptake.175
co2means <- with(co2, tapply(change, Treatment, mean))
chicks <- reshape(subset(as.data.frame(ChickWeight), Diet %in% c(1, 4) & Time %in% c(0, 21)),
                  idvar = c("Chick", "Diet"), timevar = "Time", direction = "wide")
chicks$gain <- chicks$weight.21 - chicks$weight.0
gains <- with(subset(chicks, !is.na(gain)), tapply(gain, Diet, mean))
chick_counts <- with(subset(chicks, !is.na(gain)), table(Diet))
sleep_wide <- reshape(sleep, idvar = "ID", timevar = "group", direction = "wide")
sleep_diffs <- with(sleep_wide, extra.2 - extra.1)

reference <- list(
  "plant-growth" = c("Control mean" = plant[["ctrl"]],
                     "Treatment 2 minus control" = plant[["trt2"]] - plant[["ctrl"]]),
  "tooth-growth" = setNames(tooth["OJ",] - tooth["VC",],
                           c("Low dose difference", "Middle dose difference", "High dose difference")),
  "grass-co2" = c("Chilled mean change" = round(co2means[["chilled"]], 2),
                 "Nonchilled mean change" = round(co2means[["nonchilled"]], 2),
                 "Paired plants" = sum(!is.na(co2$change))),
  "chick-growth" = c("Diet 1 pairs" = chick_counts[["1"]], "Diet 4 pairs" = chick_counts[["4"]],
                    "Diet 1 mean gain" = round(gains[["1"]], 2),
                    "Diet 4 mean gain" = round(gains[["4"]], 2)),
  "paired-sleep" = c("Complete pairs" = sum(!is.na(sleep_diffs)), "Mean difference" = mean(sleep_diffs),
                     "Positive differences" = sum(sleep_diffs > 0))
)
rows <- do.call(rbind, lapply(names(reference), function(id) {
  data.frame(id = id, metric = names(reference[[id]]), value = unname(reference[[id]]))
}))
write.table(rows, file.path(out, "reference.tsv"), sep = "\t", row.names = FALSE, quote = FALSE)
writeLines(R.version.string, file.path(out, "r-version.txt"))
