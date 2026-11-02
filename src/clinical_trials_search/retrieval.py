"""Validated ClinicalTrials.gov retrieval, with an optional ctrdata R adapter.

Each call uses a fresh destination. Failed attempts and raw responses are retained.
This module never falls back between backends; the caller owns that decision.
"""

from __future__ import annotations

import datetime as dt
import http.client
import json
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path

API = "https://clinicaltrials.gov/api/v2"
NCT = re.compile(r"NCT\d{8}\Z")
PARAMS = {"query.cond", "query.term", "query.intr", "query.spons", "filter.ids"}


class RetrievalError(RuntimeError):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


class ValidationError(RetrievalError):
    def __init__(self, message):
        super().__init__(message, retryable=False)


@dataclass
class RetrievalResult:
    records: dict[str, dict]
    query_ids: dict[str, list[str]]
    version: dict
    backend: str
    metadata: dict


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValidationError(
            f"Cannot read valid JSON from {path.name}: {exc}"
        ) from exc


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _validate_queries(queries):
    if not isinstance(queries, list) or not queries:
        raise ValidationError("At least one query is required.")
    seen = set()
    for query in queries:
        if not isinstance(query, dict):
            raise ValidationError("Each query must be an object.")
        name, params = query.get("name"), query.get("params")
        if not isinstance(name, str) or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name
        ):
            raise ValidationError("Query names must be safe, nonempty slugs.")
        if name in seen:
            raise ValidationError(f"Duplicate query name: {name}")
        seen.add(name)
        if not isinstance(params, dict) or not params or set(params) - PARAMS:
            raise ValidationError(f"Unsupported or empty query parameters for {name}.")
        if any(
            not isinstance(value, str) or not value.strip() for value in params.values()
        ):
            raise ValidationError(f"Query values must be nonempty strings: {name}")
        if "filter.ids" in params:
            ids = params["filter.ids"].split(",")
            if any(not NCT.fullmatch(value.strip()) for value in ids):
                raise ValidationError(
                    "filter.ids must contain comma-separated NCT identifiers."
                )


def _prepare(queries, destination, backend):
    _validate_queries(queries)
    destination = Path(destination).resolve()
    if destination.exists() and (
        not destination.is_dir() or any(destination.iterdir())
    ):
        raise ValidationError(
            "Retrieval destination must be a fresh or empty directory; existing evidence is never overwritten."
        )
    destination.mkdir(parents=True, exist_ok=True)
    _write(destination / "query_plan.json", queries)
    metadata = {
        "backend": backend,
        "status": "running",
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "queries": [],
        "source": API,
    }
    _write(destination / "retrieval_manifest.json", metadata)
    return destination, metadata


def _failure(destination, metadata, exc):
    metadata.update(
        status="failed", error=str(exc), retryable=getattr(exc, "retryable", False)
    )
    _write(destination / "retrieval_manifest.json", metadata)


def _version(value):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("dataTimestamp"), str)
        or not value["dataTimestamp"]
    ):
        raise ValidationError("Registry version response lacks a valid dataTimestamp.")
    return value


def _record_id(record):
    try:
        nct = record["protocolSection"]["identificationModule"]["nctId"]
    except (TypeError, KeyError):
        raise ValidationError(
            "A study lacks protocolSection.identificationModule.nctId."
        ) from None
    if not isinstance(nct, str) or not NCT.fullmatch(nct):
        raise ValidationError(f"Invalid registry identifier: {nct!r}")
    if "hasResults" in record and not isinstance(record["hasResults"], bool):
        raise ValidationError(f"{nct}: hasResults must be a JSON boolean.")
    return nct


def _merge(records, record):
    nct = _record_id(record)
    if nct in records and _canonical(records[nct]) != _canonical(record):
        raise ValidationError(f"Conflicting duplicate full records for {nct}.")
    records[nct] = record
    return nct


def _count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError("Registry totalCount must be a nonnegative integer.")
    return value


def _page(page, expected, ids, seen_ids, tokens, records):
    if not isinstance(page, dict) or not isinstance(page.get("studies"), list):
        raise ValidationError("Registry page must contain a studies array.")
    if expected is None:
        expected = _count(page.get("totalCount"))
    elif (
        "totalCount" in page
        and page["totalCount"] is not None
        and _count(page["totalCount"]) != expected
    ):
        raise ValidationError("Registry totalCount changed during pagination.")
    for record in page["studies"]:
        nct = _merge(records, record)
        if nct in seen_ids:
            raise ValidationError(f"Duplicate {nct} within one paginated query.")
        ids.append(nct)
        seen_ids.add(nct)
    if len(ids) > expected:
        raise ValidationError("Retrieved more unique records than totalCount.")
    token = page.get("nextPageToken")
    if token is not None and (not isinstance(token, str) or not token):
        raise ValidationError("Invalid nextPageToken.")
    if token:
        if token in tokens:
            raise ValidationError("Repeated pagination token.")
        if not page["studies"] or len(ids) >= expected:
            raise ValidationError(
                "Pagination token conflicts with page/count completeness."
            )
        tokens.add(token)
    elif len(ids) != expected:
        raise ValidationError(
            f"Incomplete query: totalCount={expected}, retrieved={len(ids)}."
        )
    return expected, token


def _delay(headers, attempt):
    value = headers.get("Retry-After") if headers else None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = (
                parsedate_to_datetime(value) - dt.datetime.now(dt.timezone.utc)
            ).total_seconds()
        except (TypeError, ValueError, OverflowError):
            seconds = 2**attempt
    if seconds > 30:
        raise RetrievalError(
            "Registry Retry-After exceeds the 30-second bounded wait; retry later without switching backends.",
            retryable=False,
        )
    return max(0.0, seconds)


def _request_artifact(path, value, *, binary=False):
    """Disk failures cannot justify retrying or switching retrieval clients."""
    try:
        if binary:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        else:
            _write(path, value)
    except OSError as exc:
        raise RetrievalError(f"Local I/O failure: {exc}", retryable=False) from exc


def _request_json(url, raw_path, *, timeout, retries):
    """Retries means additional attempts; HTTP 429 can never trigger fallback."""
    saw_rate_limit = False
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "ClinicalTrialsSearch/0.1",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            saw_rate_limit |= exc.code == 429
            retry = exc.code == 429 or 500 <= exc.code <= 599
            artifact = (
                raw_path.parent / "failed_attempts" / f"{raw_path.stem}_{attempt + 1}"
            )
            failure = {"url": url, "status": exc.code, "attempt": attempt + 1}
            try:
                error_body = exc.read()
            except (http.client.IncompleteRead, OSError) as read_error:
                error_body = getattr(read_error, "partial", b"")
                failure["body_read_error"] = str(read_error)
                if isinstance(read_error, ssl.SSLCertVerificationError):
                    retry = False
            _request_artifact(artifact.with_suffix(".body"), error_body, binary=True)
            _request_artifact(artifact.with_suffix(".json"), failure)
            if not retry or attempt == retries:
                raise RetrievalError(
                    f"Registry HTTP {exc.code}; inspect saved attempt artifacts.",
                    retryable=retry and not saw_rate_limit,
                ) from exc
            time.sleep(_delay(exc.headers, attempt))
        except http.client.IncompleteRead as exc:
            artifact = (
                raw_path.parent / "failed_attempts" / f"{raw_path.stem}_{attempt + 1}"
            )
            _request_artifact(
                artifact.with_suffix(".partial"), exc.partial, binary=True
            )
            _request_artifact(
                artifact.with_suffix(".json"),
                {
                    "url": url,
                    "error": str(exc),
                    "partial_bytes": len(exc.partial),
                    "attempt": attempt + 1,
                },
            )
            if attempt == retries:
                raise RetrievalError(
                    "Registry response body was truncated; partial bytes retained.",
                    retryable=not saw_rate_limit,
                ) from exc
            time.sleep(min(8, 2**attempt))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            # Only network operations are inside this try block. SSL read errors
            # are transport failures; certificate verification stays fail-closed.
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            verification_failed = isinstance(reason, ssl.SSLCertVerificationError)
            artifact = (
                raw_path.parent
                / "failed_attempts"
                / f"{raw_path.stem}_{attempt + 1}.json"
            )
            _request_artifact(
                artifact, {"url": url, "error": str(exc), "attempt": attempt + 1}
            )
            if verification_failed:
                raise RetrievalError(
                    f"Registry TLS certificate verification failed: {exc}",
                    retryable=False,
                ) from exc
            if attempt == retries:
                raise RetrievalError(
                    f"Registry network failure: {exc}", retryable=not saw_rate_limit
                ) from exc
            time.sleep(min(8, 2**attempt))
        else:
            _request_artifact(raw_path, body, binary=True)
            try:
                return json.loads(body)
            except (UnicodeError, ValueError) as exc:
                raise ValidationError(
                    f"Invalid registry JSON in {raw_path.name}."
                ) from exc


def _finish(destination, metadata, records, query_ids, before, after, backend):
    if _canonical(before) != _canonical(after):
        raise ValidationError(
            "Registry version changed during retrieval; the run cannot be treated as one snapshot."
        )
    for nct, record in sorted(records.items()):
        _write(destination / "records" / f"{nct}.json", record)
    _write(destination / "query_ids.json", query_ids)
    metadata.update(
        status="complete",
        finished_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
        source_version_before=before,
        source_version_after=after,
        unique_records=len(records),
    )
    _write(destination / "retrieval_manifest.json", metadata)
    return RetrievalResult(records, query_ids, after, backend, metadata)


def retrieve_native(queries, destination, *, timeout=60, retries=3):
    if timeout <= 0 or not isinstance(retries, int) or retries < 0:
        raise ValidationError(
            "timeout must be positive and retries a nonnegative integer."
        )
    destination, metadata = _prepare(queries, destination, "native")
    records, query_ids = {}, {}
    try:
        before = _version(
            _request_json(
                API + "/version",
                destination / "version_before.json",
                timeout=timeout,
                retries=retries,
            )
        )
        metadata["source_version_before"] = before
        for query in queries:
            ids, seen_ids, tokens = [], set(), set()
            expected, token, number = None, None, 0
            pages = []
            while True:
                number += 1
                params = dict(
                    query["params"], pageSize=100, countTotal="true", format="json"
                )
                if token:
                    params["pageToken"] = token
                raw = destination / "raw" / query["name"] / f"page_{number:06d}.json"
                page = _request_json(
                    API + "/studies?" + urllib.parse.urlencode(params),
                    raw,
                    timeout=timeout,
                    retries=retries,
                )
                pages.append(str(raw.relative_to(destination)))
                expected, token = _page(page, expected, ids, seen_ids, tokens, records)
                if not token:
                    break
            query_ids[query["name"]] = ids
            metadata["queries"].append(
                {
                    "name": query["name"],
                    "params": query["params"],
                    "total_count": expected,
                    "retrieved": len(ids),
                    "raw_pages": pages,
                }
            )
            _write(destination / "retrieval_manifest.json", metadata)
        after = _version(
            _request_json(
                API + "/version",
                destination / "version_after.json",
                timeout=timeout,
                retries=retries,
            )
        )
        metadata["source_version_after"] = after
        return _finish(
            destination, metadata, records, query_ids, before, after, "native"
        )
    except RetrievalError as exc:
        _failure(destination, metadata, exc)
        raise


def _ctrdata_query(params):
    if "filter.ids" in params:
        raise RetrievalError(
            "ctrdata does not support filter.ids faithfully; use the native backend or an explicit AREA[NCTId] query.term.",
            retryable=False,
        )
    return urllib.parse.urlencode(
        {key.removeprefix("query."): value for key, value in params.items()},
        quote_via=urllib.parse.quote,
    )


def _bridge_path(destination, value, label):
    """Validate artifact references before consuming the bridge's JSON output."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(ord(c) < 32 for c in value)
    ):
        raise ValidationError(
            f"Malformed ctrdata output: {label} must be a relative file name."
        )
    relative = Path(value)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ValidationError(
            f"Malformed ctrdata output: {label} must stay inside its attempt."
        )
    try:
        path = (destination / relative).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValidationError(
            f"Malformed ctrdata output: cannot resolve {label} inside its attempt."
        ) from exc
    if destination not in path.parents:
        raise ValidationError(
            f"Malformed ctrdata output: {label} must stay inside its attempt."
        )
    return path


def _bridge_manifest(destination, queries):
    manifest = _read(destination / "bridge_manifest.json")
    if not isinstance(manifest, dict):
        raise ValidationError(
            "Malformed ctrdata output: bridge manifest must be an object."
        )
    for field in ("ctrdata_version", "r_version"):
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            raise ValidationError(
                f"Malformed ctrdata output: {field} must be a nonempty string."
            )
    reports = manifest.get("queries")
    if not isinstance(reports, list) or any(
        not isinstance(item, dict) for item in reports
    ):
        raise ValidationError(
            "Malformed ctrdata output: queries must be an array of objects."
        )
    if [report.get("name") for report in reports] != [
        query["name"] for query in queries
    ]:
        raise ValidationError("ctrdata bridge returned a different query plan.")
    for report, query in zip(reports, queries):
        if report.get("queryterm") != _ctrdata_query(query["params"]):
            raise ValidationError(
                "ctrdata bridge returned a different query expression."
            )
        for field in ("count_file", "records_file"):
            _bridge_path(destination, report.get(field), field)
        pages = report.get("raw_pages")
        if not isinstance(pages, list):
            raise ValidationError(
                "Malformed ctrdata output: raw_pages must be an array."
            )
        for relative in pages:
            _bridge_path(destination, relative, "raw_pages entry")
    return manifest


def retrieve_ctrdata(
    queries, destination, *, rscript="Rscript", r_library=None, timeout=900
):
    if timeout <= 0:
        raise ValidationError("timeout must be positive.")
    destination, metadata = _prepare(queries, destination, "ctrdata")
    try:
        bridge_queries = [
            {
                "name": query["name"],
                "params": query["params"],
                "queryterm": _ctrdata_query(query["params"]),
            }
            for query in queries
        ]
        plan = destination / "bridge_plan.json"
        _write(plan, {"queries": bridge_queries})
        env = os.environ.copy()
        temporary = destination / "tmp"
        temporary.mkdir()
        env.update({key: str(temporary) for key in ("TEMP", "TMP", "TMPDIR")})
        if r_library is not None:
            env["R_LIBS_USER"] = str(Path(r_library).resolve())
        bridge = Path(__file__).with_name("r") / "ctrdata_fetch.R"
        with (
            (destination / "ctrdata_stdout.log").open("wb") as stdout,
            (destination / "ctrdata_stderr.log").open("wb") as stderr,
        ):
            try:
                completed = subprocess.run(
                    [
                        str(rscript),
                        "--vanilla",
                        str(bridge),
                        str(plan),
                        str(destination),
                    ],
                    cwd=destination,
                    env=env,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise RetrievalError(
                    f"Rscript executable not found: {rscript}", retryable=True
                ) from exc
            except OSError as exc:
                raise RetrievalError(
                    f"Cannot launch Rscript: {exc}", retryable=False
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise RetrievalError(
                    "ctrdata process timed out; partial artifacts retained.",
                    retryable=True,
                ) from exc
        if completed.returncode:
            error_path = destination / "bridge_error.json"
            error = (
                _read(error_path)
                if error_path.exists()
                else {
                    "message": "ctrdata process failed; inspect stderr log.",
                    "retryable": False,
                }
            )
            if not isinstance(error, dict) or not isinstance(error.get("message"), str):
                raise ValidationError(
                    "Malformed ctrdata output: error report requires a message string."
                )
            if not isinstance(error.get("retryable", False), bool):
                raise ValidationError(
                    "Malformed ctrdata output: retryable must be a boolean."
                )
            raise RetrievalError(
                error["message"], retryable=error.get("retryable", False)
            )
        bridge_manifest = _bridge_manifest(destination, queries)
        before = _version(_read(destination / "version_before.json"))
        after = _version(_read(destination / "version_after.json"))
        metadata.update(
            source_version_before=before,
            source_version_after=after,
            ctrdata_version=bridge_manifest.get("ctrdata_version"),
            r_version=bridge_manifest.get("r_version"),
        )
        records, query_ids, import_metadata = {}, {}, {}
        manifests = bridge_manifest["queries"]
        for query, report in zip(queries, manifests):
            count_page = _read(
                _bridge_path(destination, report["count_file"], "count_file")
            )
            if not isinstance(count_page, dict):
                raise ValidationError(
                    "Malformed ctrdata output: count response must be an object."
                )
            expected = _count(count_page.get("totalCount"))
            raw_records, ids, seen_ids, tokens = {}, [], set(), set()
            raw_pages = report["raw_pages"]
            if expected and not raw_pages:
                raise ValidationError(
                    "ctrdata returned no raw pages for a nonempty query."
                )
            last_token = None
            for index, relative in enumerate(raw_pages):
                page = _read(_bridge_path(destination, relative, "raw_pages entry"))
                _, last_token = _page(
                    page, expected, ids, seen_ids, tokens, raw_records
                )
                if index < len(raw_pages) - 1 and not last_token:
                    raise ValidationError(
                        "ctrdata has extra pages after the final pagination token."
                    )
            if last_token or len(ids) != expected:
                raise ValidationError("ctrdata raw pages are incomplete.")
            exported = _read(
                _bridge_path(destination, report["records_file"], "records_file")
            )
            if not isinstance(exported, list):
                raise ValidationError(
                    "ctrdata record export must be an array, not a flattened data frame."
                )
            imported = {}
            for original in exported:
                if not isinstance(original, dict):
                    raise ValidationError("ctrdata exported a non-object study.")
                record = dict(original)
                extra = {
                    key: record.pop(key)
                    for key in ("_id", "ctrname", "record_last_import")
                    if key in record
                }
                nct = _record_id(record)
                if "_id" in extra and extra["_id"] != nct:
                    raise ValidationError(
                        "ctrdata document ID differs from its registry NCT ID."
                    )
                if nct in imported:
                    raise ValidationError(f"Duplicate ctrdata export for {nct}.")
                imported[nct] = record
                import_metadata.setdefault(nct, []).append(
                    {"query": query["name"], **extra}
                )
            if set(imported) != set(ids):
                raise ValidationError(
                    "ctrdata SQLite export IDs differ from its raw registry pages."
                )
            for nct, record in imported.items():
                if _canonical(record) != _canonical(raw_records[nct]):
                    raise ValidationError(
                        f"ctrdata altered registry source fields for {nct}."
                    )
                _merge(records, record)
            query_ids[query["name"]] = ids
            metadata["queries"].append(
                {
                    "name": query["name"],
                    "params": query["params"],
                    "ctrdata_queryterm": report.get("queryterm"),
                    "total_count": expected,
                    "retrieved": len(ids),
                    "raw_pages": raw_pages,
                }
            )
        metadata["record_import_metadata"] = import_metadata
        return _finish(
            destination, metadata, records, query_ids, before, after, "ctrdata"
        )
    except RetrievalError as exc:
        _failure(destination, metadata, exc)
        raise
