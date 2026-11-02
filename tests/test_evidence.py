"""Offline evidence regressions using synthetic records only."""

import copy
import json
import unittest

from clinical_trials_search.evidence import (
    alias_matches,
    analyze,
    compare_snapshots,
    renal_category,
)


def profile(**selection):
    return {
        "schema_version": 1,
        "name": "synthetic",
        "description": "Offline test",
        "queries": [{"name": "search", "params": {"query.term": "example"}}],
        "selection": {
            "include_ids": [],
            "exclude_ids": [],
            "aliases": [],
            "exclude_aliases": [],
            "phrases": [],
            "default": "include",
            **selection,
        },
        "expected": {"aliases": [], "trial_ids": [], "external_agents": []},
    }


def study(nct="NCT00000001", names=("ABC123",), title="Synthetic trial", summary=""):
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct, "briefTitle": title},
            "descriptionModule": {"briefSummary": summary},
            "statusModule": {"overallStatus": "COMPLETED"},
            "armsInterventionsModule": {
                "interventions": [{"name": name} for name in names]
            },
        },
        "hasResults": False,
    }


def result_study():
    record = study()
    record["hasResults"] = True
    record["resultsSection"] = {
        "adverseEventsModule": {
            "eventGroups": [
                {"id": "E1", "title": "Experimental", "description": "Candidate agent"},
                {"id": "C1", "title": "Control", "description": "Comparator"},
            ],
            "frequencyThreshold": "5",
            "timeFrame": "12 weeks",
            "description": "Safety population",
            "seriousEvents": [
                {
                    "term": "Acute kidney injury",
                    "organSystem": "Renal and urinary disorders",
                    "assessmentType": "SYSTEMATIC_ASSESSMENT",
                    "notes": "Source note",
                    "stats": [
                        {
                            "groupId": "E1",
                            "numAffected": 1,
                            "numAtRisk": 8,
                            "numEvents": 2,
                        },
                        {
                            "groupId": "C1",
                            "numAffected": 0,
                            "numAtRisk": 7,
                            "numEvents": 0,
                        },
                    ],
                }
            ],
            "otherEvents": [
                {
                    "term": "Acute kidney injury",
                    "organSystem": "Renal and urinary disorders",
                    "stats": [{"groupId": "E1", "numAffected": 1, "numAtRisk": 8}],
                },
                {
                    "term": "Headache",
                    "organSystem": "Nervous system disorders",
                    "stats": [
                        {"groupId": "E1", "numAffected": None, "numAtRisk": None}
                    ],
                },
            ],
        }
    }
    return record


def run(record, config=None, reviews=None, discovered=True):
    nct = record["protocolSection"]["identificationModule"]["nctId"]
    return analyze(
        {nct: record},
        config or profile(),
        {"search": [nct] if discovered else []},
        reviews,
    )


class SelectionTests(unittest.TestCase):
    def test_query_membership_keeps_overlapping_queries_sorted_and_distinct(self):
        records = {"NCT00000001": study(), "NCT00000002": study("NCT00000002")}
        queries = {
            "zeta": ["NCT00000002", "NCT00000001"],
            "alpha": ["NCT00000001"],
            "empty": [],
        }
        output = analyze(records, profile(), queries)
        memberships = {
            row["NCTId"]: json.loads(row["query_names"]) for row in output["decisions"]
        }
        self.assertEqual(
            memberships, {"NCT00000001": ["alpha", "zeta"], "NCT00000002": ["zeta"]}
        )

    def test_default_general_search_includes_query_matches_without_human_claim(self):
        output = run(study())
        self.assertEqual(output["decisions"][0]["selection"], "include")
        self.assertFalse(output["decisions"][0]["reviewed_by_human"])
        self.assertEqual(json.loads(output["decisions"][0]["query_names"]), ["search"])

    def test_alias_boundaries_and_hyphen_variants(self):
        for text in ("abc-123 for injection", "ABC 123", "ABC123-SI"):
            with self.subTest(text=text):
                self.assertTrue(alias_matches(text, "ABC123"))
        for text in ("ABC1230", "XABC123", "ABC123A"):
            with self.subTest(text=text):
                self.assertFalse(alias_matches(text, "ABC123"))
                output = run(
                    study(names=[text]), profile(aliases=["ABC123"], default="review")
                )
                self.assertEqual(output["decisions"][0]["selection"], "review")

    def test_aliases_only_names_and_other_names_not_description_or_crossfields(self):
        record = study(names=["ABC", "123"], title="ABC123", summary="ABC123")
        intervention = record["protocolSection"]["armsInterventionsModule"][
            "interventions"
        ][0]
        intervention["description"] = "ABC123"
        config = profile(aliases=["ABC123"], default="review")
        self.assertEqual(run(record, config)["decisions"][0]["selection"], "review")
        intervention["otherNames"] = ["ABC-123"]
        self.assertEqual(run(record, config)["decisions"][0]["selection"], "include")

    def test_phrases_are_literal_bounded_and_do_not_cross_fields(self):
        config = profile(
            phrases=["kidney safety", "renal (phase 1)"], default="include"
        )
        for record in (
            study(summary="Kidney safety study"),
            study(summary="A renal (phase 1) study"),
        ):
            with self.subTest(record=record):
                self.assertEqual(
                    run(record, config)["decisions"][0]["selection"], "review"
                )
        for record in (
            study(title="kidney", summary="safety study"),
            study(summary="prekidney safety"),
            study(summary="renal phase 1"),
        ):
            with self.subTest(record=record):
                self.assertEqual(
                    run(record, config)["decisions"][0]["matched_phrases"], "[]"
                )

    def test_exclusion_and_combination_precedence(self):
        record = study(names=["ABC123", "XYZ456"])
        config = profile(aliases=["ABC123"], exclude_aliases=["XYZ456"])
        self.assertEqual(run(record, config)["decisions"][0]["selection"], "include")
        config["selection"]["exclude_ids"] = ["NCT00000001"]
        config["selection"]["include_ids"] = ["NCT00000001"]
        self.assertEqual(run(record, config)["decisions"][0]["selection"], "exclude")
        config["selection"].update(exclude_ids=[], include_ids=[])
        record["protocolSection"]["armsInterventionsModule"]["interventions"] = [
            {"name": "XYZ456"}
        ]
        self.assertEqual(run(record, config)["decisions"][0]["selection"], "exclude")

    def test_human_review_override_is_explicit_and_propagated_to_all_rows(self):
        record = result_study()
        config = profile(exclude_ids=["NCT00000001"])
        reviews = {
            "NCT00000001": {
                "decision": "include",
                "reason": "Eligibility verified against protocol",
            }
        }
        output = run(record, config, reviews)
        self.assertEqual(
            output["decisions"][0]["reason"], reviews["NCT00000001"]["reason"]
        )
        self.assertTrue(
            all(row["reviewed_by_human"] for row in output["adverse_events"])
        )
        self.assertTrue(output["trials"][0]["reviewed_by_human"])
        for override in (
            {"decision": "include", "reason": " "},
            {"decision": "maybe", "reason": "x"},
        ):
            with self.subTest(override=override), self.assertRaises(ValueError):
                run(record, config, {"NCT00000001": override})

    def test_review_candidates_keep_ae_evidence_and_warning(self):
        output = run(result_study(), profile(default="review"))
        self.assertEqual(len(output["adverse_events"]), 4)
        self.assertTrue(
            all(row["selection"] == "review" for row in output["adverse_events"])
        )
        self.assertTrue(any("unresolved" in warning for warning in output["warnings"]))

    def test_coverage_uses_only_included_discovery_and_external_absence_separate(self):
        config = profile(aliases=["ABC123"], exclude_ids=["NCT00000001"])
        config["expected"] = {
            "aliases": ["ABC123", "EXT789"],
            "trial_ids": ["NCT00000009"],
            "external_agents": ["EXT789"],
        }
        output = run(study(), config)
        self.assertEqual(output["alias_coverage"]["hits"]["ABC123"], [])
        self.assertEqual(output["alias_coverage"]["missing_expected"], ["ABC123"])
        self.assertEqual(
            output["alias_coverage"]["unmatched_external_agents"], ["EXT789"]
        )
        self.assertEqual(
            output["alias_coverage"]["missing_expected_trials"], ["NCT00000009"]
        )
        config["selection"]["exclude_ids"] = []
        self.assertEqual(
            run(study(), config, discovered=False)["alias_coverage"]["hits"]["ABC123"],
            [],
        )
        self.assertEqual(
            run(study(), config)["alias_coverage"]["hits"]["ABC123"], ["NCT00000001"]
        )

    def test_missing_payload_is_warned_without_fabricated_record(self):
        output = analyze(
            {}, profile(include_ids=["NCT00000009"]), {"search": ["NCT00000009"]}
        )
        self.assertEqual(output["trials"], [])
        self.assertTrue(
            any("no record payload" in warning for warning in output["warnings"])
        )

    def test_selection_aliases_warn_even_without_expected_watchlist(self):
        output = run(study(names=["Unrelated"]), profile(aliases=["ABC123"]))
        self.assertEqual(output["alias_coverage"]["missing_expected"], ["ABC123"])
        self.assertTrue(any("ABC123" in warning for warning in output["warnings"]))


class ExtractionTests(unittest.TestCase):
    def test_pyelonephritis_uses_infection_soc_without_capturing_other_nephritis(self):
        for term in ("Pyelonephritis", "Pyelonephritis acute", "PYELONEPHRITIS"):
            with self.subTest(term=term):
                self.assertEqual(
                    renal_category(term, "Infections and infestations"),
                    "urinary_infection",
                )
                self.assertEqual(
                    renal_category(term, "Renal and urinary disorders"),
                    "other_renal_or_urinary",
                )
                self.assertIsNone(renal_category(term, None))
        for term in ("Nephritis", "Glomerulonephritis", "Tubulointerstitial nephritis"):
            with self.subTest(term=term):
                self.assertEqual(
                    renal_category(term, "Renal and urinary disorders"),
                    "kidney_diagnosis",
                )

    def test_pyelonephritis_category_change_preserves_rows_counts_and_source(self):
        for soc, expected in (
            ("Infections and infestations", "urinary_infection"),
            ("Renal and urinary disorders", "other_renal_or_urinary"),
            (None, None),
        ):
            with self.subTest(soc=soc):
                record = result_study()
                event = record["resultsSection"]["adverseEventsModule"][
                    "seriousEvents"
                ][0]
                event.update(term="Pyelonephritis", organSystem=soc)
                before = copy.deepcopy(record)
                output = run(record)
                rows = [
                    row
                    for row in output["adverse_events"]
                    if row["term"] == "Pyelonephritis"
                ]
                self.assertEqual(len(rows), 2)
                self.assertTrue(all(row["classification"] == expected for row in rows))
                self.assertEqual(
                    [(row["num_affected"], row["num_at_risk"]) for row in rows],
                    [(1, 8), (0, 7)],
                )
                self.assertEqual(
                    sum(
                        row["term"] == "Pyelonephritis"
                        for row in output["renal_events"]
                    ),
                    2 if expected else 0,
                )
                self.assertEqual(record, before)

    def test_all_events_preserve_arms_seriousness_and_null_zero(self):
        output = run(result_study())
        self.assertEqual(len(output["adverse_events"]), 4)
        self.assertEqual(len(output["renal_events"]), 3)
        rows = output["adverse_events"]
        self.assertEqual(
            (rows[1]["arm"], rows[1]["num_affected"], rows[1]["num_at_risk"]),
            ("Control", 0, 7),
        )
        self.assertEqual(rows[0]["seriousness"], "serious")
        self.assertEqual(rows[2]["seriousness"], "nonserious")
        self.assertIsNone(rows[2]["num_events"])
        self.assertIsNone(rows[3]["num_affected"])
        self.assertIsNone(rows[3]["num_at_risk"])
        self.assertEqual(
            rows[1]["source_path"],
            "resultsSection.adverseEventsModule.seriousEvents[0].stats[1]",
        )
        self.assertEqual(rows[0]["nonserious_frequency_threshold"], "5")
        self.assertEqual(rows[0]["assessment_type"], "SYSTEMATIC_ASSESSMENT")
        self.assertEqual(rows[0]["arm_description"], "Candidate agent")
        self.assertEqual(output["trials"][0]["rows_with_unknown_affected"], 1)

    def test_renal_heuristics_do_not_discard_nonrenal_source_events(self):
        record = result_study()
        events = record["resultsSection"]["adverseEventsModule"]["otherEvents"]
        for term, soc in (
            ("Adrenal insufficiency", "Endocrine disorders"),
            ("Gallbladder obstruction", "Hepatobiliary disorders"),
        ):
            self.assertIsNone(renal_category(term, soc))
            events.append(
                {
                    "term": term,
                    "organSystem": soc,
                    "stats": [{"groupId": "E1", "numAffected": 1, "numAtRisk": 8}],
                }
            )
        output = run(record)
        self.assertEqual(len(output["adverse_events"]), 6)
        self.assertEqual(len(output["renal_events"]), 3)
        self.assertEqual(
            renal_category("Blood creatinine increased", "Investigations"),
            "kidney_laboratory",
        )

    def test_reported_event_without_statistics_remains_visible(self):
        record = result_study()
        record["resultsSection"]["adverseEventsModule"]["otherEvents"].append(
            {"term": "Proteinuria", "stats": []}
        )
        row = run(record)["adverse_events"][-1]
        self.assertFalse(row["statistics_present"])
        self.assertIsNone(row["num_affected"])
        self.assertIsNone(row["group_id"])
        self.assertEqual(
            row["source_path"], "resultsSection.adverseEventsModule.otherEvents[2]"
        )

    def test_invalid_counts_fail_with_source_location(self):
        for values in (
            {"numAffected": 9, "numAtRisk": 8},
            {"numAffected": -1},
            {"numAtRisk": -1},
            {"numAffected": True},
            {"numEvents": 1.5},
            {"numAtRisk": "8"},
        ):
            with self.subTest(values=values):
                record = result_study()
                record["resultsSection"]["adverseEventsModule"]["seriousEvents"][0][
                    "stats"
                ][0].update(values)
                with self.assertRaisesRegex(ValueError, r"NCT00000001.*stats\[0\]"):
                    run(record)

    def test_missing_results_states_are_distinct_from_reported_zero(self):
        record = study()
        self.assertEqual(run(record)["trials"][0]["ae_state"], "no_posted_results")
        del record["hasResults"]
        self.assertIsNone(run(record)["trials"][0]["has_results"])
        self.assertEqual(run(record)["trials"][0]["ae_state"], "results_section_absent")
        record.update(hasResults=True, resultsSection={})
        self.assertEqual(
            run(record)["trials"][0]["ae_state"], "results_without_adverse_events"
        )
        record["resultsSection"] = {"adverseEventsModule": {}}
        self.assertEqual(
            run(record)["trials"][0]["ae_state"], "adverse_events_module_empty"
        )
        record["resultsSection"]["adverseEventsModule"] = {
            "seriousEvents": [],
            "otherEvents": [],
        }
        self.assertEqual(
            run(record)["trials"][0]["ae_state"], "adverse_events_no_event_rows"
        )
        self.assertEqual(
            run(result_study())["trials"][0]["ae_state"], "adverse_events_reported"
        )

    def test_malformed_has_results_not_coerced_and_bad_ae_shapes_fail(self):
        record = study()
        for value in ("false", 0, []):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "hasResults"),
            ):
                run(dict(record, hasResults=value))
        for value in ("bad", [{"term": "not a module"}]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                run(dict(record, resultsSection={"adverseEventsModule": value}))
        record = result_study()
        record["resultsSection"]["adverseEventsModule"]["seriousEvents"][0]["stats"] = [
            None
        ]
        with self.assertRaises(ValueError):
            run(record)

    def test_complex_source_notes_are_deterministic_json_cells(self):
        record = result_study()
        record["resultsSection"]["adverseEventsModule"]["seriousEvents"][0]["notes"] = {
            "z": [1, None],
            "a": "note",
        }
        output = run(record)
        self.assertEqual(
            output["adverse_events"][0]["notes"], '{"a":"note","z":[1,null]}'
        )
        for table in ("decisions", "trials", "adverse_events", "renal_events"):
            for row in output[table]:
                self.assertTrue(
                    all(
                        value is None or isinstance(value, (str, int, float, bool))
                        for value in row.values()
                    )
                )

    def test_input_records_are_not_mutated(self):
        record = result_study()
        original = copy.deepcopy(record)
        run(record)
        self.assertEqual(record, original)


class ComparisonTests(unittest.TestCase):
    def test_source_type_changes_are_not_hidden_by_python_numeric_equality(self):
        baseline = study()
        baseline["protocolSection"]["designModule"] = {"enrollmentInfo": {"count": 0}}
        current = copy.deepcopy(baseline)
        current["protocolSection"]["designModule"]["enrollmentInfo"]["count"] = False
        result = compare_snapshots({"NCT00000001": current}, {"NCT00000001": baseline})[
            0
        ]
        self.assertEqual(result["changed_fields"], ["protocolSection.designModule"])

    def test_assessment_group_and_denominator_changes_are_detected(self):
        baseline = result_study()
        for change in ("assessment", "group", "denominator"):
            with self.subTest(change=change):
                current = copy.deepcopy(baseline)
                module = current["resultsSection"]["adverseEventsModule"]
                if change == "assessment":
                    module["seriousEvents"][0]["assessmentType"] = (
                        "NON_SYSTEMATIC_ASSESSMENT"
                    )
                elif change == "group":
                    module["eventGroups"][0]["description"] = "Revised population"
                else:
                    module["seriousEvents"][0]["stats"][0]["numAtRisk"] = 9
                row = compare_snapshots(
                    {"NCT00000001": current}, {"NCT00000001": baseline}
                )[0]
                self.assertEqual(row["status"], "changed")
                self.assertEqual(
                    row["changed_fields"], ["resultsSection.adverseEventsModule"]
                )

    def test_event_group_and_stat_order_are_semantically_unchanged(self):
        baseline = result_study()
        current = copy.deepcopy(baseline)
        module = current["resultsSection"]["adverseEventsModule"]
        module["eventGroups"].reverse()
        module["otherEvents"].reverse()
        module["seriousEvents"][0]["stats"].reverse()
        row = compare_snapshots({"NCT00000001": current}, {"NCT00000001": baseline})[0]
        self.assertEqual(row["status"], "unchanged")
        self.assertEqual(row["changed_fields"], [])

    def test_duplicate_event_and_missing_versus_null_remain_detectable(self):
        baseline = result_study()
        current = copy.deepcopy(baseline)
        module = current["resultsSection"]["adverseEventsModule"]
        module["seriousEvents"].append(copy.deepcopy(module["seriousEvents"][0]))
        self.assertEqual(
            compare_snapshots({"NCT00000001": current}, {"NCT00000001": baseline})[0][
                "status"
            ],
            "changed",
        )
        current = copy.deepcopy(baseline)
        current["resultsSection"]["adverseEventsModule"]["otherEvents"][0]["stats"][0][
            "numEvents"
        ] = None
        self.assertEqual(
            compare_snapshots({"NCT00000001": current}, {"NCT00000001": baseline})[0][
                "status"
            ],
            "changed",
        )

    def test_added_removed_and_protocol_status_changes(self):
        baseline = {"NCT00000001": study(), "NCT00000002": study("NCT00000002")}
        current = {"NCT00000001": study(), "NCT00000003": study("NCT00000003")}
        current["NCT00000001"]["protocolSection"]["statusModule"]["overallStatus"] = (
            "TERMINATED"
        )
        rows = {row["NCTId"]: row for row in compare_snapshots(current, baseline)}
        self.assertEqual(
            rows["NCT00000001"]["changed_fields"], ["protocolSection.statusModule"]
        )
        self.assertEqual(rows["NCT00000002"]["status"], "removed")
        self.assertEqual(rows["NCT00000003"]["status"], "added")


if __name__ == "__main__":
    unittest.main()
