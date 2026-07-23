from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any


_DINGTALK_WEBHOOK_HOST = "oapi.dingtalk.com"
_DINGTALK_WEBHOOK_PATH = "/robot/send"
_DINGTALK_SECRET_PATTERN = re.compile(r"^SEC[A-Za-z0-9]{32,}$")
_PLACEHOLDER_MARKERS = (
    "<",
    ">",
    "changeme",
    "dummy",
    "example",
    "placeholder",
    "replace_me",
    "your_",
)


def _looks_like_placeholder(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    return not lowered or any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def validate_dingtalk_credentials(webhook: str, secret: str) -> bool:
    base_webhook = str(webhook or "").strip()
    signing_secret = str(secret or "").strip()
    if _looks_like_placeholder(base_webhook) or _looks_like_placeholder(signing_secret):
        return False
    if not _DINGTALK_SECRET_PATTERN.fullmatch(signing_secret):
        return False

    try:
        parsed = urllib.parse.urlsplit(base_webhook)
    except ValueError:
        return False
    if parsed.scheme.lower() != "https":
        return False
    if (parsed.hostname or "").lower() != _DINGTALK_WEBHOOK_HOST:
        return False
    if parsed.port not in (None, 443):
        return False
    if parsed.username or parsed.password or parsed.fragment:
        return False
    if parsed.path.rstrip("/") != _DINGTALK_WEBHOOK_PATH:
        return False

    access_tokens = urllib.parse.parse_qs(parsed.query, keep_blank_values=True).get(
        "access_token",
        [],
    )
    if len(access_tokens) != 1:
        return False
    access_token = str(access_tokens[0]).strip()
    return len(access_token) >= 16 and not _looks_like_placeholder(access_token)


def resolve_dingtalk_credentials(dingtalk: dict[str, Any]) -> tuple[str, str]:
    webhook_env = str(dingtalk.get("webhook_env", "DINGTALK_WEBHOOK")).strip()
    secret_env = str(dingtalk.get("secret_env", "DINGTALK_SECRET")).strip()
    webhook = str(os.environ.get(webhook_env, "") if webhook_env else "").strip()
    secret = str(os.environ.get(secret_env, "") if secret_env else "").strip()
    if not webhook:
        webhook = str(dingtalk.get("webhook", "")).strip()
    if not secret:
        secret = str(dingtalk.get("secret", "")).strip()
    return webhook, secret


def build_signed_webhook(webhook: str, secret: str) -> str:
    base_webhook = str(webhook or "").strip()
    signing_secret = str(secret or "").strip()
    if not base_webhook or not signing_secret:
        return base_webhook

    timestamp = str(int(datetime.now().timestamp() * 1000))
    string_to_sign = f"{timestamp}\n{signing_secret}"
    signature = base64.b64encode(
        hmac.new(
            signing_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
    ).decode("utf-8")
    encoded_signature = urllib.parse.quote_plus(signature)
    connector = "&" if "?" in base_webhook else "?"
    return f"{base_webhook}{connector}timestamp={timestamp}&sign={encoded_signature}"


def post_dingtalk_text_message(system_config: dict[str, Any], content: str) -> bool:
    dingtalk = dict(system_config.get("notifications", {}).get("dingtalk", {}))
    if not dingtalk.get("enabled", False):
        return False

    webhook, secret = resolve_dingtalk_credentials(dingtalk)
    if not validate_dingtalk_credentials(webhook, secret):
        print("[WARN] DingTalk notification credentials are missing or invalid.")
        return False

    request_url = build_signed_webhook(webhook, secret)
    payload = json.dumps(
        {
            "msgtype": "text",
            "text": {
                "content": str(content or "").strip(),
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        request_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response_body = response.read()
        try:
            response_payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            print("[WARN] DingTalk notification returned an invalid response.")
            return False
        if not isinstance(response_payload, dict) or response_payload.get("errcode") != 0:
            print("[WARN] DingTalk notification was rejected by the API.")
            return False
        return True
    except Exception as exc:
        print(f"[WARN] DingTalk notification failed ({type(exc).__name__}).")
        return False
