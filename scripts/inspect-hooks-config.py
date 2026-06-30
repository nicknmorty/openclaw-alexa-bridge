#!/usr/bin/env python3
"""Summarize OpenClaw hook config without printing secrets."""

from __future__ import annotations

import json
import os
import sys
from typing import Any


WEBHOOK_KEYS = (
    "enabled",
    "path",
    "token",
    "allowedAgentIds",
    "defaultSessionKey",
    "allowedSessionKeyPrefixes",
    "allowRequestSessionKey",
    "maxBodyBytes",
)


def describe_token(token: Any) -> str:
    source = os.environ.get("OPENCLAW_ALEXA_HOOKS_SOURCE", "").strip().lower()
    if token is None:
        return "missing"
    if isinstance(token, dict):
        source = token.get("source")
        token_id = token.get("id")
        if source == "env" and isinstance(token_id, str) and token_id:
            return f"env reference ({token_id})"
        return "structured reference"
    if isinstance(token, str):
        stripped = token.strip()
        if not stripped:
            return "missing"
        if stripped.startswith("${") and stripped.endswith("}"):
            return f"env reference ({stripped[2:-1]})"
        if source == "resolved":
            return "value present in resolved config output"
        return "literal value present"
    return f"unexpected type ({type(token).__name__})"


def main() -> int:
    raw = os.environ.get("OPENCLAW_ALEXA_HOOKS_JSON", "").strip()
    if not raw:
        print("hooks config: unavailable")
        return 1

    try:
        hooks = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"hooks config: invalid JSON: {exc}", file=sys.stderr)
        return 1

    if not isinstance(hooks, dict):
        print("hooks config: unexpected non-object value")
        return 1

    internal = hooks.get("internal")
    if isinstance(internal, dict):
        entries = internal.get("entries")
        entry_count = len(entries) if isinstance(entries, dict) else 0
        print(f"hooks.internal: present ({entry_count} entries)")
    else:
        print("hooks.internal: not configured")

    configured = [key for key in WEBHOOK_KEYS if key in hooks]
    if configured:
        print(f"webhook keys: {', '.join(configured)}")
    else:
        print("webhook keys: not configured")

    print(f"hooks.enabled: {hooks.get('enabled', 'missing')}")
    print(f"hooks.path: {hooks.get('path', 'missing')}")
    print(f"hooks.token: {describe_token(hooks.get('token'))}")
    print(f"hooks.allowedAgentIds: {hooks.get('allowedAgentIds', 'missing')}")
    print(f"hooks.defaultSessionKey: {hooks.get('defaultSessionKey', 'missing')}")
    print(f"hooks.allowRequestSessionKey: {hooks.get('allowRequestSessionKey', 'missing')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
