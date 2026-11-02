# Optional offline R regression: run from the checkout with Rscript --vanilla.
# Evaluate only the shipped parsing helpers; do not run the retrieval entry point.
expressions <- parse(file = "src/clinical_trials_search/r/ctrdata_fetch.R")
helpers <- new.env()
for (expression in expressions) {
  if (is.call(expression) && identical(expression[[1]], as.name("<-")) &&
      is.symbol(expression[[2]]) &&
      as.character(expression[[2]]) %in% c("fail", "parse_retry_after_date")) {
    eval(expression, envir = helpers)
  }
}

run_checks <- function() {
  original <- Sys.getlocale("LC_TIME")
  on.exit(Sys.setlocale("LC_TIME", original), add = TRUE)
  expected <- as.POSIXct("2026-11-02 12:00:00", format = "%Y-%m-%d %H:%M:%S", tz = "UTC")
  locales <- list(
    C = "C",
    French = c("fr_FR.UTF-8", "French_France.utf8"),
    German = c("de_DE.UTF-8", "German_Germany.utf8"),
    Spanish = c("es_ES.UTF-8", "Spanish_Spain.utf8")
  )
  for (label in names(locales)) {
    chosen <- ""
    for (candidate in locales[[label]]) {
      chosen <- suppressWarnings(Sys.setlocale("LC_TIME", candidate))
      if (nzchar(chosen)) break
    }
    if (!nzchar(chosen)) {
      if (label == "C") stop("The C locale must be available")
      cat("SKIP:", label, "locale is not installed\n")
      next
    }
    parsed <- helpers$parse_retry_after_date("Mon, 02 Nov 2026 12:00:00 GMT")
    stopifnot(identical(as.numeric(parsed), as.numeric(expected)))
    stopifnot(identical(Sys.getlocale("LC_TIME"), chosen))
    failure <- tryCatch(helpers$parse_retry_after_date("not an HTTP date"), error = identity)
    stopifnot(inherits(failure, "retrieval_error"), identical(failure$retryable, FALSE))
    stopifnot(identical(Sys.getlocale("LC_TIME"), chosen))
    cat("PASS:", label, "date parsing and locale restoration on success and failure\n")
  }
}
run_checks()
