# ctrdata >= 1.26.3 bridge. Called by retrieval.py, never installs dependencies.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2L) stop("Expected query-plan path and destination")
destination <- normalizePath(args[[2]], winslash = "/", mustWork = TRUE)

fail <- function(message, retryable = FALSE) {
  stop(structure(list(message = message, call = NULL, retryable = retryable),
                 class = c("retrieval_error", "error", "condition")))
}

parse_retry_after_date <- function(value) {
  previous_locale <- Sys.getlocale("LC_TIME")
  on.exit(Sys.setlocale("LC_TIME", previous_locale), add = TRUE)
  locale <- suppressWarnings(Sys.setlocale("LC_TIME", "C"))
  if (!nzchar(locale)) fail("Cannot set the HTTP-date locale for Retry-After")
  when <- as.POSIXct(value, format = "%a, %d %b %Y %H:%M:%S", tz = "UTC")
  if (is.na(when)) fail("Cannot honor registry Retry-After; retry later without switching backends")
  when
}

main <- function() {
  for (package in c("ctrdata", "jsonlite", "nodbi", "DBI", "RSQLite", "httr2")) {
    if (!requireNamespace(package, quietly = TRUE)) fail(paste("Missing R package:", package), TRUE)
  }
  if (utils::packageVersion("ctrdata") < "1.26.3") fail("ctrdata >= 1.26.3 is required", TRUE)
  if (.Platform$OS.type == "windows") {
    locale <- suppressWarnings(Sys.setlocale("LC_CTYPE", "English_United States.utf8"))
    if (!nzchar(locale)) fail("A UTF-8 locale is required for registry JSON")
  }
  options(timeout = 60)
  write_json <- function(value, path) {
    jsonlite::write_json(value, path, auto_unbox = TRUE, pretty = TRUE, null = "null", na = "null")
  }
  read_json <- function(path) jsonlite::fromJSON(path, simplifyVector = FALSE)
  plan <- read_json(args[[1]])
  api <- "https://clinicaltrials.gov/api/v2"
  fetch <- function(url, path, params = list()) {
    limited <- FALSE
    for (attempt in 0:3) {
      req <- httr2::request(url)
      if (length(params)) req <- do.call(httr2::req_url_query, c(list(req), params))
      req <- httr2::req_timeout(req, 60)
      req <- httr2::req_error(req, is_error = function(resp) FALSE)
      response <- tryCatch(httr2::req_perform(req), error = function(e) e)
      if (inherits(response, "error")) {
        write_json(list(message = conditionMessage(response), attempt = attempt + 1L),
                   paste0(path, ".attempt", attempt + 1L, ".error.json"))
        if (attempt == 3L) fail(conditionMessage(response), !limited)
        Sys.sleep(min(8, 2^attempt))
        next
      }
      status <- httr2::resp_status(response)
      body <- httr2::resp_body_raw(response)
      if (status >= 200L && status < 300L) {
        writeBin(body, path)
        return(read_json(path))
      }
      writeBin(body, paste0(path, ".attempt", attempt + 1L, ".body"))
      limited <- limited || status == 429L
      retry <- status == 429L || status >= 500L
      if (!retry || attempt == 3L) fail(paste("Registry HTTP", status), retry && !limited)
      retry_after <- httr2::resp_header(response, "Retry-After")
      delay <- suppressWarnings(as.numeric(retry_after))
      if (length(delay) != 1L || is.na(delay)) {
        if (length(retry_after) == 1L && nzchar(retry_after)) {
          when <- parse_retry_after_date(retry_after)
          delay <- as.numeric(difftime(when, Sys.time(), units = "secs"))
        } else {
          delay <- 2^attempt
        }
      }
      if (delay > 30) fail("Registry Retry-After exceeds the 30-second bounded wait; retry later without switching backends")
      Sys.sleep(max(0, delay))
    }
  }
  before <- fetch(paste0(api, "/version"), file.path(destination, "version_before.json"))
  manifest <- list(ctrdata_version = as.character(utils::packageVersion("ctrdata")),
                   r_version = R.version.string, source_version_before = before, queries = list())
  save_manifest <- function() write_json(manifest, file.path(destination, "bridge_manifest.json"))
  save_manifest()
  dbfile <- file.path(destination, "registry.sqlite")
  for (query in plan$queries) {
    raw_relative <- file.path("raw", query$name)
    raw_dir <- file.path(destination, raw_relative)
    dir.create(raw_dir, recursive = TRUE, showWarnings = FALSE)
    count_relative <- file.path(raw_relative, "count_response.json")
    count <- fetch(paste0(api, "/studies"), file.path(destination, count_relative),
                   c(query$params, list(pageSize = 1L, countTotal = "true", format = "json")))
    expected <- count$totalCount
    if (length(expected) != 1L || !is.numeric(expected) || expected < 0 || expected != floor(expected)) {
      fail("Count response lacks a valid totalCount")
    }
    export_relative <- paste0("records_", query$name, ".json")
    if (expected == 0L) {
      writeLines("[]", file.path(destination, export_relative), useBytes = TRUE)
      manifest$queries[[length(manifest$queries) + 1L]] <- list(
        name = query$name, queryterm = query$queryterm, count_file = count_relative,
        records_file = export_relative, raw_pages = list(), total_count = 0L)
      save_manifest()
      next
    }
    options(ctrdata.tempdir = raw_dir)
    dbc <- nodbi::src_sqlite(dbname = dbfile, collection = paste0("query_", query$name))
    exported <- tryCatch({
      result <- ctrdata::ctrLoadQueryIntoDb(queryterm = query$queryterm, register = "CTGOV2",
                                           con = dbc, ctgov2history = FALSE,
                                           documents.path = NULL, verbose = TRUE)
      saveRDS(result, file.path(destination, paste0("load_result_", query$name, ".rds")))
      if (is.null(result$n) || result$n != expected || length(result$failed)) {
        fail("ctrdata import count/failure list disagrees with the registry count")
      }
      table <- DBI::dbQuoteIdentifier(dbc$con, dbc$collection)
      sql <- paste0('SELECT "_id", json("json") AS "json" FROM ', table,
                    ' WHERE "_id" <> ? ORDER BY "_id"')
      stored <- DBI::dbGetQuery(dbc$con, sql, params = list("meta-info"))
      if (nrow(stored) != expected) fail("SQLite record count disagrees with registry count")
      history <- ctrdata::dbQueryHistory(con = dbc)
      saveRDS(history, file.path(destination, paste0("query_history_", query$name, ".rds")))
      stored$json
    }, finally = DBI::dbDisconnect(dbc$con))
    # Export SQLite JSON text directly; parsing into data frames corrupts arrays.
    writeLines(enc2utf8(c("[", paste(exported, collapse = ",\n"), "]")),
               file.path(destination, export_relative), useBytes = TRUE)
    pages <- list.files(raw_dir, pattern = "^ctgov_trials_.*_[0-9]+[.]json$", full.names = FALSE)
    page_numbers <- as.integer(sub(".*_([0-9]+)[.]json$", "\\1", pages))
    pages <- pages[order(page_numbers)]
    manifest$queries[[length(manifest$queries) + 1L]] <- list(
      name = query$name, queryterm = query$queryterm, count_file = count_relative,
      records_file = export_relative, raw_pages = as.list(file.path(raw_relative, pages)),
      total_count = expected)
    save_manifest()
  }
  after <- fetch(paste0(api, "/version"), file.path(destination, "version_after.json"))
  manifest$source_version_after <- after
  save_manifest()
  writeLines(capture.output(sessionInfo()), file.path(destination, "session_info.txt"))
  if (!identical(before, after)) fail("Registry version changed during ctrdata retrieval")
}

tryCatch(main(), error = function(error) {
  message(conditionMessage(error))
  retryable <- isTRUE(error$retryable)
  if (grepl("429|Too Many Requests", conditionMessage(error), ignore.case = TRUE)) retryable <- FALSE
  if (requireNamespace("jsonlite", quietly = TRUE)) {
    jsonlite::write_json(list(message = conditionMessage(error), retryable = retryable),
                         file.path(destination, "bridge_error.json"), auto_unbox = TRUE, pretty = TRUE)
  }
  quit(save = "no", status = 1L)
})
