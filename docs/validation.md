# Validation record

Initial validation on 2026-11-02 used Python 3.12.14 on Windows, R 4.6.1, and ctrdata 1.26.3. All 66 offline regression tests passed. The tests use synthetic records and mocked failures, including pagination errors, changing registry versions, missing values, alias boundaries, human overrides, partial responses, and whole-plan fallback.

A live run of `profiles/kidney-small.json` through each backend returned the same two trials, NCT00304148 and NCT03036150. The API reported version `2.0.5` and data timestamp `2026-11-02T10:00:05` for both runs.

| Check | Result |
| --- | --- |
| Retrieved trial IDs | Identical; 2 trials |
| Complete source record content | Identical |
| Adverse-event export | Identical; 1,380 event-by-arm rows |
| Renal/urinary subset | Identical; 100 event-by-arm rows |
| Decisions, trial summaries, and query membership | Identical |
| Configured coverage warnings | None |

The R adapter also checked its SQLite JSON export against the original registry pages. Raw source records and local paths are kept outside this repository; the reproducible bounded profile is included.

These results establish equivalence for this small cohort and source version. They do not establish comprehensive kidney-trial coverage, clinical validity of the renal heuristic, successful recovery from a real upstream outage, or live multi-page equivalence. Pagination and automatic fallback were exercised with controlled failures in the offline suite. GitHub Actions runs that suite and the publication scan on Windows and Linux with Python 3.11, 3.12, and 3.13.

## Follow-up correctness review

On 2026-11-02, the expanded suite passed 78 Python tests after corrections to pyelonephritis classification, malformed R-output validation, SSL read retries, and ID lookup performance. The optional R regression passed under C, French, German, and Spanish locales, including restoration after a parsing error. The original code reproduced both the pyelonephritis misclassification and the `running` attempt manifest after a missing bridge field.

Repeating the same bounded live comparison produced identical source records and six evidence exports across both backends. Compared with the initial snapshot, four pyelonephritis rows changed from `kidney_diagnosis` to `urinary_infection`; their counts, arms, seriousness and source locations were unchanged. Both runs still contained 1,380 AE rows and 100 renal/urinary rows. The registry version and source records were unchanged, and both manifests correctly flagged the changed software fingerprint against the original baseline.

## Evaluation and corrections for version 0.2

On 2026-11-12 the published version 0.1.0 was evaluated on Windows with Python 3.14.7. Its 78 offline tests passed. Two live searches through the native backend then retrieved 736 unique studies carrying 232,171 adverse-event rows: 219 studies with posted results that matched diabetic nephropathy or dapagliflozin, and 542 phase 3 studies with posted results and a kidney condition. The API reported version `2.0.5` and data timestamp `2026-11-12T10:00:05` throughout.

The renal rules of both versions were applied to those rows. Every distinct term and organ-system pair whose label changed was listed and inspected during development. That inspection is not clinical adjudication.

| Check | Version 0.1 | Version 0.2 |
| --- | --- | --- |
| Rows in the renal view | 15,048 | 18,349 |
| Rows removed as false positives | | 94, across 5 term and organ-system pairs |
| Rows added | | 3,395, across 233 pairs |
| Rows moved to a more specific category | | 858, across 39 pairs |
| The 542-study search, wall time | 259 s | 38 s |
| The 542-study search, peak memory | 1.9 GiB | 0.7 GiB |

The removed rows were infective and emphysematous cholecystitis and dacryocystitis, which matched `cystitis` under the infection organ system, and creatine kinase misspelled as "creatinine phosphokinase". The added rows were kidney and urinary terms reported under other organ systems: kidney infection, renal abscess, pyonephrosis, urethritis, and bacteriuria under infections; dialysis and kidney transplant terms; protein, albumin, blood, and white cells in urine under investigations; uraemic complications and cardiorenal syndrome; and kidney and bladder neoplasms, procedures, and injuries. The moved rows were mainly end-stage renal disease, azotaemia, renal tubular necrosis, glomerulosclerosis, and renal insufficiency, which had been `other_renal_or_urinary`.

In `profiles/kidney-small.json` the adverse-event export is unchanged at 1,380 rows. Its renal view grew from 100 to 124 rows: 2 cholecystitis rows left, 26 rows entered, and 8 rows moved from `other_renal_or_urinary` to `kidney_diagnosis`.

The second run of the 542-study search returned source records identical to the first. Its wall time includes verifying the first run as a baseline. Transfer speed varies between runs, so the isolated measurement is the more reliable figure: one page of 100 full records was 9.2 MB and took 3.8 s uncompressed, and 1.2 MB and 0.9 s with gzip.

A 0.1 snapshot written on Windows and a 0.2 snapshot of the same profile carry different profile hashes, because 0.1 wrote CRLF line endings there. The comparison reported them as the same profile, query plan, and reviews, and flagged the changed software.

The offline suite now has 102 tests, which passed on Windows with Python 3.14.7. The R adapter was not run live in this evaluation; its offline contract tests pass unchanged. A count preview of `profiles/kidney-ckd.json` reported 5,863 studies. That profile was not retrieved in full, so time and memory for a search of that size remain unmeasured.
