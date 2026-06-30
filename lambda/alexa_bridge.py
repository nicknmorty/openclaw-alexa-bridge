"""Pure helpers for the OpenClaw Alexa Lambda bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re
import socket
from typing import Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

MAX_QUERY_CHARS = 4096
MAX_SPEECH_CHARS = 7800
DEFAULT_REQUEST_TIMEOUT_SECONDS = 4.0
MAX_REQUEST_TIMEOUT_SECONDS = 7.0
DEFAULT_VISIBLE_REPLY_PREFIX = "Alexa -> OpenClaw:"
DEFAULT_RESPONSE_MODE = "telegram"
RESPONSE_MODES = {"telegram", "device"}


class BridgeConfigError(ValueError):
    """Raised when required Lambda environment is missing or invalid."""


class OpenClawHookError(RuntimeError):
    """Raised when the OpenClaw hook cannot be reached or rejects the request."""

    def __init__(self, message: str, *, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class BridgeConfig:
    openclaw_hook_url: str
    openclaw_hook_token: str = field(repr=False)
    delivery_channel: str = "telegram"
    delivery_target: Optional[str] = None
    skill_name: str = "Alexa"
    response_mode: str = DEFAULT_RESPONSE_MODE
    device_response_url: Optional[str] = None
    visible_reply_prefix: Optional[str] = DEFAULT_VISIBLE_REPLY_PREFIX
    agent_id: Optional[str] = None
    session_key: Optional[str] = None
    wake_mode: str = "now"
    deliver: bool = True
    agent_timeout_seconds: Optional[int] = 600
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS


def _clean_optional(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _read_bool(value: Optional[str], *, default: bool) -> bool:
    cleaned = _clean_optional(value)
    if cleaned is None:
        return default
    lowered = cleaned.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise BridgeConfigError(f"expected boolean value, got {cleaned!r}")


def _read_positive_int(value: Optional[str], *, default: Optional[int]) -> Optional[int]:
    cleaned = _clean_optional(value)
    if cleaned is None:
        return default
    try:
        parsed = int(cleaned)
    except ValueError as exc:
        raise BridgeConfigError(f"expected positive integer, got {cleaned!r}") from exc
    if parsed <= 0:
        raise BridgeConfigError(f"expected positive integer, got {cleaned!r}")
    return parsed


def _read_positive_float(value: Optional[str], *, default: float) -> float:
    cleaned = _clean_optional(value)
    if cleaned is None:
        return default
    try:
        parsed = float(cleaned)
    except ValueError as exc:
        raise BridgeConfigError(f"expected positive number, got {cleaned!r}") from exc
    if parsed <= 0:
        raise BridgeConfigError(f"expected positive number, got {cleaned!r}")
    return parsed


def _validate_hook_url(value: str) -> str:
    return _validate_https_url(value, expected_path="/hooks/agent", env_name="OPENCLAW_HOOK_URL")


def _validate_device_response_url(value: str) -> str:
    return _validate_https_url(
        value,
        expected_path="/alexa/respond",
        env_name="OPENCLAW_DEVICE_RESPONSE_URL",
    )


def _validate_https_url(value: str, *, expected_path: str, env_name: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise BridgeConfigError(f"{env_name} must be an https URL")
    if parsed.username or parsed.password:
        raise BridgeConfigError(f"{env_name} must not include userinfo")
    if parsed.query or parsed.fragment:
        raise BridgeConfigError(f"{env_name} must not include query or fragment")
    if parsed.path.rstrip("/") != expected_path:
        raise BridgeConfigError(f"{env_name} must end in {expected_path}")
    return value.rstrip("/")


def load_config(env: Mapping[str, str] = os.environ) -> BridgeConfig:
    hook_url = _clean_optional(env.get("OPENCLAW_HOOK_URL"))
    hook_token = _clean_optional(env.get("OPENCLAW_HOOK_TOKEN"))
    if not hook_url:
        raise BridgeConfigError("OPENCLAW_HOOK_URL is required")
    if not hook_token:
        raise BridgeConfigError("OPENCLAW_HOOK_TOKEN is required")
    hook_url = _validate_hook_url(hook_url)

    wake_mode = _clean_optional(env.get("OPENCLAW_WAKE_MODE")) or "now"
    if wake_mode not in {"now", "next-heartbeat"}:
        raise BridgeConfigError("OPENCLAW_WAKE_MODE must be now or next-heartbeat")
    request_timeout_seconds = _read_positive_float(
        env.get("OPENCLAW_REQUEST_TIMEOUT_SECONDS"),
        default=DEFAULT_REQUEST_TIMEOUT_SECONDS,
    )
    if request_timeout_seconds > MAX_REQUEST_TIMEOUT_SECONDS:
        raise BridgeConfigError(
            f"OPENCLAW_REQUEST_TIMEOUT_SECONDS must be <= {MAX_REQUEST_TIMEOUT_SECONDS:g}",
        )
    response_mode = (_clean_optional(env.get("OPENCLAW_RESPONSE_MODE")) or DEFAULT_RESPONSE_MODE).lower()
    if response_mode not in RESPONSE_MODES:
        raise BridgeConfigError(
            f"OPENCLAW_RESPONSE_MODE must be one of {', '.join(sorted(RESPONSE_MODES))}",
        )
    device_response_url = _clean_optional(env.get("OPENCLAW_DEVICE_RESPONSE_URL"))
    if device_response_url:
        device_response_url = _validate_device_response_url(device_response_url)
    if response_mode == "device" and not device_response_url:
        raise BridgeConfigError(
            "OPENCLAW_DEVICE_RESPONSE_URL is required when OPENCLAW_RESPONSE_MODE=device",
        )

    return BridgeConfig(
        openclaw_hook_url=hook_url,
        openclaw_hook_token=hook_token,
        delivery_channel=_clean_optional(env.get("OPENCLAW_DELIVERY_CHANNEL")) or "telegram",
        delivery_target=_clean_optional(env.get("OPENCLAW_DELIVERY_TARGET")),
        skill_name=_clean_optional(env.get("OPENCLAW_SKILL_NAME")) or "Alexa",
        response_mode=response_mode,
        device_response_url=device_response_url,
        visible_reply_prefix=_clean_optional(
            env.get("OPENCLAW_VISIBLE_REPLY_PREFIX"),
        )
        if env.get("OPENCLAW_VISIBLE_REPLY_PREFIX") is not None
        else DEFAULT_VISIBLE_REPLY_PREFIX,
        agent_id=_clean_optional(env.get("OPENCLAW_AGENT_ID")),
        session_key=_clean_optional(env.get("OPENCLAW_SESSION_KEY")),
        wake_mode=wake_mode,
        deliver=_read_bool(env.get("OPENCLAW_DELIVER"), default=True),
        agent_timeout_seconds=_read_positive_int(
            env.get("OPENCLAW_AGENT_TIMEOUT_SECONDS"),
            default=600,
        ),
        request_timeout_seconds=request_timeout_seconds,
    )


def normalize_query(query: Optional[str]) -> str:
    cleaned = re.sub(r"\s+", " ", query or "").strip()
    if not cleaned:
        raise ValueError("query required")
    if len(cleaned) > MAX_QUERY_CHARS:
        return cleaned[:MAX_QUERY_CHARS].rstrip()
    return cleaned


def build_agent_message(query: str, config: BridgeConfig) -> str:
    message = normalize_query(query)
    prefix = _clean_optional(config.visible_reply_prefix)
    if not prefix:
        return message
    return (
        "Voice request via Alexa. "
        f"When producing a visible async reply, start your answer with {prefix} "
        f"Request: {message}"
    )


def build_device_agent_message(query: str, config: BridgeConfig) -> str:
    message = normalize_query(query)
    return (
        "Voice request via Alexa. Reply with text Alexa can speak aloud. "
        "Keep the answer concise, direct, and natural. Do not include Markdown, "
        "code fences, citations, or a Telegram prefix unless the user explicitly asks. "
        f"Request: {message}"
    )


def build_openclaw_payload(
    query: str,
    config: BridgeConfig,
    *,
    request_id: Optional[str] = None,
) -> dict:
    message = build_agent_message(query, config)
    payload = {
        "message": message,
        "name": config.skill_name,
        "wakeMode": config.wake_mode,
        "deliver": config.deliver,
        "channel": config.delivery_channel,
    }
    if request_id:
        payload["idempotencyKey"] = request_id
    if config.delivery_target:
        payload["to"] = config.delivery_target
    if config.agent_id:
        payload["agentId"] = config.agent_id
    if config.session_key:
        payload["sessionKey"] = config.session_key
    if config.agent_timeout_seconds:
        payload["timeoutSeconds"] = config.agent_timeout_seconds
    return payload


def build_device_response_payload(
    query: str,
    config: BridgeConfig,
    *,
    request_id: Optional[str] = None,
) -> dict:
    payload = {
        "message": build_device_agent_message(query, config),
        "name": config.skill_name,
        "fallbackPayload": build_openclaw_payload(query, config, request_id=request_id),
    }
    if request_id:
        payload["requestId"] = request_id
    return payload


def build_openclaw_headers(config: BridgeConfig, *, request_id: Optional[str] = None) -> dict:
    headers = {
        "Authorization": f"Bearer {config.openclaw_hook_token}",
        "Content-Type": "application/json",
    }
    if request_id:
        headers["Idempotency-Key"] = request_id
    return headers


def _post_json(url: str, payload: dict, config: BridgeConfig, *, request_id: Optional[str] = None) -> dict:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers=build_openclaw_headers(config, request_id=request_id),
        method="POST",
    )
    try:
        with urlopen(request, timeout=config.request_timeout_seconds) as response:
            raw = response.read(64 * 1024).decode("utf-8")
    except HTTPError as exc:
        raw = exc.read(4096).decode("utf-8", errors="replace")
        raise OpenClawHookError(
            f"OpenClaw hook returned HTTP {exc.code}: {raw}",
            status=exc.code,
        ) from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise OpenClawHookError(f"OpenClaw hook request failed: {exc}") from exc

    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise OpenClawHookError("OpenClaw hook returned non-JSON response") from exc
    if not isinstance(parsed, dict):
        raise OpenClawHookError("OpenClaw hook returned unexpected response")
    return parsed


def post_openclaw_hook(payload: dict, config: BridgeConfig, *, request_id: Optional[str] = None) -> dict:
    return _post_json(config.openclaw_hook_url, payload, config, request_id=request_id)


def post_device_response_request(
    payload: dict,
    config: BridgeConfig,
    *,
    request_id: Optional[str] = None,
) -> dict:
    if not config.device_response_url:
        raise BridgeConfigError("OPENCLAW_DEVICE_RESPONSE_URL is required")
    return _post_json(config.device_response_url, payload, config, request_id=request_id)


def alexa_speech_for_openclaw_result(result: Mapping[str, object]) -> str:
    if result.get("ok") is True:
        for key in ("speech", "response", "text"):
            text = result.get(key)
            if isinstance(text, str) and text.strip():
                cleaned = text.strip()
                if len(cleaned) <= MAX_SPEECH_CHARS:
                    return cleaned
                trimmed = cleaned[:MAX_SPEECH_CHARS].rsplit(" ", 1)[0].rstrip(".,;: ")
                return (trimmed or cleaned[:MAX_SPEECH_CHARS]).rstrip() + "..."
        if result.get("runId"):
            return "I sent that to OpenClaw. The assistant will reply when it is done."
        return "OpenClaw accepted the request."
    return "OpenClaw rejected that request."
