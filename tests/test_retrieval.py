"""Offline backend contract tests. No R installation or network is required."""

import datetime as dt
import http.client
import io
import json
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.parse
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

from clinical_trials_search import retrieval as r

VERSION = {"apiVersion": "test", "dataTimestamp": "2026-01-01T00:00:00"}
QUERIES = [{"name": "condition", "params": {"query.cond": "example condition"}}]


def study(nct="NCT00000001", title="Example study"):
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct, "briefTitle": title}
        },
        "hasResults": False,
    }


def response(value):
    return io.BytesIO(json.dumps(value).encode())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class NativeRetrieval(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name) / "attempt"

    def retrieve(self, pages, queries=None):
        values = [VERSION, *pages, VERSION]
        with patch.object(
            r.urllib.request,
            "urlopen",
            side_effect=[response(value) for value in values],
        ) as get:
            result = r.retrieve_native(queries or QUERIES, self.destination, retries=0)
        return result, get

    def test_paginated_full_records_and_exact_query_plan(self):
        first, second = study(), study("NCT00000002")
        result, get = self.retrieve(
            [
                {"totalCount": 2, "studies": [first], "nextPageToken": "page-two"},
                {"studies": [second]},
            ]
        )
        self.assertEqual(result.records, {"NCT00000001": first, "NCT00000002": second})
        self.assertEqual(
            result.query_ids, {"condition": ["NCT00000001", "NCT00000002"]}
        )
        self.assertEqual(result.version, VERSION)
        self.assertEqual(result.backend, "native")
        urls = [call.args[0].full_url for call in get.call_args_list]
        query = urllib.parse.parse_qs(urllib.parse.urlparse(urls[1]).query)
        self.assertEqual(query["pageSize"], ["100"])
        self.assertEqual(query["countTotal"], ["true"])
        self.assertNotIn("fields", query)
        self.assertIn("pageToken=page-two", urls[2])
        self.assertEqual(
            json.loads((self.destination / "query_plan.json").read_text()), QUERIES
        )
        self.assertEqual(
            len(list((self.destination / "raw" / "condition").glob("page_*.json"))), 2
        )
        self.assertTrue((self.destination / "records" / "NCT00000001.json").exists())

    def test_valid_empty_is_success(self):
        result, _ = self.retrieve([{"totalCount": 0, "studies": []}])
        self.assertEqual(result.records, {})
        self.assertEqual(result.query_ids, {"condition": []})
        self.assertEqual(result.metadata["status"], "complete")

    def test_identical_cross_query_record_is_deduplicated(self):
        queries = QUERIES + [{"name": "term", "params": {"query.term": "example"}}]
        page = {"totalCount": 1, "studies": [study()]}
        result, _ = self.retrieve([page, page], queries)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(len(result.query_ids), 2)

    def test_conflicting_cross_query_record_fails(self):
        queries = QUERIES + [{"name": "term", "params": {"query.term": "example"}}]
        with self.assertRaisesRegex(r.ValidationError, "Conflicting duplicate"):
            self.retrieve(
                [
                    {"totalCount": 1, "studies": [study()]},
                    {"totalCount": 1, "studies": [study(title="Changed")]},
                ],
                queries,
            )

    def test_missing_count_and_incomplete_pagination_fail(self):
        for page in ({"studies": []}, {"totalCount": 2, "studies": [study()]}):
            with self.subTest(page=page):
                destination = Path(self.temp.name) / (
                    "case-" + str(len(list(Path(self.temp.name).iterdir())))
                )
                with patch.object(
                    r.urllib.request,
                    "urlopen",
                    side_effect=[response(VERSION), response(page)],
                ):
                    with self.assertRaises(r.ValidationError) as raised:
                        r.retrieve_native(QUERIES, destination, retries=0)
                self.assertFalse(raised.exception.retryable)
                self.assertTrue(
                    (destination / "raw" / "condition" / "page_000001.json").exists()
                )

    def test_changed_count_is_rejected(self):
        with self.assertRaisesRegex(r.ValidationError, "totalCount changed"):
            self.retrieve(
                [
                    {"totalCount": 3, "studies": [study()], "nextPageToken": "two"},
                    {"totalCount": 2, "studies": [study("NCT00000002")]},
                ]
            )

    def test_repeated_token_is_rejected(self):
        with self.assertRaisesRegex(r.ValidationError, "Repeated pagination"):
            self.retrieve(
                [
                    {"totalCount": 3, "studies": [study()], "nextPageToken": "same"},
                    {"studies": [study("NCT00000002")], "nextPageToken": "same"},
                ]
            )

    def test_duplicate_within_query_is_rejected(self):
        with self.assertRaisesRegex(r.ValidationError, "within one paginated query"):
            self.retrieve([{"totalCount": 2, "studies": [study(), study()]}])

    def test_invalid_nct_and_boolean_count_are_rejected(self):
        for page in (
            {"totalCount": 1, "studies": [study("../escape")]},
            {"totalCount": True, "studies": [study()]},
        ):
            with self.subTest(page=page):
                destination = Path(self.temp.name) / (
                    "case-" + str(len(list(Path(self.temp.name).iterdir())))
                )
                with patch.object(
                    r.urllib.request,
                    "urlopen",
                    side_effect=[response(VERSION), response(page)],
                ):
                    with self.assertRaises(r.ValidationError):
                        r.retrieve_native(QUERIES, destination, retries=0)

    def test_changed_source_version_is_rejected_and_saved(self):
        after = dict(VERSION, dataTimestamp="2026-01-02T00:00:00")
        with patch.object(
            r.urllib.request,
            "urlopen",
            side_effect=[
                response(VERSION),
                response({"totalCount": 0, "studies": []}),
                response(after),
            ],
        ):
            with self.assertRaisesRegex(r.ValidationError, "version changed"):
                r.retrieve_native(QUERIES, self.destination, retries=0)
        self.assertEqual(
            json.loads((self.destination / "version_after.json").read_text()), after
        )
        self.assertEqual(
            json.loads((self.destination / "retrieval_manifest.json").read_text())[
                "status"
            ],
            "failed",
        )

    def test_http_400_does_not_retry_or_allow_fallback(self):
        error = urllib.error.HTTPError(
            "https://example.invalid",
            400,
            "Bad request",
            {},
            io.BytesIO(b"bad request"),
        )
        with patch.object(r.urllib.request, "urlopen", side_effect=error) as get:
            with self.assertRaises(r.RetrievalError) as raised:
                r.retrieve_native(QUERIES, self.destination, retries=3)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(get.call_count, 1)
        self.assertTrue(list(self.destination.rglob("*.body")))

    def test_long_retry_after_stops_without_retrying_early(self):
        errors = [
            urllib.error.HTTPError(
                "https://example.invalid",
                429,
                "Too many",
                {"Retry-After": "86400"},
                io.BytesIO(b"rate limited"),
            )
            for _ in range(2)
        ]
        with (
            patch.object(r.urllib.request, "urlopen", side_effect=errors),
            patch.object(r.time, "sleep") as sleep,
        ):
            with self.assertRaises(r.RetrievalError) as raised:
                r.retrieve_native(QUERIES, self.destination, retries=1)
        self.assertFalse(raised.exception.retryable)
        sleep.assert_not_called()
        self.assertEqual(len(list(self.destination.rglob("*.body"))), 1)

    def test_short_429_backoff_respects_server_and_blocks_fallback(self):
        errors = [
            urllib.error.HTTPError(
                "https://example.invalid",
                429,
                "Too many",
                {"Retry-After": "2"},
                io.BytesIO(b"rate limited"),
            )
            for _ in range(2)
        ]
        with (
            patch.object(r.urllib.request, "urlopen", side_effect=errors),
            patch.object(r.time, "sleep") as sleep,
        ):
            with self.assertRaises(r.RetrievalError) as raised:
                r.retrieve_native(QUERIES, self.destination, retries=1)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(sleep.call_args.args, (2.0,))
        self.assertEqual(len(list(self.destination.rglob("*.body"))), 2)

    def test_http_date_retry_after_does_not_retry_early(self):
        future = format_datetime(
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1), usegmt=True
        )
        error = urllib.error.HTTPError(
            "https://example.invalid",
            429,
            "Too many",
            {"Retry-After": future},
            io.BytesIO(b"rate limited"),
        )
        with (
            patch.object(r.urllib.request, "urlopen", side_effect=error) as get,
            patch.object(r.time, "sleep") as sleep,
        ):
            with self.assertRaises(r.RetrievalError) as raised:
                r.retrieve_native(QUERIES, self.destination, retries=1)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()

    def test_truncated_http_body_is_preserved_and_retryable(self):
        broken = http.client.IncompleteRead(b'{"partial":', 20)
        with patch.object(r.urllib.request, "urlopen", side_effect=broken):
            with self.assertRaises(r.RetrievalError) as raised:
                r.retrieve_native(QUERIES, self.destination, retries=0)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(
            next(self.destination.rglob("*.partial")).read_bytes(), b'{"partial":'
        )

    def test_truncated_response_can_retry_successfully(self):
        values = [
            http.client.IncompleteRead(b'{"partial":', 20),
            response(VERSION),
            response({"totalCount": 0, "studies": []}),
            response(VERSION),
        ]
        with (
            patch.object(r.urllib.request, "urlopen", side_effect=values),
            patch.object(r.time, "sleep"),
        ):
            result = r.retrieve_native(QUERIES, self.destination, retries=1)
        self.assertEqual(result.metadata["status"], "complete")
        self.assertTrue(list(self.destination.rglob("*.partial")))

    def test_5xx_and_network_exhaustion_are_retryable(self):
        for index, error in enumerate(
            (
                urllib.error.HTTPError(
                    "https://example.invalid", 503, "Unavailable", {}, io.BytesIO(b"")
                ),
                urllib.error.URLError("connection failed"),
            )
        ):
            with self.subTest(index=index):
                with patch.object(r.urllib.request, "urlopen", side_effect=error):
                    with self.assertRaises(r.RetrievalError) as raised:
                        r.retrieve_native(
                            QUERIES, Path(self.temp.name) / str(index), retries=0
                        )
                self.assertTrue(raised.exception.retryable)

    def test_existing_attempt_is_never_overwritten(self):
        self.destination.mkdir()
        marker = self.destination / "original.txt"
        marker.write_text("retain")
        with self.assertRaises(r.ValidationError):
            r.retrieve_native(QUERIES, self.destination)
        self.assertEqual(marker.read_text(), "retain")


class CtrdataAdapter(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name) / "attempt"

    def fake_bridge(self, command, **kwargs):
        destination = Path(command[-1])
        record = study()
        save(destination / "version_before.json", VERSION)
        save(destination / "version_after.json", VERSION)
        save(
            destination / "raw" / "condition" / "count.json",
            {"totalCount": 1, "studies": [record]},
        )
        save(
            destination / "raw" / "condition" / "page.json",
            {"totalCount": 1, "studies": [record]},
        )
        save(
            destination / "records_condition.json",
            [dict(record, ctrname="CTGOV2", record_last_import="fixture")],
        )
        save(
            destination / "bridge_manifest.json",
            {
                "ctrdata_version": "1.26.3",
                "r_version": "fixture",
                "queries": [
                    {
                        "name": "condition",
                        "queryterm": "cond=example%20condition",
                        "count_file": "raw/condition/count.json",
                        "records_file": "records_condition.json",
                        "raw_pages": ["raw/condition/page.json"],
                    }
                ],
            },
        )
        return subprocess.CompletedProcess(command, 0)

    def test_adapter_returns_source_records_and_separate_import_metadata(self):
        with patch.object(r.subprocess, "run", side_effect=self.fake_bridge) as run:
            result = r.retrieve_ctrdata(
                QUERIES,
                self.destination,
                rscript="custom-Rscript",
                r_library=Path(self.temp.name),
            )
        self.assertEqual(result.records, {"NCT00000001": study()})
        self.assertEqual(result.query_ids, {"condition": ["NCT00000001"]})
        self.assertEqual(result.backend, "ctrdata")
        self.assertEqual(
            result.metadata["record_import_metadata"]["NCT00000001"][0]["ctrname"],
            "CTGOV2",
        )
        self.assertEqual(run.call_args.args[0][0], "custom-Rscript")
        self.assertEqual(
            Path(run.call_args.kwargs["env"]["TEMP"]).parent, self.destination
        )
        plan = json.loads((self.destination / "bridge_plan.json").read_text())
        self.assertEqual(plan["queries"][0]["queryterm"], "cond=example%20condition")

    def test_adapter_detects_sqlite_source_field_change(self):
        def corrupt(command, **kwargs):
            completed = self.fake_bridge(command, **kwargs)
            save(self.destination / "records_condition.json", [study(title="Altered")])
            return completed

        with patch.object(r.subprocess, "run", side_effect=corrupt):
            with self.assertRaisesRegex(r.ValidationError, "altered registry source"):
                r.retrieve_ctrdata(QUERIES, self.destination)

    def test_adapter_valid_empty_does_not_require_raw_pages(self):
        def empty(command, **kwargs):
            completed = self.fake_bridge(command, **kwargs)
            save(
                self.destination / "raw" / "condition" / "count.json",
                {"totalCount": 0, "studies": []},
            )
            save(self.destination / "records_condition.json", [])
            manifest = json.loads(
                (self.destination / "bridge_manifest.json").read_text()
            )
            manifest["queries"][0]["raw_pages"] = []
            save(self.destination / "bridge_manifest.json", manifest)
            return completed

        with patch.object(r.subprocess, "run", side_effect=empty):
            result = r.retrieve_ctrdata(QUERIES, self.destination)
        self.assertEqual(result.records, {})
        self.assertEqual(result.query_ids["condition"], [])

    def test_adapter_rejects_filter_ids_before_launch(self):
        with patch.object(r.subprocess, "run") as run:
            with self.assertRaisesRegex(r.RetrievalError, "filter.ids") as raised:
                r.retrieve_ctrdata(
                    [{"name": "ids", "params": {"filter.ids": "NCT00000001"}}],
                    self.destination,
                )
        run.assert_not_called()
        self.assertFalse(raised.exception.retryable)

    def test_missing_r_and_timeout_have_clear_errors(self):
        for index, error in enumerate(
            (
                FileNotFoundError("not installed"),
                subprocess.TimeoutExpired("Rscript", 1),
            )
        ):
            with (
                self.subTest(index=index),
                patch.object(r.subprocess, "run", side_effect=error),
            ):
                with self.assertRaises(r.RetrievalError):
                    r.retrieve_ctrdata(
                        QUERIES, Path(self.temp.name) / str(index), timeout=1
                    )

    def test_adapter_propagates_rate_limit_without_retryability(self):
        def limited(command, **kwargs):
            save(
                Path(command[-1]) / "bridge_error.json",
                {"message": "Registry HTTP 429", "retryable": False},
            )
            return subprocess.CompletedProcess(command, 1)

        with patch.object(r.subprocess, "run", side_effect=limited):
            with self.assertRaises(r.RetrievalError) as raised:
                r.retrieve_ctrdata(QUERIES, self.destination)
        self.assertFalse(raised.exception.retryable)

    def test_local_r_launch_failure_is_not_retryable(self):
        with patch.object(
            r.subprocess, "run", side_effect=PermissionError("not executable")
        ):
            with self.assertRaises(r.RetrievalError) as raised:
                r.retrieve_ctrdata(QUERIES, self.destination)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(
            json.loads((self.destination / "retrieval_manifest.json").read_text())[
                "status"
            ],
            "failed",
        )


if __name__ == "__main__":
    unittest.main()
