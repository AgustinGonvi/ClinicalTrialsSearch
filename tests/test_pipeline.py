"""End-to-end guarantees using synthetic records and interchangeable backends."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from clinical_trials_search.pipeline import (
    RunFailed,
    compare_runs,
    load_snapshot,
    run_search,
)
from clinical_trials_search.profiles import query_plan, validate_profile
from clinical_trials_search.retrieval import RetrievalError, RetrievalResult


def record():
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT00000001",
                "briefTitle": "Synthetic kidney trial",
            },
            "statusModule": {"overallStatus": "COMPLETED"},
        },
        "hasResults": False,
    }


def profile():
    return {
        "schema_version": 1,
        "name": "kidney-test",
        "queries": [{"name": "condition", "params": {"query.cond": "kidney disease"}}],
        "selection": {"default": "include"},
    }


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.profile = self.root / "profile.json"
        self.profile.write_text(json.dumps(profile()), encoding="utf-8")
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def native(self, queries, destination, **kwargs):
        self.calls.append("native")
        (destination / "raw.json").write_text(json.dumps(record()), encoding="utf-8")
        return RetrievalResult(
            {"NCT00000001": record()},
            {q["name"]: ["NCT00000001"] for q in queries},
            {"apiVersion": "test", "dataTimestamp": "test"},
            "native",
            {},
        )

    def fallback(self, queries, destination, **kwargs):
        result = self.native(queries, destination)
        self.calls[-1] = "ctrdata"
        result.backend = "ctrdata"
        return result

    def fail(self, queries, destination, **kwargs):
        self.calls.append("failed-native")
        (destination / "partial-page.json").write_text("{}", encoding="utf-8")
        raise RetrievalError("synthetic connection failure", retryable=True)

    def test_complete_run_has_verified_hashes_and_separate_outputs(self):
        path, manifest = run_search(
            self.profile, self.root / "outputs", retrievers={"native": self.native}
        )
        self.assertEqual(manifest["status"], "complete")
        self.assertFalse(path.name.endswith(".partial"))
        self.assertEqual(load_snapshot(path)[0], {"NCT00000001": record()})
        self.assertTrue((path / "renal_events.csv").read_text().startswith("NCTId,"))
        self.assertNotIn(str(self.root), json.dumps(manifest))

    def test_auto_restarts_whole_query_plan_preserving_failed_attempt(self):
        path, manifest = run_search(
            self.profile,
            self.root,
            backend="auto",
            retrievers={"native": self.fail, "ctrdata": self.fallback},
        )
        self.assertEqual(self.calls, ["failed-native", "ctrdata"])
        self.assertEqual(manifest["backend"], "ctrdata")
        self.assertTrue((path / "attempts/native/partial-page.json").exists())
        self.assertEqual(manifest["attempts"][0]["status"], "failed")

    def test_valid_empty_does_not_fail_over(self):
        def empty(queries, destination, **kwargs):
            return RetrievalResult(
                {},
                {q["name"]: [] for q in queries},
                {"dataTimestamp": "test"},
                "native",
                {},
            )

        path, manifest = run_search(
            self.profile,
            self.root,
            backend="auto",
            retrievers={"native": empty, "ctrdata": self.fallback},
        )
        self.assertEqual(self.calls, [])
        self.assertEqual(load_snapshot(path)[0], {})
        self.assertEqual(manifest["counts"]["records"], 0)

    def test_validation_failure_does_not_trigger_fallback_or_complete(self):
        def invalid(queries, destination, **kwargs):
            result = self.native(queries, destination)
            result.query_ids = {}
            return result

        with self.assertRaises(RunFailed) as raised:
            run_search(
                self.profile,
                self.root,
                backend="auto",
                retrievers={"native": invalid, "ctrdata": self.fallback},
            )
        manifest = json.loads((raised.exception.path / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertTrue(raised.exception.path.name.endswith(".partial"))
        self.assertEqual(self.calls, ["native"])

    def test_nonretryable_error_does_not_bypass_rate_limit(self):
        def rate_limited(queries, destination):
            raise RetrievalError("rate limit exhausted", retryable=False)

        with self.assertRaises(RunFailed):
            run_search(
                self.profile,
                self.root,
                backend="auto",
                retrievers={"native": rate_limited, "ctrdata": self.fallback},
            )
        self.assertEqual(self.calls, [])

    def test_tampered_and_incomplete_snapshots_are_rejected(self):
        path, _ = run_search(
            self.profile, self.root, retrievers={"native": self.native}
        )
        (path / "records/NCT00000001.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "integrity"):
            load_snapshot(path)
        manifest_path = path / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["status"] = "failed"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "complete"):
            load_snapshot(path)

    def test_comparison_flags_changed_profile(self):
        old, _ = run_search(self.profile, self.root, retrievers={"native": self.native})
        rules = profile()
        rules["selection"]["default"] = "review"
        self.profile.write_text(json.dumps(rules), encoding="utf-8")
        new, manifest = run_search(
            self.profile, self.root, baseline=old, retrievers={"native": self.native}
        )
        self.assertFalse(manifest["baseline"]["same_profile"])
        diff = compare_runs(old, new, self.root / "comparison.json")
        self.assertFalse(diff["same_profile"])
        self.assertEqual(diff["changes"][0]["status"], "unchanged")

    def test_unknown_review_ids_fail_instead_of_being_ignored(self):
        reviews = self.root / "reviews.csv"
        reviews.write_text(
            "NCTId,decision,reason\nNCT00000002,include,Reviewed scope\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RunFailed, "absent"):
            run_search(
                self.profile,
                self.root,
                reviews_path=reviews,
                retrievers={"native": self.native},
            )

    def test_output_name_cannot_route_into_checkout_under_allowed_parent(self):
        checkout = self.root / "kidney-test"
        (checkout / "src/clinical_trials_search").mkdir(parents=True)
        (checkout / "pyproject.toml").touch()
        with self.assertRaisesRegex(ValueError, "outside"):
            run_search(self.profile, self.root, retrievers={"native": self.native})
        self.assertEqual(self.calls, [])


class ProfileTests(unittest.TestCase):
    def test_alias_discovery_and_explicit_ids_are_independent_queries(self):
        rules = profile()
        rules["selection"].update(aliases=["DRUG-12"], include_ids=["NCT00000001"])
        plan = query_plan(validate_profile(rules))
        self.assertEqual(plan[1]["params"], {"query.intr": '"DRUG-12"'})
        self.assertEqual(plan[2]["params"], {"query.term": "AREA[NCTId](NCT00000001)"})

    def test_invalid_profiles_fail_early(self):
        variants = []
        rules = profile()
        rules["schema_version"] = True
        variants.append(rules)
        rules = profile()
        rules["selection"]["defualt"] = "include"
        variants.append(rules)
        rules = profile()
        rules["selection"]["default"] = []
        variants.append(rules)
        rules = profile()
        rules["queries"][0]["params"] = {"fieldz": "kidney"}
        variants.append(rules)
        rules = profile()
        rules["name"] = "../unsafe"
        variants.append(rules)
        rules = profile()
        rules["selection"]["aliases"] = ['bad"query']
        variants.append(rules)
        rules = profile()
        rules["selection"]["aliases"] = ["DRUG-12"]
        rules["expected"] = {"external_agents": ["drug12"]}
        variants.append(rules)
        rules = profile()
        rules["queries"].append(copy.deepcopy(rules["queries"][0]))
        variants.append(rules)
        for rules in variants:
            with self.subTest(rules=rules), self.assertRaises(ValueError):
                validate_profile(rules)


if __name__ == "__main__":
    unittest.main()
