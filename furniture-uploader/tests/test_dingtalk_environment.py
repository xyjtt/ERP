from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from dingtalk import resolve_dingtalk_credentials


class DingTalkEnvironmentTests(unittest.TestCase):
    def test_environment_credentials_override_config_values(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TEST_DINGTALK_WEBHOOK": "https://example.invalid/from-env",
                "TEST_DINGTALK_SECRET": "env-secret",
            },
        ):
            webhook, secret = resolve_dingtalk_credentials(
                {
                    "webhook_env": "TEST_DINGTALK_WEBHOOK",
                    "secret_env": "TEST_DINGTALK_SECRET",
                    "webhook": "https://example.invalid/from-config",
                    "secret": "config-secret",
                }
            )

        self.assertEqual(webhook, "https://example.invalid/from-env")
        self.assertEqual(secret, "env-secret")


if __name__ == "__main__":
    unittest.main()
