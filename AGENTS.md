# Working on ClinicalTrialsSearch

This repository contains a reusable ClinicalTrials.gov search and evidence pipeline. Keep topic-specific discovery terms, aliases, exclusions, and expected coverage in profiles. Kidney examples are demonstrations, not a restriction on the engine.

## Preserve the evidence contract

- Retain full source records and distinguish backend metadata from registry fields.
- Keep serious and nonserious AEs, study arms, denominators, assessment information, and missing values separate.
- Do not coerce an absent count to zero or sum event rows into unique affected participants.
- Preserve uncertainty and review decisions. Automatic rules are not human adjudication.
- Treat renal classification as a transparent screening heuristic. Keep the full AE export available.
- Fail visibly on incomplete retrieval or invalid evidence. Do not use fallback to conceal a scientific validation failure.

## Keep retrieval interchangeable

Adapters must submit the same scientific query and return the common full-record contract. Preserve raw attempt artifacts, including unsuccessful attempts. A fallback restarts the complete query plan; it must not mix pages from separate attempts. A successful empty result is valid.

Both current adapters use ClinicalTrials.gov. Do not describe another client as broader registry coverage. A new registry needs an explicit source model, mapping, and validation.

## Keep the repository portable

Write search outputs, downloaded records, databases, temporary work, and local settings outside the checkout. Use explicit output options or external configuration. Never hardcode a workstation path, username, credential, or private research detail in source, examples, tests, logs, or documentation.

Use synthetic fixtures without personal data. Do not copy live study records into tests. Review the staged file list and diff for generated artifacts and local details before committing; ignore rules do not replace that review. Run `python tools/check_public_tree.py` after staging files to catch common publication leaks. It does not replace review for personal details that a pattern cannot identify.

## Validate relevant behavior

Install the package and run the offline suite:

```sh
python -m pip install -e .
python -m unittest discover -s tests
```

Validate any changed profiles with `cts validate-profile`. Add regression tests for material changes to query completeness, scope rules, missingness, source preservation, or comparison semantics. Keep ordinary tests offline; use a bounded explicit profile for a live adapter check.

When changing an adapter, verify query membership and source record equality before comparing derived exports. Record the registry version and distinguish mocked tests from live validation. Update the relevant guide when command behavior or output semantics change.
