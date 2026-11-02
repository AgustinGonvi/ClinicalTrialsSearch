# Retrieval adapters

The default native adapter uses the Python standard library to call ClinicalTrials.gov API v2. The optional adapter uses R package `ctrdata` against the same registry. Both return full registry records for the same submitted query plan.

## Install the optional adapter

Install a current R version supported by `ctrdata`. In R:

```r
install.packages(c("ctrdata", "nodbi", "RSQLite", "DBI", "jsonlite", "httr2"))
```

Use a dedicated R library if needed and record its location in the external settings file using `r_library`. Set `rscript` when `Rscript` is not on your command search path:

```sh
cts configure --config ../clinical-trials-r-settings.json --output-root ../clinical-trials-output --rscript Rscript
cts doctor --config ../clinical-trials-r-settings.json
cts run --profile profiles/kidney-small.json --name kidney-r --backend ctrdata --config ../clinical-trials-r-settings.json
```

`configure` creates a new file and refuses to overwrite an existing one. To add R settings to an existing configuration, edit that external JSON file explicitly. Relative paths inside a configuration file resolve against the file's directory.

The bridge targets `ctrdata` 1.26.3 or newer. Its SQLite database, downloaded artifacts, and logs are run data, not repository content. See [ctrdata installation](https://rfhb.github.io/ctrdata/) for platform-specific dependencies.

## Common contract

An adapter returns:

- Full source records keyed by NCT ID.
- The matching IDs for every named query, including successful empty queries.
- Source version information and backend metadata.
- Raw retrieval artifacts saved under its attempt directory.

Adapters validate NCT IDs, page/count consistency, conflicting duplicate records, and source-version stability. Returned records retain the ClinicalTrials.gov schema. Adapter-specific bookkeeping stays outside that schema.

The shared query parameters are `query.cond`, `query.term`, `query.intr`, and `query.spons`. Native retrieval additionally supports `filter.ids`; the current R bridge rejects that parameter explicitly. To keep a fixed cohort portable across both adapters, use an exact `AREA[NCTId](...)` expression in `query.term`.

The R bridge uses structured records exported from its database. It does not use `dbGetFieldsIntoDf()` as the interchange format, because the evidence layer needs original fields and nesting. `ctrdata` describes its record-preserving model in its [data model documentation](https://rfhb.github.io/ctrdata/#data-model-of-ctrdata).

## Backend choices

| Option | Behavior |
| --- | --- |
| `native` | Use the Python client; default |
| `ctrdata` | Use the optional R bridge |
| `auto` | Try native first; retry the whole plan with the R bridge only after an eligible client or transport failure |

Automatic fallback never combines a failed attempt's pages with the alternate attempt's records. A successful empty query is not a transport failure. Invalid queries, inconsistent evidence, source-version changes, and rate limiting do not justify switching clients to seek a different result. Bounded native retries handle eligible transient errors before fallback is considered.

Interrupted SSL reads are treated as transport errors; certificate-verification failures and local artifact-write failures do not trigger retries or fallback. Malformed R bridge output is recorded as a failed attempt, with its existing raw artifacts retained. The R bridge parses HTTP-date Retry-After headers in the C time locale and restores the previous locale afterward.

Both clients share the upstream source. Neither adds Chinese registrations, European safety notices, sponsor causality assessments, or other material absent from the submitted ClinicalTrials.gov records. Although `ctrdata` itself supports multiple registries, this integration supports only `CTGOV2`; [its broader registry support](https://rfhb.github.io/ctrdata/) should not be confused with this project's scope.

## Proving equivalence

For a new or upgraded adapter, run a bounded profile through both backends while the registry version is stable. Compare query ID sets and source record content, then compare derived evidence. Ignore only documented backend metadata; do not discard scientific fields to make the comparison pass.

Offline tests exercise contract failures. Live parity checks are deliberate validation runs and should not become repeated public-registry traffic in every test job.
