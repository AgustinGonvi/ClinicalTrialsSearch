# Contributing

Keep the core search engine independent of any one disease, intervention, or lab installation. Add a profile for a scientific topic; change a core module only when the behavior belongs in the reusable workflow.

Install the package in an isolated Python environment and run the offline tests:

```sh
python -m pip install -e .
python -m unittest discover -s tests
```

Include a synthetic regression case for changes to retrieval completeness, selection, missingness, or AE semantics. Test the failure that motivated the change. Preserve unknown values, original source records, separate arms, and separate serious/nonserious event categories.

Do not commit downloaded trial records, local configuration, credentials, databases, logs, reviewer details, or machine-specific paths. Fixtures should be synthetic and contain no personal data. Run outputs belong outside the checkout. Check the staged diff before committing; ignore rules are a guardrail, not a content audit.

After staging the intended files, run:

```sh
python tools/check_public_tree.py
```

This check examines tracked UTF-8 text for common local paths, email addresses, credential patterns, and accidental runtime artifacts. It reports filenames, line numbers, and rule names without printing matched values. It also rejects unreviewed binary files and symlinks. This is not general de-identification or proof that every secret or personal detail is absent; manual content review still matters.

Document behavior changes in the relevant guide. For adapter changes, state which offline checks passed and whether a live equivalence check was performed. Do not claim broad registry coverage from a successful single-query check.

Report the smallest reproducible problem you can share safely: package version, profile shape, expected behavior, actual behavior, and a synthetic fixture where possible. Remove identifying or local details from logs before attaching them.
