"""Validate the portable search profile and compile its exact retrieval plan."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
NCT = re.compile(r"NCT[0-9]{8}\Z")
PARAMS = {"query.cond", "query.term", "query.intr", "query.spons", "filter.ids"}
RESERVED = {"profile-aliases", "profile-include-ids"}


def _keys(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = set(value) - set(allowed)
    if unknown:
        raise ValueError(f"{label}: unknown keys: {', '.join(sorted(unknown))}")


def _strings(value, label, *, ids=False):
    if not isinstance(value, list) or any(
        not isinstance(x, str) or not x.strip() for x in value
    ):
        raise ValueError(f"{label} must be a list of nonempty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} contains duplicates")
    if any(x != x.strip() or any(ord(c) < 32 for c in x) for x in value):
        raise ValueError(
            f"{label} contains whitespace at an edge or control characters"
        )
    if ids and any(not NCT.fullmatch(x) for x in value):
        raise ValueError(f"{label} requires NCT followed by eight digits")
    return value


def validate_profile(value: dict) -> dict:
    """Return a normalized copy, rejecting misspelled or unsupported options."""
    profile = copy.deepcopy(value)
    _keys(
        profile,
        {"schema_version", "name", "description", "queries", "selection", "expected"},
        "profile",
    )
    if type(profile.get("schema_version")) is not int or profile["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    if not isinstance(profile.get("name"), str) or not SLUG.fullmatch(profile["name"]):
        raise ValueError("profile name must be a lowercase hyphen-separated slug")
    if not isinstance(profile.get("description", ""), str):
        raise ValueError("description must be a string")
    profile.setdefault("description", "")
    queries = profile.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("queries must be a nonempty list")
    names = set()
    for query in queries:
        _keys(query, {"name", "params"}, "query")
        name = query.get("name")
        if (
            not isinstance(name, str)
            or not SLUG.fullmatch(name)
            or name in RESERVED
            or name in names
        ):
            raise ValueError(
                "query names must be unique slugs and cannot use reserved profile-* names"
            )
        names.add(name)
        params = query.get("params")
        _keys(params, PARAMS, f"query {name} params")
        if not params or any(
            not isinstance(v, str) or not v.strip() for v in params.values()
        ):
            raise ValueError(f"query {name} requires nonempty string parameters")
        if "filter.ids" in params:
            ids = params["filter.ids"].split(",")
            if any(not NCT.fullmatch(i) for i in ids):
                raise ValueError(
                    "filter.ids must be comma-separated NCT identifiers without spaces"
                )
    selection = profile.setdefault("selection", {})
    _keys(
        selection,
        {
            "include_ids",
            "exclude_ids",
            "aliases",
            "exclude_aliases",
            "phrases",
            "default",
        },
        "selection",
    )
    for key in ("include_ids", "exclude_ids", "aliases", "exclude_aliases", "phrases"):
        _strings(
            selection.setdefault(key, []), f"selection.{key}", ids=key.endswith("_ids")
        )
    if set(selection["include_ids"]) & set(selection["exclude_ids"]):
        raise ValueError("include_ids and exclude_ids overlap")
    selection.setdefault("default", "review")
    if not isinstance(selection["default"], str) or selection["default"] not in {
        "include",
        "review",
    }:
        raise ValueError("selection.default must be include or review")
    expected = profile.setdefault("expected", {})
    _keys(expected, {"aliases", "trial_ids", "external_agents"}, "expected")
    for key in ("aliases", "trial_ids", "external_agents"):
        _strings(
            expected.setdefault(key, []), f"expected.{key}", ids=key == "trial_ids"
        )
    aliases = selection["aliases"] + selection["exclude_aliases"] + expected["aliases"]
    if any('"' in x or not re.search(r"[a-zA-Z0-9]", x) for x in aliases):
        raise ValueError(
            "aliases need a letter or digit and cannot contain double quotes"
        )
    normalized_external = {
        re.sub(r"[^a-z0-9]", "", item.lower()) for item in expected["external_agents"]
    }
    normalized_required = {
        re.sub(r"[^a-z0-9]", "", item.lower())
        for item in expected["aliases"] + selection["aliases"]
    }
    if normalized_external & normalized_required:
        raise ValueError(
            "external_agents must be separate from required/discovery aliases"
        )
    return profile


def load_profile(path: Path | str) -> dict:
    return validate_profile(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def query_plan(profile: dict) -> list[dict]:
    """Aliases discover mechanism-blind records independently of free-text queries."""
    result = copy.deepcopy(profile["queries"])
    aliases = list(
        dict.fromkeys(profile["selection"]["aliases"] + profile["expected"]["aliases"])
    )
    if aliases:
        result.append(
            {
                "name": "profile-aliases",
                "params": {
                    "query.intr": " OR ".join(f'"{alias}"' for alias in aliases)
                },
            }
        )
    ids = profile["selection"]["include_ids"]
    if ids:
        result.append(
            {
                "name": "profile-include-ids",
                "params": {"query.term": "AREA[NCTId](" + " OR ".join(ids) + ")"},
            }
        )
    return result
