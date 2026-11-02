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
