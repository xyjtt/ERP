from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any


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
    if not webhook:
        print("[WARN] DingTalk notification is enabled but webhook is empty.")
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
            response.read()
        return True
    except Exception as exc:
        print(f"[WARN] DingTalk notification failed ({type(exc).__name__}).")
        return False
