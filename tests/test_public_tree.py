"""Publication checks use constructed synthetic leaks, never real local data."""

import importlib.util
import unittest
from pathlib import Path

CHECKER = Path(__file__).resolve().parents[1] / "tools" / "check_public_tree.py"
SPEC = importlib.util.spec_from_file_location("public_tree_check", CHECKER)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class PublicTreeTests(unittest.TestCase):
    def test_common_leaks_report_rule_and_line_without_value(self):
        slash = chr(92)
        samples = {
            "local-drive-path": "Q" + chr(58) + slash + "research",
            "network-path": slash * 2 + "server" + slash + "share",
            "user-home-path": "/".join(("", "home", "researcher", "project")),
            "email-address": "contact" + chr(64) + "example.invalid",
            "github-token": "ghp_" + "a" * 24,
            "api-token": "sk-" + "a" * 30,
            "cloud-access-key": "AKIA" + "A" * 16,
            "private-key": "-----BEGIN " + "PRIVATE KEY-----",
            "credential-assignment": "password" + " = " + repr("synthetic-placeholder"),
        }
        for rule, value in samples.items():
            with self.subTest(rule=rule):
                issues = CHECK.findings("example.txt", ("safe line\n" + value).encode())
                self.assertIn((2, rule), issues)
                self.assertNotIn(value, repr(issues))

    def test_safe_urls_and_checker_source_do_not_trigger(self):
        self.assertEqual(
            CHECK.findings(
                "README.md", b"https://clinicaltrials.gov/study/NCT00000001"
            ),
            [],
        )
        self.assertEqual(
            CHECK.findings("tools/check_public_tree.py", CHECKER.read_bytes()), []
        )
        self.assertEqual(
            CHECK.findings("tests/test_public_tree.py", Path(__file__).read_bytes()), []
        )

    def test_runtime_artifacts_are_rejected_but_synthetic_fixtures_are_allowed(self):
        for name in (
            "records/study.json",
            "runs/report.md",
            "output/report.csv",
            "bridge.sqlite-wal",
            ".venv/config.txt",
        ):
            with self.subTest(name=name):
                self.assertIn(
                    (1, "tracked-runtime-artifact"), CHECK.findings(name, b"{}")
                )
        self.assertEqual(CHECK.findings("tests/fixtures/synthetic.json", b"{}"), [])

    def test_generic_email_exception_is_limited_to_metadata(self):
        value = CHECK.GENERIC_EMAIL.encode()
        self.assertEqual(CHECK.findings("CITATION.cff", value), [])
        self.assertIn((1, "email-address"), CHECK.findings("README.md", value))

    def test_binary_or_non_utf8_content_requires_review(self):
        self.assertIn(
            (1, "unreviewed-binary-file"), CHECK.findings("unknown.bin", b"\0")
        )
        self.assertIn((1, "non-utf8-file"), CHECK.findings("unknown.txt", bytes([255])))


if __name__ == "__main__":
    unittest.main()
