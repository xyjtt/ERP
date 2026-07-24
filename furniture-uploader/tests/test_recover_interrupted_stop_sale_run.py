from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from recover_interrupted_stop_sale_run import (  # noqa: E402
    _load_owned_interrupted_lock,
    build_argument_parser,
)


class RecoverInterruptedStopSaleRunTests(unittest.TestCase):
    def test_accepts_matching_stop_sale_lock_with_dead_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "shared.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "pid": 12345,
                        "token": "token-1",
                        "metadata": {
                            "cycle": "1688_stop_sale_pipeline",
                            "run_id": "RUN-1",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch("recover_interrupted_stop_sale_run._is_process_running", return_value=False):
                payload = _load_owned_interrupted_lock(lock_path, "RUN-1")

        self.assertEqual(payload["token"], "token-1")

    def test_rejects_lock_for_different_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "shared.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "pid": 12345,
                        "metadata": {
                            "cycle": "1688_stop_sale_pipeline",
                            "run_id": "RUN-2",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "run_id"):
                _load_owned_interrupted_lock(lock_path, "RUN-1")

    def test_rejects_live_lock_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "shared.lock"
            lock_path.write_text(
                json.dumps(
                    {
                        "pid": 12345,
                        "metadata": {
                            "cycle": "1688_stop_sale_pipeline",
                            "run_id": "RUN-1",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch("recover_interrupted_stop_sale_run._is_process_running", return_value=True):
                with self.assertRaisesRegex(RuntimeError, "still active"):
                    _load_owned_interrupted_lock(lock_path, "RUN-1")

    def test_parser_requires_explicit_confirmation_at_runtime(self) -> None:
        args = build_argument_parser().parse_args(
            [
                "--run-id",
                "RUN-1",
                "--shared-runtime-root",
                "E:/runtime",
                "--shared-lock-path",
                "E:/runtime/lock",
                "--reason",
                "executor stopped",
            ]
        )

        self.assertFalse(args.yes)


if __name__ == "__main__":
    unittest.main()
