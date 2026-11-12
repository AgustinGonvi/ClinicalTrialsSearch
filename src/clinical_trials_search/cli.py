"""Command-line interface; machine-specific settings never enter a profile."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .pipeline import RunFailed, compare_runs, run_search
from .profiles import load_profile, query_plan
from .retrieval import RetrievalError, count_native
from .settings import load_settings, require_external, save_settings


def doctor(settings):
    """Check local dependencies without contacting a registry or installing tools."""
    info = {
        "tool_version": __version__,
        "python_version": sys.version.split()[0],
        "native": {"available": True},
        "output_configured": bool(settings.get("output_root")),
        "ctrdata": {"available": False},
    }
    executable = settings.get("rscript", "Rscript")
    if Path(executable).is_file() or shutil.which(executable):
        env = os.environ.copy()
        if settings.get("r_library"):
            env["R_LIBS_USER"] = settings["r_library"]
        expression = 'if (all(vapply(c("ctrdata", "nodbi", "RSQLite", "DBI", "jsonlite", "httr2"), requireNamespace, logical(1), quietly=TRUE))) {if (packageVersion("ctrdata") < "1.26.3") quit(status=3); cat(as.character(packageVersion("ctrdata")))} else {quit(status=2)}'
        try:
            result = subprocess.run(
                [executable, "--vanilla", "-e", expression],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                check=False,
            )
            if result.returncode == 0:
                info["ctrdata"] = {"available": True, "version": result.stdout.strip()}
            else:
                info["ctrdata"]["reason"] = (
                    "R is available; optional packages are missing or ctrdata is older than 1.26.3"
                )
        except (OSError, subprocess.TimeoutExpired):
            info["ctrdata"]["reason"] = "R dependency check could not finish"
    else:
        info["ctrdata"]["reason"] = "Rscript is not configured or on PATH"
    return info


def _progress(message):
    """Status goes to stderr so stdout stays a single JSON document."""
    print(message, file=sys.stderr, flush=True)


def parser():
    result = argparse.ArgumentParser(
        prog="cts",
        description="Reproducible clinical trial searches with kidney-focused exports",
    )
    result.add_argument("--version", action="version", version=__version__)
    commands = result.add_subparsers(dest="command", required=True)
    validate = commands.add_parser(
        "validate-profile", help="Validate rules and display the exact query plan"
    )
    validate.add_argument("profile", type=Path)
    count = commands.add_parser(
        "count", help="Ask the registry how many studies each query matches"
    )
    count.add_argument("profile", type=Path)
    run = commands.add_parser("run", help="Retrieve and analyze a new dated snapshot")
    run.add_argument("--profile", required=True, type=Path)
    run.add_argument("--name")
    run.add_argument(
        "--backend", choices=("native", "ctrdata", "auto"), default="native"
    )
    run.add_argument("--output-root", type=Path)
    run.add_argument("--baseline", type=Path)
    run.add_argument("--reviews", type=Path)
    run.add_argument("--config", type=Path)
    run.add_argument(
        "--quiet", action="store_true", help="Do not print progress lines to stderr"
    )
    compare = commands.add_parser(
        "compare", help="Compare complete snapshots, checking source-record hashes"
    )
    compare.add_argument("old_run", type=Path)
    compare.add_argument("new_run", type=Path)
    compare.add_argument("--output", required=True, type=Path)
    configure = commands.add_parser(
        "configure", help="Create settings outside the repository"
    )
    configure.add_argument("--config", required=True, type=Path)
    configure.add_argument("--output-root", required=True, type=Path)
    configure.add_argument("--rscript")
    configure.add_argument("--r-library", type=Path)
    check = commands.add_parser("doctor", help="Check local dependencies (no network)")
    check.add_argument("--config", type=Path)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "validate-profile":
            profile = load_profile(args.profile)
            print(
                json.dumps(
                    {
                        "valid": True,
                        "name": profile["name"],
                        "queries": query_plan(profile),
                    },
                    indent=2,
                )
            )
        elif args.command == "count":
            profile = load_profile(args.profile)
            result = count_native(query_plan(profile))
            print(
                json.dumps(
                    {
                        "name": profile["name"],
                        "registry_version": result["version"],
                        "queries": result["queries"],
                        "sum_of_query_totals": sum(
                            query["total_count"] for query in result["queries"]
                        ),
                        "note": "Totals are per query. Overlapping queries retrieve "
                        "fewer unique records than this sum. Nothing was saved.",
                    },
                    indent=2,
                )
            )
        elif args.command == "configure":
            values = {"output_root": str(require_external(args.output_root))}
            if args.rscript:
                values["rscript"] = (
                    str(Path(args.rscript).expanduser().resolve())
                    if "/" in args.rscript or "\\" in args.rscript
                    else args.rscript
                )
            if args.r_library:
                values["r_library"] = str(require_external(args.r_library))
            print(f"Saved local settings: {save_settings(args.config, values)}")
        elif args.command == "doctor":
            print(json.dumps(doctor(load_settings(args.config)), indent=2))
        elif args.command == "compare":
            result = compare_runs(args.old_run, args.new_run, args.output)
            print(
                json.dumps(
                    {
                        "output": str(args.output),
                        "records_compared": len(result["changes"]),
                        "same_profile": result["same_profile"],
                    },
                    indent=2,
                )
            )
        elif args.command == "run":
            settings = load_settings(args.config)
            output = args.output_root or settings.get("output_root")
            if not output:
                raise ValueError(
                    "Set --output-root, CTS_OUTPUT_ROOT, or output_root in external --config/CTS_CONFIG"
                )
            location, manifest = run_search(
                args.profile,
                output,
                name=args.name,
                backend=args.backend,
                baseline=args.baseline,
                reviews_path=args.reviews,
                settings=settings,
                progress=None if args.quiet else _progress,
            )
            print(
                json.dumps(
                    {
                        "status": manifest["status"],
                        "output": str(location),
                        "backend": manifest["backend"],
                        "counts": manifest["counts"],
                        "warnings": manifest["warnings"],
                    },
                    indent=2,
                )
            )
        return 0
    except RunFailed as error:
        print(
            f"Run failed: {error}\nDiagnostic snapshot retained at: {error.path}",
            file=sys.stderr,
        )
        return 1
    except RetrievalError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "Interrupted. A run that had started is marked failed in its "
            ".partial directory.",
            file=sys.stderr,
        )
        return 130
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
