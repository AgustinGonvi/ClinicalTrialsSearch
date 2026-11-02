# Profiles and review decisions

A profile is a versioned JSON description of one investigation. Validate it before retrieval:

```sh
cts validate-profile profiles/kidney-small.json
```

Start from `profiles/template.json`. Keep machine configuration in an external settings file; the profile travels with the scientific question.

## Profile fields

| Field | Meaning |
| --- | --- |
| `schema_version` | Profile format version; currently `1` |
| `name`, `description` | Portable identifier and description of the investigation |
| `queries` | Named queries; the candidate set is their union |
| `selection` | Rules for handling retrieved candidates |
| `expected` | Coverage checks and explicitly external agents |

Each query contains a unique `name` and a `params` object. Supported query keys are `query.cond`, `query.term`, `query.intr`, `query.spons`, and `filter.ids`. Values are strings. These use the [ClinicalTrials.gov API v2 query model](https://clinicaltrials.gov/data-api/api). Multiple fields within one query are submitted together; separate queries are unioned after retrieval.

`filter.ids` limits a native query to a comma-separated list of NCT IDs. The current R adapter does not support that parameter. For a fixed cohort that works through both adapters, use an exact identifier expression in `query.term`, as in the small kidney profile:

```json
{"query.term": "AREA[NCTId](NCT00304148 OR NCT03036150)"}
```

A fixed identifier query does not discover new trials.

## Selection rules

`selection` contains:

- `include_ids`: explicit inclusions; these also generate an exact identifier query to retrieve those records.
- `exclude_ids`: explicit exclusions for retrieved records.
- `aliases`: intervention names or codes that qualify a candidate. Matching checks individual intervention names and other names, tolerates separators, and avoids matching a code inside a longer code.
- `exclude_aliases`: intervention codes used for scope exclusions. A positive qualifying alias takes precedence over this adjacent-agent rule.
- `phrases`: literal, case-insensitive phrases checked in titles, summaries, and intervention names. Matches do not join text across separate fields. A phrase match sends a candidate to review unless another rule establishes its decision.
- `default`: `include` or `review` for candidates without another rule.

Automatic inclusion is an implementation of the profile's rules; it is not a claim that a person reviewed each trial. The exported decision records that distinction. Use `review` for uncertain scope. Review candidates retain their AE rows with their decision attached, so unresolved evidence is visible.

Explicit exclusions take precedence among automatic rules. Imported review decisions can override automatic rules and must give a reason.

## Coverage checks

Coverage checks use the union of `selection.aliases` and `expected.aliases`, so a selection alias that stops matching cannot disappear quietly. An unmatched alias produces a warning; it does not prove the agent has no trial anywhere.

`expected.aliases` adds agents to the discovery query and coverage watchlist without making a matching intervention an automatic inclusion rule. Only included discovery records count as covered. An agent found solely in unresolved or excluded candidates therefore still warns and needs scope review.

`expected.trial_ids` checks that specified records are present. A missing expected trial should prompt inspection of the query, source, and scope decision.

`expected.external_agents` records agents that are known to require another source. These are kept separate from unexpected missing aliases, so routine limitations do not produce misleading failure warnings.

The run controller adds an intervention alias query from the union of `selection.aliases` and `expected.aliases`, and an exact identifier query for `selection.include_ids`. These generated queries are recorded alongside the submitted plan. The names `profile-aliases` and `profile-include-ids` are reserved for them.

`expected.trial_ids`, `exclude_ids`, phrases, and external-agent labels do not broaden retrieval. Put other necessary discovery terms in `queries`.

Generated alias and inclusion-ID queries currently use a single URL each. Large lists can exceed a client or server URL limit; automatic batching is not implemented. Split a large fixed cohort into several explicit named identifier queries rather than placing thousands of IDs in `include_ids`. Keep the selection default appropriate for those queries. A length error fails retrieval visibly; the tool does not silently truncate the list.

## Importing human review

Create a CSV with this header:

```csv
NCTId,decision,reason
NCT00000001,include,Meets the prespecified eligibility criteria
NCT00000002,exclude,Outside the prespecified population
```

The example IDs are synthetic. Decisions are `include`, `exclude`, or `review`; a nonempty reason is required. Reviewer names are not required. Keep review files with local investigation material, outside this code repository.

```sh
cts run --profile profiles/kidney-small.json --name kidney-reviewed --reviews review.csv --output-root ../clinical-trials-output
```

The review file applies to the new run. It is not an interactive review system or a shared audit database. Keep previous runs when changing eligibility rules so the earlier decisions remain inspectable.
