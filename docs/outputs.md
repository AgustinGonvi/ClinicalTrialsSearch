# Outputs and interpretation

Every run has its own directory beneath `<output_root>/<search-name>/`. Processing starts in a `.partial` directory; a successful run is renamed to its final timestamped directory. A processing error or a keyboard interrupt retains the `.partial` directory and a manifest with status `failed`; a process that is killed outright may leave status `running`. A successful manifest has status `complete`. Inspect that status before using derived tables, particularly after a command exits with an error.

| Artifact | Purpose |
| --- | --- |
| `manifest.json` | Run status, retrieval details, counts, coverage warnings, and file hashes |
| `profile.json` | The profile used for the run |
| `queries.json`, `query_ids.json` | The compiled query plan and its per-query matching IDs |
| `reviews.json` | Imported review decisions and reasons, if provided |
| `records/*.json` | Full retrieved study records |
| Retrieval attempt directories | Queries, raw pages, and backend diagnostics, including failed attempts |
| `evidence.json` | Structured analysis with explicit JSON null values, one table row per line |
| `decisions.csv` | Inclusion, exclusion, or review status and its reason |
| `trials.csv` | Trial design, sponsor, dates, and AE availability |
| `adverse_events.csv` | Source AE rows for included and review candidates |
| `renal_events.csv` | Rows assigned a renal or urinary category |
| `citations.ris` | Registry references |
| `report.md` | Run summary and interpretation notes |
| `changes.csv`, `changes.json` | Differences when an explicit baseline was supplied |

JSON, Markdown, and RIS artifacts use LF line endings on every operating system, so the same profile, query plan, or review file has the same hash wherever the run happened. CSV files keep the CRLF row endings of the CSV convention on every system.

## Trial table

`trials.csv` has one row per retrieved record. Beside the title, status, and AE availability it carries the study type, phases, enrollment count and whether that count is actual or estimated, conditions, intervention names, lead sponsor and sponsor class, and the start, primary completion, completion, first-posted, results-first-posted, and last-update dates. Lists are JSON arrays in one cell.

Each value is copied from the registry record. A field the record lacks stays blank, and a reported zero stays zero. Dates keep the registry's precision: a month-only date is not padded to a day.

`citations.ris` gives each included or review record its registration year and date when the record has one, so a reference manager can sort and cite it.

## What a row means

An adverse-event row represents a source event's statistics for one reported group. Serious and nonserious events remain separate. The export retains the group identifier, arm description, term, organ system, affected count, at-risk count, event count, assessment information, reporting threshold, timeframe, and source path when supplied. The `classification`, `classification_basis`, and `classification_match` columns hold the renal screening label described below.

`nonserious_frequency_threshold` is the source module's threshold for reporting Other/Nonserious AEs. It is repeated as module context on both kinds of row; it does not impose a threshold on serious events. The scope is shown in the registry's [Other Adverse Events template](https://cdn.clinicaltrials.gov/documents/results_table_layout/DataEntryTable_FreqAEForm.pdf).

Missing values remain missing. Zero means the registry reported zero. A record without posted results is different from one with results but no AE module, and both differ from one with no matching renal terms. Neither an absent row nor an absent module demonstrates a normal renal measurement.

Counts may describe participants or events in different source fields. Do not sum event rows to estimate unique affected participants: the same participant can contribute to several rows. Preserve the denominator and group definition when comparing arms. Reported AEs alone do not establish treatment causality.

The registry's [results data structure](https://clinicaltrials.gov/data-api/about-api/study-data-structure) is the source of the underlying fields. This toolkit does not replace the full record, study protocol, or publication when interpreting results.

## Renal classification

The renal view uses deterministic term and organ-system rules. Every event row carries three columns. `classification` is the category. `classification_basis` says whether the label rests on the `term`, on the reported `organ_system`, or on both (`term_and_organ_system`). `classification_match` is the part of the term that matched. Sort by those columns to audit the screen.

The rules run in this order, and the first match decides:

| Category | Rule |
| --- | --- |
| `kidney_laboratory` | Creatinine, blood urea or BUN, glomerular filtration or GFR, cystatin, and kidney function tests |
| `kidney_diagnosis` | A named kidney disease or injury: nephritis other than pyelonephritis, nephropathy, nephrotic syndrome, nephrosis, glomerulosclerosis, acute or chronic kidney disease, kidney failure, impairment, insufficiency, or injury, end-stage kidney disease, tubular necrosis or acidosis, uraemia, azotaemia, hepatorenal and cardiorenal syndromes, and the abbreviations AKI, CKD, and ESRD |
| `urinary_marker` | Protein, albumin, blood, or white cells in urine, including proteinuria, albuminuria, haematuria, pyuria, and leukocyturia |
| `urinary_infection` | A term that names the kidney or urinary tract, or a urinary infection such as cystitis, pyelonephritis, or urosepsis, reported under `Infections and infestations` |
| `kidney_replacement_therapy` | Dialysis, haemofiltration, or a kidney transplant, including their complications |
| `other_renal_or_urinary` | Any other row reported under `Renal and urinary disorders`, or whose term names the kidney or urinary tract under another organ system |

The last rule finds kidney and urinary events that sponsors report elsewhere: kidney and bladder cancers under neoplasms, kidney biopsy under investigations, nephrectomy under procedures, perirenal haematoma under injuries, and anaemia of kidney disease under blood disorders. Filter on `organ_system` or `classification_basis` to separate them.

A urinary-tract term is labeled an infection only when the sponsor reported it under `Infections and infestations`. Pyelonephritis or a urinary tract infection reported under another or a missing organ system is `other_renal_or_urinary`. Other nephritis terms, including glomerulonephritis and tubulointerstitial nephritis, map to `kidney_diagnosis`.

`kidney_replacement_therapy` marks rows that mention dialysis or a kidney transplant. A dialysis complication in a dialysis population lands here too, so the category alone does not show that a participant started dialysis.

Guards keep look-alike words out: adrenal, suprarenal, and infrarenal terms; gallbladder terms, cholecystitis, and dacryocystitis; epinephrine and metanephrine; creatine kinase, including the misspelling "creatinine phosphokinase"; retinal dialysis and iridodialysis; and EGFR when nothing marks it as a filtration estimate. Urine named only as a specimen, as in a drug screen or a hormone assay, is not a renal term.

The whole-word collision guard from the first release remains: a term containing `adrenal` or `gallbladder` receives no renal category, even under a renal organ system. This can miss composite terms that also describe a kidney event; inspect the full AE table when those terms are relevant.

This is a screening aid rather than a validated clinical adjudication. Urinary infections are not equivalent to nephrotoxicity; laboratory terms are not equivalent to a diagnosis; a kidney tumor is not evidence of drug toxicity; terms outside the dictionary may be missed. The rules changed in version 0.2, so compare derived renal tables only between runs that share a software fingerprint. The full AE export and raw record remain available for review.

## Comparison

Use `--baseline` on a run or `cts compare` to compare two completed snapshots. The standalone compare command writes a JSON report; a run with a baseline writes both CSV and JSON changes. Differences are reported per trial, including added or removed records and changed source fields. Changes in AE assessment, arm data, and counts matter even when the event term is unchanged.

A trial disappearing from a query does not prove that the trial was deleted or stopped. The query, indexing, eligibility rules, or registry record may have changed. Compare profiles and registry metadata before interpreting a difference as a real-world development.

The baseline is explicit. The toolkit does not infer the correct comparison period or silently choose the most recent folder.

Comparison covers all retrieved source records, including candidates excluded from the evidence tables. Separate flags identify changes in the profile, query plan, review decisions, and software fingerprint. Changed rules, reviews, or code can alter derived tables even when source records are unchanged. The profile, query-plan, and review flags compare content: when the stored hashes differ, the saved files are parsed and compared. Releases before 0.2 wrote platform line endings, so one profile hashed differently on Windows and Linux; such snapshots are still recognized as the same inputs. A saved file that no longer matches its own manifest hash is reported as different. These flags do not provide a row-by-row review history; inspect `decisions.csv` and `reviews.json` when reviewing eligibility changes.

## Reproduction and sharing

Retain the complete run directory, its profile, software version, and chosen baseline. Hashes help detect changes to saved files; they do not prove the validity of a clinical interpretation. Re-running a live query may return a different snapshot because registry content changes.

Keep local paths, raw downloaded records, review files, and databases out of the code repository. A source-code license does not replace the source registry's terms for its data. Before sharing a dataset, review its contents and applicable registry requirements separately.
