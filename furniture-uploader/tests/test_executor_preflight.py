from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from preflight_1688_stop_sale_executor import check_plaintext_env_file


class ExecutorPreflightTests(unittest.TestCase):
    def test_plaintext_secret_keys_are_detected_without_returning_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                f"{'JST_USERNAME'}=operator\n{'JST_PASSWORD'}=private-value\nHEADLESS=false\n",
                encoding="utf-8",
            )

            self.assertEqual(
                check_plaintext_env_file(env_path),
                ["JST_USERNAME", "JST_PASSWORD"],
            )


if __name__ == "__main__":
    unittest.main()
