"""Profile-driven screening and clinical-trial evidence tables.

This module performs no retrieval and has no topic-specific aliases or trial IDs.
Automatic inclusion is not human adjudication. AE rows are event/arm observations,
never additive counts of unique people across different events.
"""

from __future__ import annotations

import json
import re
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _cell(value: Any) -> Any:
    """Keep null/zero distinct and serialize complex CSV values consistently."""
    return _json(value) if isinstance(value, (dict, list)) else value


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _array(value: Any) -> list:
    return value if isinstance(value, list) else []


def alias_matches(text: str, alias: str) -> bool:
    """Match a drug name/code with punctuation tolerance and token boundaries."""
    code = re.sub(r"[^a-z0-9]", "", alias.lower())
    if not code or not isinstance(text, str):
        return False
    pattern = r"(?<![a-z0-9])" + r"[\W_]*".join(map(re.escape, code)) + r"(?![a-z0-9])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def intervention_names(record: dict) -> list[str]:
    module = _object(
        _object(record.get("protocolSection")).get("armsInterventionsModule")
    )
    names = []
    for intervention in _array(module.get("interventions")):
        intervention = _object(intervention)
        names.extend(
            [intervention.get("name"), *_array(intervention.get("otherNames"))]
        )
    return [name for name in names if isinstance(name, str)]


def _alias_hits(record: dict, aliases: list[str]) -> list[str]:
    names = intervention_names(record)
    return [
        alias for alias in aliases if any(alias_matches(name, alias) for name in names)
    ]


def _phrase_hits(record: dict, phrases: list[str]) -> list[str]:
    protocol = _object(record.get("protocolSection"))
    ident = _object(protocol.get("identificationModule"))
    description = _object(protocol.get("descriptionModule"))
    texts = [
        ident.get("briefTitle"),
        ident.get("officialTitle"),
        description.get("briefSummary"),
        description.get("detailedDescription"),
        *intervention_names(record),
    ]
    texts = [text for text in texts if isinstance(text, str)]
    return [
        phrase
        for phrase in phrases
        if phrase.strip()
        and any(
            re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, re.IGNORECASE)
            for text in texts
        )
    ]


RENAL_ORGAN_SYSTEM = "Renal and urinary disorders"
INFECTION_ORGAN_SYSTEM = "Infections and infestations"

# "renal" as its own word, optionally with a prefix that still names the kidney.
# Adrenal, suprarenal, and infrarenal (aortic) terms are deliberately not matched.
_KIDNEY = r"(?:kidney|(?<![a-z])(?:(?:pre|post|intra|peri)-?)?renal(?![a-z]))"
# Cholecystitis (gallbladder) and dacryocystitis (tear sac) are not bladder terms.
_CYSTITIS = r"(?<!chole)(?<!dacryo)cystitis"

_COLLISION = re.compile(r"\badrenal\b|\bgallbladder\b")
# The rules run in this order; the first match decides the category.
_TERM_RULES = (
    (
        "kidney_laboratory",
        re.compile(
            # Creatine (phospho)kinase is a muscle enzyme, sometimes misspelled.
            r"creatinine(?!\s*(?:phospho)?kinase)"
            r"|blood urea|urea nitrogen|\bbun\b"
            r"|glomerular filtration|\bgfr\b"
            # EGFR alone may name the epidermal growth factor receptor.
            r"|\begfr\b(?=.*(?:decreas|declin|reduc|low|increas|abnormal))"
            r"|(?:decreas|declin|reduc|low|increas|abnormal)\w*\s+(?:in\s+)?egfr\b"
            r"|cystatin"
            rf"|{_KIDNEY} function test"
        ),
    ),
    (
        "kidney_diagnosis",
        re.compile(
            r"hepatorenal|cardiorenal"
            # Pyelonephritis is handled as an infection term below.
            r"|(?<!pyelo)nephritis|nephropathy|nephrotic|(?<![a-z])nephrosis"
            r"|nephrosclerosis|nephrotoxic|glomerulosclerosis|glomerulopathy"
            r"|acute kidney|chronic kidney"
            rf"|{_KIDNEY} (?:failure|impairment|insufficiency|injury|dysfunction)"
            rf"|{_KIDNEY} (?:disease|infarct)"
            rf"|end[- ]?stage {_KIDNEY}"
            r"|tubular (?:necrosis|acidosis|injury|disorder|dysfunction)"
            r"|(?<![a-z])ura?emi[ac]|azota?emi[ac]"
            r"|\b(?:aki|ckd|esrd|eskd|esrf)\b"
        ),
    ),
    (
        "urinary_marker",
        re.compile(
            r"proteinuria|albuminuria|ha?ematuria|pyuria|leu[ck]ocyturia"
            r"|(?:protein|albumin|blood|blood cells?|leu[ck]ocytes?)\s+(?:in\s+)?urine"
            r"|(?<![a-z])urine\s+(?:protein|albumin)"
            r"|(?<![a-z])urinary\s+(?:protein|albumin|casts)"
        ),
    ),
)
_REPLACEMENT_THERAPY = re.compile(
    # Iridodialysis, cyclodialysis, and retinal dialysis are eye terms.
    r"(?<!retinal )(?<![a-z])(?:ha?emo)?dialysis"
    r"|ha?emo(?:dia)?filtration"
    rf"|{_KIDNEY} replacement"
    rf"|{_KIDNEY}(?: and \w+)? (?:transplant|allograft|graft)"
    rf"|transplanted {_KIDNEY}"
)
# A term that names the kidney, the urinary tract, or a urinary-tract infection.
_KIDNEY_OR_URINARY_TRACT = re.compile(
    rf"{_KIDNEY}"
    r"|renovascular"
    # Epinephrine, norepinephrine, and metanephrine are not kidney terms.
    r"|nephr(?!ine)"
    r"|glomerul|ureter|(?<![a-z])urethr"
    r"|(?<!g[au]ll)(?<!g[au]ll[ -])bladder"
    # Urine as a specimen (drug screens, hormones) is not matched on its own.
    r"|(?<![a-z])(?:genito-?)?urinary(?![a-z])"
    r"|(?<![a-z])urinat|micturition|(?<![a-z])urine output"
    rf"|(?<![a-z])pyel(?:o|itis)|urosepsis|{_CYSTITIS}|bacteriuria|\butis?\b"
)


def renal_classification(
    term: str | None, organ_system: str | None
) -> tuple[str | None, str | None, str | None]:
    """Return the renal category, what it rests on, and the matched term text.

    The basis is ``term``, ``term_and_organ_system``, or ``organ_system``. This
    is a screening heuristic; all source events are retained separately.
    """
    text = (term or "").lower()
    if _COLLISION.search(text):
        return None, None, None
    for category, pattern in _TERM_RULES:
        match = pattern.search(text)
        if match:
            return category, "term", match.group()
    tract = _KIDNEY_OR_URINARY_TRACT.search(text)
    # A urinary-tract term is called an infection only when the sponsor filed
    # it under the infection organ system.
    if tract and organ_system == INFECTION_ORGAN_SYSTEM:
        return "urinary_infection", "term_and_organ_system", tract.group()
    match = _REPLACEMENT_THERAPY.search(text)
    if match:
        return "kidney_replacement_therapy", "term", match.group()
    if organ_system == RENAL_ORGAN_SYSTEM:
        return "other_renal_or_urinary", "organ_system", None
    if tract:
        return "other_renal_or_urinary", "term", tract.group()
    return None, None, None


def renal_category(term: str | None, organ_system: str | None) -> str | None:
    """Heuristic renal subset; all source events are retained separately."""
    return renal_classification(term, organ_system)[0]


def _validate_counts(stat: dict, nct: str, source_path: str) -> None:
    for key in ("numAffected", "numAtRisk", "numEvents"):
        value = stat.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(
                f"{nct} {source_path}.{key}: expected a nonnegative integer or null"
            )
    affected, at_risk = stat.get("numAffected"), stat.get("numAtRisk")
    if affected is not None and at_risk is not None and affected > at_risk:
        raise ValueError(f"{nct} {source_path}: numAffected exceeds numAtRisk")


def _ae_module(record: dict, nct: str) -> dict:
    results = record.get("resultsSection")
    if results is not None and not isinstance(results, dict):
        raise ValueError(f"{nct}: resultsSection must be an object or null")
    module = _object(results).get("adverseEventsModule")
    if module is not None and not isinstance(module, dict):
        raise ValueError(f"{nct}: adverseEventsModule must be an object or null")
    module = _object(module)
    for key in ("eventGroups", "seriousEvents", "otherEvents"):
        if module.get(key) is not None and not isinstance(module[key], list):
            raise ValueError(
                f"{nct}: adverseEventsModule.{key} must be an array or null"
            )
    return module


def _extract_events(
    nct: str, record: dict, selection: str, reviewed: bool
) -> list[dict]:
    module = _ae_module(record, nct)
    groups = {}
    for group in _array(module.get("eventGroups")):
        if not isinstance(group, dict):
            raise ValueError(f"{nct}: adverse-event group is not an object")
        group_id = group.get("id")
        if group_id is not None and not isinstance(group_id, str):
            raise ValueError(f"{nct}: adverse-event group ID must be a string or null")
        if group_id is not None:
            if group_id in groups:
                raise ValueError(
                    f"{nct}: duplicate adverse-event group ID {group_id!r}"
                )
            groups[group_id] = group
    rows = []
    for table, seriousness in (
        ("seriousEvents", "serious"),
        ("otherEvents", "nonserious"),
    ):
        for event_index, event in enumerate(_array(module.get(table))):
            if not isinstance(event, dict):
                raise ValueError(
                    f"{nct}: {table}[{event_index}] is not an event object"
                )
            event_path = f"resultsSection.adverseEventsModule.{table}[{event_index}]"
            for key in ("term", "organSystem", "assessmentType"):
                if event.get(key) is not None and not isinstance(event[key], str):
                    raise ValueError(
                        f"{nct}: {event_path}.{key} must be a string or null"
                    )
            if event.get("stats") is not None and not isinstance(event["stats"], list):
                raise ValueError(f"{nct}: {event_path}.stats must be an array or null")
            stats = _array(event.get("stats"))
            category, basis, matched = renal_classification(
                event.get("term"), event.get("organSystem")
            )
            # A reported term without arm statistics must remain visible.
            for stat_index, stat in enumerate(stats or [None]):
                if stats and not isinstance(stat, dict):
                    raise ValueError(
                        f"{nct}: {event_path}.stats[{stat_index}] is not an object"
                    )
                has_stats = stat is not None
                stat = stat or {}
                source_path = (
                    f"{event_path}.stats[{stat_index}]" if has_stats else event_path
                )
                _validate_counts(stat, nct, source_path)
                group_id = stat.get("groupId")
                if group_id is not None and not isinstance(group_id, str):
                    raise ValueError(
                        f"{nct}: {source_path}.groupId must be a string or null"
                    )
                group = groups.get(group_id, {})
                row = {
                    "NCTId": nct,
                    "selection": selection,
                    "reviewed_by_human": reviewed,
                    "seriousness": seriousness,
                    "classification": category,
                    "classification_basis": basis,
                    "classification_match": matched,
                    "term": event.get("term"),
                    "organ_system": event.get("organSystem"),
                    "group_id": group_id,
                    "arm": group.get("title"),
                    "arm_description": group.get("description"),
                    "num_affected": stat.get("numAffected"),
                    "num_at_risk": stat.get("numAtRisk"),
                    "num_events": stat.get("numEvents"),
                    "assessment_type": event.get("assessmentType"),
                    "notes": event.get("notes"),
                    "nonserious_frequency_threshold": module.get("frequencyThreshold"),
                    "time_frame": module.get("timeFrame"),
                    "reporting_description": module.get("description"),
                    "statistics_present": has_stats,
                    "group_defined": group_id in groups,
                    "event_source_path": event_path,
                    "source_path": source_path,
                    "url": f"https://clinicaltrials.gov/study/{nct}",
                }
                rows.append({key: _cell(value) for key, value in row.items()})
    return rows


def _names(value: Any, key: str | None = None) -> str | None:
    """A JSON list cell of the source strings; an absent list stays missing."""
    if not isinstance(value, list):
        return None
    items = [_object(item).get(key) if key else item for item in value]
    return _json([item for item in items if isinstance(item, str)])


def _date(status: dict, key: str) -> Any:
    return _cell(_object(status.get(key)).get("date"))


def _trial_summary(nct: str, record: dict, decision: dict, rows: list[dict]) -> dict:
    protocol = _object(record.get("protocolSection"))
    ident = _object(protocol.get("identificationModule"))
    status = _object(protocol.get("statusModule"))
    design = _object(protocol.get("designModule"))
    enrollment = _object(design.get("enrollmentInfo"))
    sponsor = _object(
        _object(protocol.get("sponsorCollaboratorsModule")).get("leadSponsor")
    )
    results = record.get("resultsSection")
    results_present = isinstance(results, dict)
    raw_module = _object(results).get("adverseEventsModule")
    module_present = isinstance(raw_module, dict)
    module = _ae_module(record, nct)
    event_count = sum(
        len(_array(module.get(key))) for key in ("seriousEvents", "otherEvents")
    )
    has_results = record.get("hasResults")
    if not results_present:
        ae_state = (
            "no_posted_results" if has_results is False else "results_section_absent"
        )
    elif not module_present:
        ae_state = "results_without_adverse_events"
    elif not module:
        ae_state = "adverse_events_module_empty"
    elif not event_count:
        ae_state = "adverse_events_no_event_rows"
    else:
        ae_state = "adverse_events_reported"
    return {
        "NCTId": nct,
        "title": _cell(ident.get("briefTitle")),
        "status": _cell(status.get("overallStatus")),
        "study_type": _cell(design.get("studyType")),
        "phases": _names(design.get("phases")),
        "enrollment": _cell(enrollment.get("count")),
        "enrollment_type": _cell(enrollment.get("type")),
        "conditions": _names(
            _object(protocol.get("conditionsModule")).get("conditions")
        ),
        "interventions": _names(
            _object(protocol.get("armsInterventionsModule")).get("interventions"),
            "name",
        ),
        "lead_sponsor": _cell(sponsor.get("name")),
        "lead_sponsor_class": _cell(sponsor.get("class")),
        "start_date": _date(status, "startDateStruct"),
        "primary_completion_date": _date(status, "primaryCompletionDateStruct"),
        "completion_date": _date(status, "completionDateStruct"),
        "first_posted": _date(status, "studyFirstPostDateStruct"),
        "results_first_posted": _date(status, "resultsFirstPostDateStruct"),
        "has_results": _cell(has_results),
        "selection": decision["selection"],
        "reviewed_by_human": decision["reviewed_by_human"],
        "ae_state": ae_state,
        "results_section_present": results_present,
        "adverse_events_module_present": module_present,
        "source_ae_events": event_count,
        "extracted_ae_rows": len(rows),
        "rows_with_unknown_affected": sum(row["num_affected"] is None for row in rows),
        "rows_with_unknown_at_risk": sum(row["num_at_risk"] is None for row in rows),
        "last_update_posted": _date(status, "lastUpdatePostDateStruct"),
        "url": f"https://clinicaltrials.gov/study/{nct}",
    }


def analyze(
    records: dict[str, dict],
    profile: dict,
    query_ids: dict[str, list[str]],
    reviews: dict[str, dict] | None = None,
) -> dict:
    """Screen records and extract all AEs for included and unresolved candidates.

    Precedence: review override, excluded ID, included ID, positive alias,
    excluded alias, phrase nomination, discovery default, otherwise review.
    Positive and excluded aliases together therefore retain combination trials.
    """
    selection = profile.get("selection", {})
    default = selection.get("default", "review")
    if default not in {"include", "review"}:
        raise ValueError("selection.default must be include or review")
    reviews = reviews or {}
    for nct, override in reviews.items():
        if not isinstance(override, dict) or override.get("decision") not in {
            "include",
            "exclude",
            "review",
        }:
            raise ValueError(f"Invalid review decision for {nct}")
        if (
            not isinstance(override.get("reason"), str)
            or not override["reason"].strip()
        ):
            raise ValueError(f"A nonempty review reason is required for {nct}")
    include_ids = set(selection.get("include_ids", []))
    exclude_ids = set(selection.get("exclude_ids", []))
    query_names_by_id = {}
    for query_name, ids in query_ids.items():
        for nct in ids:
            query_names_by_id.setdefault(nct, set()).add(query_name)
    discovered = set(query_names_by_id)
    decisions, trials, events, warnings = [], [], [], []
    expected = profile.get("expected", {})
    expected_aliases = list(dict.fromkeys(expected.get("aliases", [])))
    external = list(dict.fromkeys(expected.get("external_agents", [])))
    coverage_aliases = list(
        dict.fromkeys([*selection.get("aliases", []), *expected_aliases, *external])
    )
    coverage = {alias: [] for alias in coverage_aliases}
    for nct, record in sorted(records.items()):
        if not isinstance(record, dict):
            raise ValueError(f"{nct}: record must be an object")
        if (
            record.get("hasResults") is not None
            and type(record["hasResults"]) is not bool
        ):
            raise ValueError(f"{nct}: hasResults must be a boolean or null")
        source_id = _object(
            _object(record.get("protocolSection")).get("identificationModule")
        ).get("nctId")
        if source_id != nct:
            raise ValueError(
                f"Record key {nct} does not match source nctId {source_id!r}"
            )
        hits = _alias_hits(record, selection.get("aliases", []))
        excluded_hits = _alias_hits(record, selection.get("exclude_aliases", []))
        phrases = _phrase_hits(record, selection.get("phrases", []))
        reviewed = nct in reviews
        if reviewed:
            state, reason = reviews[nct]["decision"], reviews[nct]["reason"].strip()
        elif nct in exclude_ids:
            state, reason = "exclude", "Explicitly excluded trial ID"
        elif nct in include_ids:
            state, reason = "include", "Explicitly included trial ID"
        elif hits:
            state, reason = "include", "Intervention alias: " + ", ".join(hits)
        elif excluded_hits:
            state, reason = (
                "exclude",
                "Excluded intervention alias: " + ", ".join(excluded_hits),
            )
        elif phrases:
            state, reason = (
                "review",
                "Phrase nomination requires scope review: " + ", ".join(phrases),
            )
        elif nct in discovered:
            state, reason = default, "Discovery query match; profile default"
        else:
            state, reason = (
                "review",
                "Record absent from discovery; requires scope review",
            )
        decision = {
            "NCTId": nct,
            "selection": state,
            "reason": reason,
            "reviewed_by_human": reviewed,
            "discovered": nct in discovered,
            "query_names": _json(sorted(query_names_by_id.get(nct, ()))),
            "matched_aliases": _json(hits),
            "matched_excluded_aliases": _json(excluded_hits),
            "matched_phrases": _json(phrases),
        }
        decisions.append(decision)
        rows = (
            _extract_events(nct, record, state, reviewed) if state != "exclude" else []
        )
        events.extend(rows)
        trials.append(_trial_summary(nct, record, decision, rows))
        if record.get("hasResults") is True and not isinstance(
            record.get("resultsSection"), dict
        ):
            warnings.append(
                f"{nct}: hasResults is true but the results section is absent"
            )
        if record.get("hasResults") is False and _object(record.get("resultsSection")):
            warnings.append(
                f"{nct}: hasResults is false despite a nonempty results section"
            )
        if state == "review":
            warnings.append(
                f"{nct}: eligibility remains unresolved; AE rows are labeled review"
            )
        if state == "include" and nct in discovered:
            for alias in _alias_hits(record, coverage_aliases):
                coverage[alias].append(nct)
        for row in rows:
            if row["statistics_present"] and not row["group_defined"]:
                warnings.append(f"{nct}: undefined event group at {row['source_path']}")
    external_codes = {re.sub(r"[^a-z0-9]", "", value.lower()) for value in external}
    required_aliases = list(
        dict.fromkeys([*selection.get("aliases", []), *expected_aliases])
    )
    missing_aliases = [
        alias
        for alias in required_aliases
        if not coverage[alias]
        and re.sub(r"[^a-z0-9]", "", alias.lower()) not in external_codes
    ]
    for alias in missing_aliases:
        warnings.append(f"Expected alias has no included discovery record: {alias}")
    missing_trials = sorted(set(expected.get("trial_ids", [])) - discovered)
    for nct in missing_trials:
        warnings.append(f"Expected trial absent from discovery: {nct}")
    for nct in sorted((include_ids | discovered | set(reviews)) - set(records)):
        warnings.append(
            f"Requested, discovered, or reviewed trial has no record payload: {nct}"
        )
    return {
        "decisions": decisions,
        "trials": trials,
        "adverse_events": events,
        "renal_events": [
            row.copy() for row in events if row["classification"] is not None
        ],
        "warnings": list(dict.fromkeys(warnings)),
        "alias_coverage": {
            "hits": coverage,
            "missing_expected": missing_aliases,
            "external_agents": external,
            "unmatched_external_agents": [
                alias for alias in external if not coverage[alias]
            ],
            "missing_expected_trials": missing_trials,
            "scope": "Included discovery records only; not completeness of all trials or registries",
        },
    }


def _canonical(value: Any, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {
            key: _canonical(item, (*path, key)) for key, item in sorted(value.items())
        }
    if isinstance(value, list):
        items = [_canonical(item, path) for item in value]
        if "adverseEventsModule" in path and path[-1] in {
            "eventGroups",
            "seriousEvents",
            "otherEvents",
            "stats",
        }:
            items.sort(key=_json)
        return items
    return value


def _source_sections(record: dict) -> dict:
    sections = {}
    for key, value in record.items():
        if (
            key in {"protocolSection", "resultsSection"}
            and isinstance(value, dict)
            and value
        ):
            for module, content in value.items():
                sections[f"{key}.{module}"] = _canonical(content, (key, module))
        else:
            sections[key] = _canonical(value, (key,))
    return sections


def compare_snapshots(
    current_records: dict[str, dict], baseline_records: dict[str, dict]
) -> list[dict]:
    """Compare source records; AE table/stat order alone is not a source change.

    This does not compare profiles, review decisions, or extraction-rule versions.
    Differences are reported at registry-module level, including AE assessments,
    group definitions, denominators and missing-versus-null fields.
    """
    rows = []
    for nct in sorted(set(current_records) | set(baseline_records)):
        if nct not in baseline_records:
            status, changed = "added", []
        elif nct not in current_records:
            status, changed = "removed", []
        else:
            current, previous = (
                _source_sections(current_records[nct]),
                _source_sections(baseline_records[nct]),
            )
            changed = [
                key
                for key in sorted(set(current) | set(previous))
                if key not in current
                or key not in previous
                or _json(current[key]) != _json(previous[key])
            ]
            status = "changed" if changed else "unchanged"
        rows.append({"NCTId": nct, "status": status, "changed_fields": changed})
    return rows
