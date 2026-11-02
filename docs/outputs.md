# Outputs and interpretation

Every run has its own directory beneath `<output_root>/<search-name>/`. Processing starts in a `.partial` directory; a successful run is renamed to its final timestamped directory. A processing error retains the `.partial` directory and a manifest with status `failed`; an interrupted process may leave status `running`. A successful manifest has status `complete`. Inspect that status before using derived tables, particularly after a command exits with an error.

| Artifact | Purpose |
| --- | --- |
| `manifest.json` | Run status, retrieval details, counts, coverage warnings, and file hashes |
| `profile.json` | The profile used for the run |
| `queries.json`, `query_ids.json` | The compiled query plan and its per-query matching IDs |
| `reviews.json` | Imported review decisions and reasons, if provided |
| `records/*.json` | Full retrieved study records |
| Retrieval attempt directories | Queries, raw pages, and backend diagnostics, including failed attempts |
| `evidence.json` | Structured analysis with explicit JSON null values |
| `decisions.csv` | Inclusion, exclusion, or review status and its reason |
| `trials.csv` | Trial-level metadata and AE availability |
| `adverse_events.csv` | Source AE rows for included and review candidates |
| `renal_events.csv` | Rows assigned a renal or urinary category |
| `citations.ris` | Registry references |
| `report.md` | Run summary and interpretation notes |
| `changes.csv`, `changes.json` | Differences when an explicit baseline was supplied |

## What a row means

An adverse-event row represents a source event's statistics for one reported group. Serious and nonserious events remain separate. The export retains the group identifier, arm description, term, organ system, affected count, at-risk count, event count, assessment information, reporting threshold, timeframe, and source path when supplied.

`nonserious_frequency_threshold` is the source module's threshold for reporting Other/Nonserious AEs. It is repeated as module context on both kinds of row; it does not impose a threshold on serious events. The scope is shown in the registry's [Other Adverse Events template](https://cdn.clinicaltrials.gov/documents/results_table_layout/DataEntryTable_FreqAEForm.pdf).

Missing values remain missing. Zero means the registry reported zero. A record without posted results is different from one with results but no AE module, and both differ from one with no matching renal terms. Neither an absent row nor an absent module demonstrates a normal renal measurement.

Counts may describe participants or events in different source fields. Do not sum event rows to estimate unique affected participants: the same participant can contribute to several rows. Preserve the denominator and group definition when comparing arms. Reported AEs alone do not establish treatment causality.

The registry's [results data structure](https://clinicaltrials.gov/data-api/about-api/study-data-structure) is the source of the underlying fields. This toolkit does not replace the full record, study protocol, or publication when interpreting results.

## Renal classification

The renal view uses deterministic term and organ-system rules. Its categories distinguish kidney diagnoses, kidney laboratory terms, urinary markers, urinary infections, and other renal or urinary entries. Matching avoids common adrenal and gallbladder false positives.

This is a screening aid rather than a validated clinical adjudication. Urinary infections are not equivalent to nephrotoxicity; laboratory terms are not equivalent to a diagnosis; terms outside the dictionary may be missed. The full AE export and raw record remain available for review.

## Comparison

Use `--baseline` on a run or `cts compare` to compare two completed snapshots. The standalone compare command writes a JSON report; a run with a baseline writes both CSV and JSON changes. Differences are reported per trial, including added or removed records and changed source fields. Changes in AE assessment, arm data, and counts matter even when the event term is unchanged.

A trial disappearing from a query does not prove that the trial was deleted or stopped. The query, indexing, eligibility rules, or registry record may have changed. Compare profiles and registry metadata before interpreting a difference as a real-world development.

The baseline is explicit. The toolkit does not infer the correct comparison period or silently choose the most recent folder.

Comparison covers all retrieved source records, including candidates excluded from the evidence tables. Separate flags identify changes in the profile, query plan, review decisions, and software fingerprint. Changed rules, reviews, or code can alter derived tables even when source records are unchanged. These flags do not provide a row-by-row review history; inspect `decisions.csv` and `reviews.json` when reviewing eligibility changes.

## Reproduction and sharing

Retain the complete run directory, its profile, software version, and chosen baseline. Hashes help detect changes to saved files; they do not prove the validity of a clinical interpretation. Re-running a live query may return a different snapshot because registry content changes.

Keep local paths, raw downloaded records, review files, and databases out of the code repository. A source-code license does not replace the source registry's terms for its data. Before sharing a dataset, review its contents and applicable registry requirements separately.
