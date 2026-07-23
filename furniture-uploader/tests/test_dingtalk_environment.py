from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RPA_ROOT = PROJECT_ROOT / "rpa"
if str(RPA_ROOT) not in sys.path:
    sys.path.insert(0, str(RPA_ROOT))

from dingtalk import (
    post_dingtalk_text_message,
    resolve_dingtalk_credentials,
    validate_dingtalk_credentials,
)


VALID_WEBHOOK = (
    "https://oapi.dingtalk.com/robot/send?access_token=" + "a" * 64
)
VALID_SECRET = "SEC" + "b" * 64


class DingTalkEnvironmentTests(unittest.TestCase):
    def test_environment_credentials_override_config_values(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TEST_DINGTALK_WEBHOOK": VALID_WEBHOOK,
                "TEST_DINGTALK_SECRET": VALID_SECRET,
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

        self.assertEqual(webhook, VALID_WEBHOOK)
        self.assertEqual(secret, VALID_SECRET)

    def test_valid_dingtalk_pair_is_accepted(self) -> None:
        self.assertTrue(validate_dingtalk_credentials(VALID_WEBHOOK, VALID_SECRET))

    def test_placeholder_and_malformed_pairs_are_rejected(self) -> None:
        invalid_pairs = (
            ("https://example.invalid/robot/send?access_token=placeholder", "SEC" + "b" * 64),
            ("http://oapi.dingtalk.com/robot/send?access_token=" + "a" * 64, VALID_SECRET),
            ("https://oapi.dingtalk.com/not-robot?access_token=" + "a" * 64, VALID_SECRET),
            (VALID_WEBHOOK, "placeholder-secret"),
            (VALID_WEBHOOK, "not-a-sec-secret"),
        )

        for webhook, secret in invalid_pairs:
            with self.subTest(webhook=webhook[:40], secret_prefix=secret[:3]):
                self.assertFalse(validate_dingtalk_credentials(webhook, secret))

    def test_http_200_with_success_errcode_returns_true(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"errcode": 0, "errmsg": "ok"}'
        config = {"notifications": {"dingtalk": {"enabled": True}}}

        with (
            patch.dict(
                os.environ,
                {"DINGTALK_WEBHOOK": VALID_WEBHOOK, "DINGTALK_SECRET": VALID_SECRET},
                clear=True,
            ),
            patch("dingtalk.urllib.request.urlopen", return_value=response),
        ):
            sent = post_dingtalk_text_message(config, "acceptance probe")

        self.assertTrue(sent)

    def test_http_200_with_business_error_returns_false(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'{"errcode": 310000, "errmsg": "invalid token"}'
        )
        config = {"notifications": {"dingtalk": {"enabled": True}}}

        with (
            patch.dict(
                os.environ,
                {"DINGTALK_WEBHOOK": VALID_WEBHOOK, "DINGTALK_SECRET": VALID_SECRET},
                clear=True,
            ),
            patch("dingtalk.urllib.request.urlopen", return_value=response),
        ):
            sent = post_dingtalk_text_message(config, "acceptance probe")

        self.assertFalse(sent)


if __name__ == "__main__":
    unittest.main()
