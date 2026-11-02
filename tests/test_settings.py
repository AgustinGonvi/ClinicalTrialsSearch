import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from clinical_trials_search.settings import (
    load_settings,
    require_external,
    save_settings,
)


class SettingsTests(unittest.TestCase):
    def test_installed_package_cannot_write_into_a_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            repo = root / "checkout"
            (repo / "src/clinical_trials_search").mkdir(parents=True)
            (repo / "pyproject.toml").touch()
            with patch(
                "clinical_trials_search.settings.source_repository", return_value=None
            ):
                with self.assertRaisesRegex(ValueError, "outside"):
                    require_external(repo / "runs")
                with self.assertRaisesRegex(ValueError, "outside"):
                    save_settings(repo / "config.json", {"output_root": "../output"})
                self.assertEqual(require_external(root / "output"), root / "output")

    def test_relative_config_paths_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            config = root / "local.json"
            save_settings(config, {"output_root": "output", "r_library": "r-library"})
            with patch.dict("os.environ", {}, clear=True):
                values = load_settings(config)
                self.assertEqual(values["output_root"], str(root / "output"))
                self.assertEqual(values["r_library"], str(root / "r-library"))
            with patch.dict("os.environ", {"CTS_OUTPUT_ROOT": "override"}):
                self.assertEqual(load_settings(config)["output_root"], "override")
            with self.assertRaisesRegex(ValueError, "already exists"):
                save_settings(config, {})


if __name__ == "__main__":
    unittest.main()
