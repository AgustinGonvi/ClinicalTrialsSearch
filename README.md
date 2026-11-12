# ClinicalTrialsSearch

Reproducible ClinicalTrials.gov searches for research groups, with kidney examples and a renal adverse-event view. Define a question in a JSON profile, retrieve full registry records, document selection decisions, and export evidence with its source fields intact.

The search engine is general. Kidney examples provide a starting point; other conditions, interventions, and research questions use the same pipeline. Topic-specific drug lists and scope decisions belong in profiles.

## What it does

- Retrieves full ClinicalTrials.gov API v2 records through a native Python client or an optional `ctrdata` R adapter.
- Keeps queries, raw retrieval artifacts, selected records, decisions, and a run manifest together outside the source repository.
- Separates automatic rules from imported human review decisions.
- Exports all reported adverse-event rows and a heuristic renal subset, keeping arms, seriousness, denominators, assessments, and source locations separate. Each renal label records the rule and term text behind it.
- Summarizes every trial's design, phase, enrollment, conditions, interventions, sponsor, and dates in one table.
- Checks expected aliases and trial IDs, and compares records against an explicitly chosen previous run.
- Can retry an eligible failed native retrieval with `ctrdata` without changing the downstream evidence code.

Both adapters in this project use **ClinicalTrials.gov**. Switching clients does not add another registry or resolve an upstream registry outage. The optional adapter uses `ctrdata`'s structured records rather than its flattened analysis tables. See the [adapter contract](docs/adapters.md) and [ctrdata documentation](https://rfhb.github.io/ctrdata/).

## Install and try a small kidney search

Requires Python 3.11 or newer. The native runtime uses the Python standard library. From a checkout:

```sh
python -m pip install .
cts validate-profile profiles/kidney-small.json
cts run --profile profiles/kidney-small.json --name kidney-demo --backend native --output-root ../clinical-trials-output
```

The small profile retrieves two specific kidney studies. It exercises a record with posted results and a record without posted results; it is a bounded demonstration, not a kidney literature search. The source records are [NCT03036150](https://clinicaltrials.gov/study/NCT03036150) and [NCT00304148](https://clinicaltrials.gov/study/NCT00304148).

For a broader starting search:

```sh
cts run --profile profiles/kidney-ckd.json --name ckd --backend native --output-root ../clinical-trials-output
```

That profile searches the condition field for chronic kidney disease. A systematic search still needs an investigator-defined strategy, synonyms, eligibility criteria, and coverage checks. The command retrieves several thousand full records. Check the size of any search first:

```sh
cts count profiles/kidney-ckd.json
```

`count` asks the registry how many studies each query matches and saves nothing. A run prints progress lines to standard error and one JSON summary to standard output; add `--quiet` to suppress the progress lines.

## Keep local settings outside Git

An external configuration file can hold your output directory and optional R installation settings:

```sh
cts configure --config ../clinical-trials-settings.json --output-root ../clinical-trials-output
cts doctor --config ../clinical-trials-settings.json
cts run --profile profiles/kidney-small.json --name kidney-demo --config ../clinical-trials-settings.json
```

Use `--config` or the `CTS_CONFIG` environment variable to select that file. For the output root, an explicit `--output-root` takes precedence over `CTS_OUTPUT_ROOT`, which takes precedence over the configuration file. No machine-specific directory is distributed in this repository.

Each search creates a dated run below `<output_root>/<search-name>/`. Run artifacts, databases, local settings, and downloaded records do not belong in Git. Public registry records can contain investigator or contact details; review any dataset separately before sharing it.

## Review and compare

Copy a profile to define a new question. Edit its queries and rules, validate it, then run it. Import explicit decisions with `--reviews review.csv`; see the [profile and review guide](docs/profiles.md).

```sh
cts run --profile profiles/kidney-small.json --name kidney-demo --output-root ../clinical-trials-output --baseline PREVIOUS_RUN_DIRECTORY
cts compare PREVIOUS_RUN_DIRECTORY CURRENT_RUN_DIRECTORY --output ../clinical-trials-comparison.json
```

See [outputs and interpretation](docs/outputs.md) for completion status, missing data, row meanings, and baseline limits. An adverse-event row is not proof of causality; rows cannot be summed into unique affected patients.

## Optional retrieval adapter

Install R and the packages described in [adapter setup](docs/adapters.md), then choose `--backend ctrdata` or `--backend auto`. `auto` starts with the native client and restarts the whole query plan on the alternate client only for eligible retrieval failures. A successful empty search, invalid query, source-version change, or invalid evidence is not a reason to switch clients.

## Development

```sh
python -m pip install -e .
python -m unittest discover -s tests
```

Tests use synthetic records and mocked retrieval. Live checks require an explicit run and write outside the repository. Continuous integration runs the offline suite on Windows and Linux with Python 3.11 through 3.14.

See [architecture](docs/architecture.md), [contributing](CONTRIBUTING.md), and the [MIT license](LICENSE). There is no web interface, scheduler, or multi-registry integration in this version.

The initial [validation record](docs/validation.md) documents the offline suite and bounded live native/ctrdata comparison, including its limits.
