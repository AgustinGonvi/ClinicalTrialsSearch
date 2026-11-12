"""Command-line behavior with mocked retrieval. No network or R is used."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clinical_trials_search import cli, pipeline
from clinical_trials_search.retrieval import RetrievalError, RetrievalResult

PROFILE = {
    "schema_version": 1,
    "name": "kidney-test",
    "queries": [{"name": "condition", "params": {"query.cond": "kidney disease"}}],
    "selection": {"default": "include", "aliases": ["DRUG-12"]},
}


def record():
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT00000001",
                "briefTitle": "Synthetic kidney trial",
            },
            "armsInterventionsModule": {"interventions": [{"name": "DRUG-12"}]},
        },
        "hasResults": False,
    }


def retrieved(queries, destination, progress=None):
    if progress:
        progress("condition: 1 of 1 records")
    return RetrievalResult(
        {"NCT00000001": record()},
        {query["name"]: ["NCT00000001"] for query in queries},
        {"apiVersion": "test", "dataTimestamp": "test"},
        "native",
        {},
    )


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.profile = self.root / "profile.json"
        self.profile.write_text(json.dumps(PROFILE), encoding="utf-8")

    def invoke(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main([str(argument) for argument in arguments])
        return code, out.getvalue(), err.getvalue()

    def run_command(self, *extra):
        return self.invoke(
            "run", "--profile", self.profile, "--output-root", self.root / "out", *extra
        )

    def test_count_previews_every_query_of_the_compiled_plan(self):
        def totals(queries):
            return {
                "version": {"dataTimestamp": "test"},
                "queries": [dict(query, total_count=7) for query in queries],
            }

        with patch.object(cli, "count_native", side_effect=totals):
            code, out, err = self.invoke("count", self.profile)
        report = json.loads(out)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            [query["name"] for query in report["queries"]],
            ["condition", "profile-aliases"],
        )
        self.assertEqual(report["sum_of_query_totals"], 14)
        self.assertIn("Nothing was saved", report["note"])
        self.assertEqual(list(self.root.iterdir()), [self.profile])

    def test_count_failure_is_one_plain_line(self):
        failure = RetrievalError(
            "Registry HTTP 400 during query 'condition': Bad area."
        )
        with patch.object(cli, "count_native", side_effect=failure):
            code, out, err = self.invoke("count", self.profile)
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err.strip(), f"Error: {failure}")

    def test_run_keeps_stdout_as_json_and_reports_progress_on_stderr(self):
        with patch.object(pipeline, "retrieve_native", side_effect=retrieved):
            code, out, err = self.run_command()
            quiet_code, quiet_out, quiet_err = self.run_command("--quiet")
        self.assertEqual((code, quiet_code), (0, 0))
        self.assertEqual(json.loads(out)["status"], "complete")
        self.assertEqual(json.loads(quiet_out)["counts"]["records"], 1)
        for expected in (
            "native backend",
            "condition: 1 of 1 records",
            "Writing outputs",
        ):
            self.assertIn(expected, err)
        self.assertEqual(quiet_err, "")

    def test_interrupt_exits_130_and_leaves_a_failed_snapshot(self):
        with patch.object(pipeline, "retrieve_native", side_effect=KeyboardInterrupt):
            code, out, err = self.run_command("--quiet")
        self.assertEqual((code, out), (130, ""))
        self.assertIn("Interrupted", err)
        partial = next((self.root / "out" / "kidney-test").glob("*.partial"))
        manifest = json.loads((partial / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "failed")


if __name__ == "__main__":
    unittest.main()
