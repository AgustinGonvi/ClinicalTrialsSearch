"""Transactional runs: retrieve, validate, interpret, export, then mark complete."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import platform
import re
from pathlib import Path

from . import __version__
from .evidence import analyze, compare_snapshots
from .profiles import NCT, SLUG, load_profile, query_plan
from .retrieval import RetrievalError, retrieve_ctrdata, retrieve_native
from .settings import require_external


class RunFailed(RuntimeError):
    def __init__(self, message, path):
        super().__init__(message)
        self.path = path


def write_text(path: Path, text: str) -> None:
    """LF on every platform: equal content must hash equally across systems."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, value) -> None:
    write_text(
        path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )


def write_evidence(path: Path, analysis: dict) -> None:
    """Stream the analysis with one table row per line.

    A single dumps call holds a large search's whole file in memory twice.
    """

    def encode(value) -> str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("{\n")
        for position, (key, value) in enumerate(analysis.items()):
            stream.write(f"  {encode(key)}: ")
            if isinstance(value, list) and value:
                stream.write("[\n")
                for index, row in enumerate(value):
                    comma = "," if index < len(value) - 1 else ""
                    stream.write(f"    {encode(row)}{comma}\n")
                stream.write("  ]")
            else:
                stream.write(encode(value))
            stream.write(",\n" if position < len(analysis) - 1 else "\n")
        stream.write("}\n")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_csv(
    path: Path, rows: list[dict], empty_fields: tuple[str, ...] = ("NCTId",)
) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row)) or list(
        empty_fields
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    k: json.dumps(v, ensure_ascii=False, sort_keys=True)
                    if isinstance(v, (list, dict))
                    else v
                    for k, v in row.items()
                }
            )


def load_reviews(path: Path | str | None) -> dict[str, dict]:
    if not path:
        return {}
    result = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["NCTId", "decision", "reason"]:
            raise ValueError(
                "Reviews CSV headers must be NCTId,decision,reason in that order"
            )
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Every review row must have exactly three columns")
            nct = row["NCTId"]
            if not NCT.fullmatch(nct or "") or nct in result:
                raise ValueError("Reviews contain an invalid or duplicate NCTId")
            if (
                row["decision"] not in {"include", "exclude", "review"}
                or not (row["reason"] or "").strip()
            ):
                raise ValueError(
                    "Every review needs include/exclude/review and a nonempty reason"
                )
            result[nct] = {"decision": row["decision"], "reason": row["reason"].strip()}
    return result


def load_snapshot(path: Path | str) -> tuple[dict, dict]:
    root = Path(path)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("status") != "complete":
        raise ValueError("Only complete schema-version-1 snapshots can be compared")
    hashes = manifest.get("files", {})
    if not isinstance(hashes, dict):
        raise ValueError("Snapshot lacks file hashes")
    records = {}
    paths = sorted((root / "records").glob("*.json"))
    for path in paths:
        name = path.relative_to(root).as_posix()
        if hashes.get(name) != digest(path):
            raise ValueError(f"Snapshot integrity check failed: {name}")
        record = json.loads(path.read_text(encoding="utf-8"))
        nct = (
            record.get("protocolSection", {})
            .get("identificationModule", {})
            .get("nctId")
        )
        if not isinstance(nct, str) or not NCT.fullmatch(nct) or path.stem != nct:
            raise ValueError("Invalid snapshot record identifier")
        records[nct] = record
    expected = {
        key for key in hashes if key.startswith("records/") and key.endswith(".json")
    }
    if expected != {f"records/{nct}.json" for nct in records}:
        raise ValueError("Snapshot is missing records listed in its manifest")
    if manifest.get("counts", {}).get("records") != len(records):
        raise ValueError("Snapshot record count does not match its manifest")
    return records, manifest


def _same_artifact(old_root, old_manifest, new_root, new_manifest, name, key):
    """Whether two runs used the same profile, query plan, or reviews.

    Equal hashes settle it. Otherwise compare the parsed files, because
    releases before 0.2 wrote platform line endings, so one profile hashed
    differently on Windows and Linux. A file that fails its own manifest
    hash establishes nothing.
    """
    if old_manifest.get(key) == new_manifest.get(key):
        return True
    try:
        values = []
        for root, manifest in ((old_root, old_manifest), (new_root, new_manifest)):
            path = Path(root) / name
            if digest(path) != manifest.get(key):
                return False
            values.append(json.loads(path.read_text(encoding="utf-8")))
        return values[0] == values[1]
    except (OSError, UnicodeError, ValueError):
        return False


def _same_inputs(old_root, old_manifest, new_root, new_manifest) -> dict:
    result = {
        label: _same_artifact(
            old_root, old_manifest, new_root, new_manifest, name, key
        )
        for label, name, key in (
            ("same_profile", "profile.json", "profile_sha256"),
            ("same_query_plan", "queries.json", "query_plan_sha256"),
            ("same_reviews", "reviews.json", "reviews_sha256"),
        )
    }
    result["same_software"] = old_manifest.get(
        "source_fingerprint"
    ) == new_manifest.get("source_fingerprint")
    return result


def source_fingerprint() -> str:
    root = Path(__file__).parent
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.suffix in {".py", ".R"}):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def _validate_bundle(bundle, queries):
    if not isinstance(bundle.records, dict) or not isinstance(bundle.query_ids, dict):
        raise ValueError("Backend did not return the common record contract")
    if set(bundle.query_ids) != {q["name"] for q in queries}:
        raise ValueError("Backend query results do not match the requested query plan")
    covered = set()
    for ids in bundle.query_ids.values():
        if not isinstance(ids, list) or len(ids) != len(set(ids)):
            raise ValueError("Backend query IDs are invalid or duplicated")
        covered.update(ids)
    if covered != set(bundle.records):
        raise ValueError("Backend records and query membership disagree")
    for nct, record in bundle.records.items():
        if not isinstance(nct, str) or not NCT.fullmatch(nct):
            raise ValueError("Backend returned an invalid NCTId")
        if (
            record.get("protocolSection", {})
            .get("identificationModule", {})
            .get("nctId")
            != nct
        ):
            raise ValueError("Backend record key and NCTId disagree")


def _citations(records, decisions, path):
    selected = {row["NCTId"] for row in decisions if row["selection"] != "exclude"}
    lines = []
    for nct in sorted(selected):
        protocol = records[nct]["protocolSection"]
        ident = protocol["identificationModule"]
        title = " ".join(
            str(ident.get("officialTitle") or ident.get("briefTitle") or nct).split()
        )
        lines.extend(["TY  - DATA", f"TI  - {title}"])
        # The registration date gives reference managers a year to sort and cite.
        status = protocol.get("statusModule")
        posted = (status if isinstance(status, dict) else {}).get(
            "studyFirstPostDateStruct"
        )
        date = posted.get("date") if isinstance(posted, dict) else None
        if isinstance(date, str) and re.fullmatch(r"\d{4}(-\d{2}){0,2}", date):
            parts = date.split("-")
            lines.append(f"PY  - {parts[0]}")
            # RIS dates always carry three slashes: YYYY/MM/DD/other.
            lines.append("DA  - " + "/".join(parts + [""] * (4 - len(parts))))
        lines.extend(
            [
                "DB  - ClinicalTrials.gov",
                f"AN  - {nct}",
                f"UR  - https://clinicaltrials.gov/study/{nct}",
                "ER  -",
                "",
            ]
        )
    write_text(path, "\n".join(lines))


def _report(manifest, analysis):
    counts = manifest["counts"]
    lines = [
        f"# {manifest['profile_name']}",
        "",
        f"Run: {manifest['run_id']}. Registry: ClinicalTrials.gov. Backend: {manifest['backend']}.",
        "",
        f"Retrieved {counts['records']} records; automatic/manual decisions: {counts['included']} include, {counts['review']} review, {counts['excluded']} exclude.",
        "",
        f"Exported {counts['adverse_event_rows']} adverse-event rows, of which {counts['renal_event_rows']} match the kidney/urinary heuristic. These are event-by-arm rows, not unique patients.",
        "",
        "Review candidates remain in event exports with their selection label. An automatic include is not a human review. See decisions.csv and evidence.json.",
        "",
        "## Warnings",
        "",
    ]
    lines.extend(
        [f"- {warning}" for warning in analysis["warnings"]]
        or [
            "- No configured coverage warnings. This does not establish search completeness."
        ]
    )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Missing results, missing event tables, missing counts, and an absent renal term are distinct states. None establishes that an outcome was measured and normal. Blank CSV counts represent unknown values; JSON preserves null. Serious and nonserious events and study arms stay separate. Reported adverse events do not establish treatment causality.",
            "",
            "The kidney categories are transparent text/SOC heuristics for review, not adjudicated diagnoses. The search covers this registry and this query plan only. Another client for the same registry cannot recover a trial registered elsewhere.",
            "",
            "## Reproducibility",
            "",
            "profile.json and queries.json record the rules and exact plan. records/ contains the complete registry records returned. attempts/ retains backend evidence, including failed attempts. manifest.json records registry version, source fingerprint, counts and SHA-256 file hashes. Local configuration and output paths are not copied into the manifest.",
            "",
        ]
    )
    if manifest.get("baseline"):
        lines.extend(
            [
                "Source-record changes against the explicit baseline are in changes.csv and changes.json. Changed query plans or rules can also change membership; differences are not automatically registry changes.",
                "",
            ]
        )
    return "\n".join(lines)


def run_search(
    profile_path,
    output_root,
    *,
    name=None,
    backend="native",
    baseline=None,
    reviews_path=None,
    settings=None,
    retrievers=None,
    progress=None,
):
    """Run one search. ``progress`` receives short status lines, if given."""
    say = progress or (lambda message: None)
    profile = load_profile(profile_path)
    name = name or profile["name"]
    if not isinstance(name, str) or not SLUG.fullmatch(name):
        raise ValueError("Search name must be a lowercase hyphen-separated slug")
    if backend not in {"native", "ctrdata", "auto"}:
        raise ValueError("Backend must be native, ctrdata, or auto")
    output = require_external(output_root)
    reviews = load_reviews(reviews_path)
    previous, baseline_manifest = load_snapshot(baseline) if baseline else ({}, None)
    queries = query_plan(profile)
    settings = settings or {}
    functions = retrievers or {"native": retrieve_native, "ctrdata": retrieve_ctrdata}
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d_%H%M%S_%fZ")
    final = require_external(output / name / run_id)
    staging = require_external(final.with_name(final.name + ".partial"))
    staging.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "tool_version": __version__,
        "source_fingerprint": source_fingerprint(),
        "run_id": run_id,
        "profile_name": profile["name"],
        "status": "running",
        "registry": "ClinicalTrials.gov",
        "requested_backend": backend,
        "attempts": [],
    }
    manifest["runtime"] = {"python": platform.python_version(), "os": platform.system()}
    write_json(staging / "profile.json", profile)
    write_json(staging / "queries.json", queries)
    write_json(staging / "reviews.json", reviews)
    manifest["profile_sha256"] = digest(staging / "profile.json")
    manifest["query_plan_sha256"] = digest(staging / "queries.json")
    manifest["reviews_sha256"] = digest(staging / "reviews.json")
    write_json(staging / "manifest.json", manifest)

    def mark_failed(reason):
        manifest.update(status="failed", error=reason)
        for attempt in manifest["attempts"]:
            if attempt["status"] == "running":
                attempt.update(status="failed", error=reason)
        write_json(staging / "manifest.json", manifest)

    try:
        for choice in ["native", "ctrdata"] if backend == "auto" else [backend]:
            destination = staging / "attempts" / choice
            destination.mkdir(parents=True)
            attempt = {"backend": choice, "status": "running"}
            manifest["attempts"].append(attempt)
            kwargs = (
                {
                    "rscript": settings.get("rscript", "Rscript"),
                    "r_library": settings.get("r_library"),
                }
                if choice == "ctrdata"
                else {}
            )
            if progress:
                kwargs["progress"] = progress
            noun = "query" if len(queries) == 1 else "queries"
            say(f"Retrieving {len(queries)} {noun} with the {choice} backend")
            try:
                bundle = functions[choice](queries, destination, **kwargs)
                _validate_bundle(bundle, queries)
                attempt["status"] = "complete"
                break
            except RetrievalError as error:
                attempt.update(
                    status="failed", error=str(error), retryable=error.retryable
                )
                write_json(staging / "manifest.json", manifest)
                if backend != "auto" or choice != "native" or not error.retryable:
                    raise
                say(f"The {choice} attempt failed; restarting the whole query plan")
        else:
            raise RuntimeError("No backend completed")
        unknown_reviews = set(reviews) - set(bundle.records)
        if unknown_reviews:
            raise ValueError(
                "Reviews refer to records absent from this run: "
                + ", ".join(sorted(unknown_reviews))
            )
        say(f"Screening {len(bundle.records)} records and extracting adverse events")
        analysis = analyze(bundle.records, profile, bundle.query_ids, reviews=reviews)
        say("Writing outputs")
        manifest.update(
            backend=bundle.backend,
            registry_version=bundle.version,
            retrieval_metadata=bundle.metadata,
        )
        (staging / "records").mkdir()
        for nct, record in sorted(bundle.records.items()):
            write_json(staging / "records" / f"{nct}.json", record)
        write_json(staging / "query_ids.json", bundle.query_ids)
        for key, fields in (
            ("decisions", ("NCTId", "selection", "reason", "reviewed_by_human")),
            (
                "trials",
                (
                    "NCTId",
                    "title",
                    "status",
                    "study_type",
                    "phases",
                    "enrollment",
                    "conditions",
                    "interventions",
                    "lead_sponsor",
                    "has_results",
                    "ae_state",
                    "selection",
                ),
            ),
            (
                "adverse_events",
                (
                    "NCTId",
                    "selection",
                    "seriousness",
                    "classification",
                    "classification_basis",
                    "classification_match",
                    "term",
                    "num_affected",
                    "num_at_risk",
                    "num_events",
                    "source_path",
                ),
            ),
            (
                "renal_events",
                (
                    "NCTId",
                    "selection",
                    "seriousness",
                    "classification",
                    "classification_basis",
                    "classification_match",
                    "term",
                    "num_affected",
                    "num_at_risk",
                    "source_path",
                ),
            ),
        ):
            write_csv(staging / f"{key}.csv", analysis[key], fields)
        _citations(bundle.records, analysis["decisions"], staging / "citations.ris")
        if baseline_manifest:
            changes = compare_snapshots(bundle.records, previous)
            write_json(staging / "changes.json", changes)
            write_csv(
                staging / "changes.csv", changes, ("NCTId", "status", "changed_fields")
            )
            manifest["baseline"] = {
                "run_id": baseline_manifest["run_id"],
                "manifest_sha256": digest(Path(baseline) / "manifest.json"),
                **_same_inputs(baseline, baseline_manifest, staging, manifest),
            }
            if not manifest["baseline"]["same_profile"]:
                analysis["warnings"].append(
                    "Baseline uses a different profile: membership changes may reflect changed rules or queries."
                )
            if not manifest["baseline"]["same_reviews"]:
                analysis["warnings"].append(
                    "Baseline uses different imported review decisions; derived evidence can differ even when records are unchanged."
                )
            if not manifest["baseline"]["same_software"]:
                analysis["warnings"].append(
                    "Baseline uses different software; inspect changes to extraction and selection before interpreting derived differences."
                )
        manifest["counts"] = {
            "records": len(bundle.records),
            "included": sum(r["selection"] == "include" for r in analysis["decisions"]),
            "review": sum(r["selection"] == "review" for r in analysis["decisions"]),
            "excluded": sum(r["selection"] == "exclude" for r in analysis["decisions"]),
            "adverse_event_rows": len(analysis["adverse_events"]),
            "renal_event_rows": len(analysis["renal_events"]),
        }
        manifest["warnings"] = analysis["warnings"]
        manifest["alias_coverage"] = analysis["alias_coverage"]
        write_evidence(staging / "evidence.json", analysis)
        write_text(staging / "report.md", _report(manifest, analysis))
        manifest["files"] = {
            p.relative_to(staging).as_posix(): digest(p)
            for p in sorted(staging.rglob("*"))
            if p.is_file() and p.name != "manifest.json"
        }
        manifest["status"] = "complete"
        manifest["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_json(staging / "manifest.json", manifest)
        staging.rename(final)
        return final, manifest
    except KeyboardInterrupt:
        # Ctrl-C must not leave a snapshot that still claims to be running.
        mark_failed("Interrupted before completion")
        raise
    except Exception as error:
        mark_failed(f"{type(error).__name__}: {error}")
        raise RunFailed(str(error), staging) from error


def compare_runs(old, new, output):
    baseline, old_manifest = load_snapshot(old)
    current, new_manifest = load_snapshot(new)
    result = {
        "old_run": old_manifest["run_id"],
        "new_run": new_manifest["run_id"],
        **_same_inputs(old, old_manifest, new, new_manifest),
        "changes": compare_snapshots(current, baseline),
    }
    target = require_external(output)
    if target.exists():
        raise ValueError("Comparison output already exists; choose a new file")
    write_json(target, result)
    return result
