"""Check tracked files for common publication leaks; never print matched values."""

import re
import subprocess
import sys
from pathlib import Path

RULES = {
    "local-drive-path": re.compile(r"\b[A-Za-z]:[\\/]"),
    "network-path": re.compile(r"\\\\[A-Za-z0-9_.-]+\\[^\\\s]+"),
    "user-home-path": re.compile(r"/(?:Users|home)/[^/\s]+"),
    "email-address": re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    "github-token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
    ),
    "api-token": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    "cloud-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private-key": re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    "credential-assignment": re.compile(
        r"(?:api[_-]?key|secret|password|access[_-]?token)\s*[:=]\s*[\"'][^\"'\r\n]{8,}[\"']",
        re.I,
    ),
}
FORBIDDEN_PARTS = {
    ".venv",
    "venv",
    "__pycache__",
    ".cache",
    "cache",
    "runs",
    "output",
    "outputs",
    "data",
    "records",
    "downloads",
    "work",
    "tmp",
    "r-library",
}
FORBIDDEN_SUFFIX = re.compile(r"\.(?:sqlite3?|db)(?:-.*)?$|\.(?:py[co]|log)$", re.I)
METADATA = {"CITATION.cff", "pyproject.toml", "LICENSE"}
GENERIC_EMAIL = "contributors" + "@" + "example.invalid"


def findings(name: str, data: bytes) -> list[tuple[int, str]]:
    """Return line numbers and rule names without exposing suspect content."""
    problems = []
    if any(
        part.lower() in FORBIDDEN_PARTS for part in Path(name).parts
    ) or FORBIDDEN_SUFFIX.search(name):
        problems.append((1, "tracked-runtime-artifact"))
    if b"\x00" in data:
        return problems + [(1, "unreviewed-binary-file")]
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return problems + [(1, "non-utf8-file")]
    for number, line in enumerate(content.splitlines(), 1):
        for rule, pattern in RULES.items():
            matches = list(pattern.finditer(line))
            if rule == "email-address" and name in METADATA:
                matches = [match for match in matches if match.group() != GENERIC_EMAIL]
            if matches:
                problems.append((number, rule))
    return problems


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
        ).stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
        print(
            "Cannot inspect tracked files; run this check from a Git checkout.",
            file=sys.stderr,
        )
        return 2
    names = [name for name in listing.split("\0") if name]
    if not names:
        print(
            "No tracked files; stage the intended repository contents first.",
            file=sys.stderr,
        )
        return 2
    failures = []
    for name in names:
        path = root / name
        try:
            issues = (
                [(1, "tracked-symlink")]
                if path.is_symlink()
                else findings(name, path.read_bytes())
            )
        except OSError:
            issues = [(1, "unreadable-file")]
        failures.extend(f"{name}:{line}: {rule}" for line, rule in issues)
    if failures:
        print("\n".join(failures))
        return 1
    print(f"PASS: scanned {len(names)} tracked files for common publication leaks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
