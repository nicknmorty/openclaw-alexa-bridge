#!/usr/bin/env python3
"""Narrow Alexa proxy for hook forwarding and short spoken responses."""

from __future__ import annotations

import hmac
import http.server
import json
import os
import re
import socketserver
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18089
DEFAULT_GATEWAY_URL = "http://127.0.0.1:18789"
DEFAULT_OPENCLAW_CLI = "openclaw"
DEFAULT_BRAIN_PROVIDER = "openclaw"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5"
DEFAULT_OPENCLAW_INFER_MODEL = "openai/gpt-5.4-mini"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_BODY_BYTES = 65536
MAX_SPEECH_CHARS = 7800
DEVICE_RESPONSE_PATH = "/alexa/respond"
DEFAULT_SYNC_TIMEOUT_SECONDS = 1
DEFAULT_BRAIN_TIMEOUT_SECONDS = 5.0
DEFAULT_DEVICE_SESSION_KEY = "hook:alexa-device"
DEFAULT_TIME_ZONE = "UTC"
FALLBACK_SENTINEL = "FALLBACK_TO_ASYNC_AGENT"
ALLOWED_FORWARD_HEADERS = {
    "authorization",
    "content-type",
    "idempotency-key",
    "x-openclaw-token",
}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise SystemExit(f"{name} must be positive")
    return parsed


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a number") from exc
    if parsed <= 0:
        raise SystemExit(f"{name} must be positive")
    return parsed


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{name} must be a boolean")


def _clean_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _header_items(headers: http.client.HTTPMessage) -> Iterable[tuple[str, str]]:
    for key, value in headers.items():
        if key.lower() in ALLOWED_FORWARD_HEADERS:
            yield key, value


def _extract_response_text(agent_result: object) -> str | None:
    if not isinstance(agent_result, dict):
        return None
    result = agent_result.get("result")
    if isinstance(result, dict):
        meta = result.get("meta")
        if isinstance(meta, dict):
            for key in ("finalAssistantVisibleText", "finalAssistantRawText"):
                value = meta.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        payloads = result.get("payloads")
        if isinstance(payloads, list):
            for payload in payloads:
                if not isinstance(payload, dict):
                    continue
                value = payload.get("text")
                if isinstance(value, str) and value.strip():
                    return value.strip()
    for key in ("response", "text", "summary"):
        value = agent_result.get(key)
        if isinstance(value, str) and value.strip() and value.strip() != "completed":
            return value.strip()
    return None


def _cap_speech(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= MAX_SPEECH_CHARS:
        return cleaned
    trimmed = cleaned[:MAX_SPEECH_CHARS].rsplit(" ", 1)[0].rstrip(".,;: ")
    return (trimmed or cleaned[:MAX_SPEECH_CHARS]).rstrip() + "..."


def _request_text_from_agent_message(message: str) -> str:
    marker = "Request:"
    if marker in message:
        return message.rsplit(marker, 1)[1].strip()
    return message.strip()


def _strip_invocation_prefix(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = re.sub(r"^(?:please\s+)?(?:ask|tell)\s+", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(
        r"^(?:the\s+)?claw bridge(?:\s+(?:to|that|please))?\s+",
        "",
        cleaned,
        flags=re.I,
    ).strip()
    return re.sub(r"^(?:please\s+|to\s+)+", "", cleaned, flags=re.I).strip()


def _fast_local_response(message: str) -> str | None:
    request_text = _strip_invocation_prefix(_request_text_from_agent_message(message))

    say_match = re.match(r"^(?:say|repeat|echo)\s+(.{1,120})$", request_text, flags=re.I)
    if say_match:
        return say_match.group(1).strip()

    normalized = re.sub(r"[^a-z0-9 ]+", "", request_text.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if normalized in {
        "time",
        "tell me the time",
        "tell me what time it is",
        "what is the time",
        "what time is it",
        "what time it is",
        "whats the time",
    }:
        now = datetime.now(ZoneInfo(_clean_env("OPENCLAW_ALEXA_TIME_ZONE") or DEFAULT_TIME_ZONE))
        return f"It is {now.strftime('%-I:%M %p')}."

    return None


def _alexa_brain_system_prompt() -> str:
    timezone_name = _clean_env("OPENCLAW_ALEXA_TIME_ZONE") or DEFAULT_TIME_ZONE
    now = datetime.now(ZoneInfo(timezone_name))
    return (
        "You are a fast Alexa voice response helper. Answer the user's spoken request "
        "directly in one or two short, natural sentences for Alexa to speak aloud. "
        "No Markdown, bullets, code blocks, citations, or prefixes. "
        "If the request requires private files, system state, tools, sending messages, "
        "web browsing, code changes, purchases, or long-running work, reply exactly "
        f"{FALLBACK_SENTINEL}. "
        f"Current configured local time is {now.strftime('%-I:%M %p %Z on %A, %B %-d, %Y')}."
    )


def _extract_chat_completion_text(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    text = first.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    return None


def _extract_anthropic_message_text(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts).strip() or None


def _extract_infer_model_text(payload: object) -> str | None:
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return None
    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        return None
    for item in outputs:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def _is_fallback_sentinel(text: str) -> bool:
    normalized = re.sub(r"[^A-Z_]+", "", text.upper())
    return normalized == FALLBACK_SENTINEL


def _configured_brain_model(provider: str) -> str:
    model = _clean_env("OPENCLAW_ALEXA_BRAIN_MODEL")
    if provider in {"anthropic", "claude"}:
        if not model or model.startswith(("openai/", "openrouter/")):
            return DEFAULT_ANTHROPIC_MODEL
        return model
    if provider in {"openai", "gpt", "openclaw", "openclaw-infer"}:
        if not model or model.startswith(("claude", "anthropic/", "openrouter/")):
            return DEFAULT_OPENCLAW_INFER_MODEL
        return model
    if provider == "openrouter":
        return model or DEFAULT_OPENROUTER_MODEL
    return model or DEFAULT_ANTHROPIC_MODEL


def _anthropic_auth_headers() -> dict[str, str] | None:
    api_key = _clean_env("ANTHROPIC_API_KEY")
    if api_key:
        return {"x-api-key": api_key}
    oauth_token = _clean_env("ANTHROPIC_OAUTH_TOKEN") or _clean_env("ANTHROPIC_AUTH_TOKEN")
    if oauth_token:
        return {"Authorization": f"Bearer {oauth_token}"}
    return None


def _anthropic_brain_response(request_text: str) -> str | None:
    auth_headers = _anthropic_auth_headers()
    if not auth_headers:
        return None
    body = json.dumps(
        {
            "model": _configured_brain_model("anthropic"),
            "system": _alexa_brain_system_prompt(),
            "messages": [{"role": "user", "content": request_text}],
            "temperature": 0.2,
            "max_tokens": 96,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        ANTHROPIC_MESSAGES_URL,
        data=body,
        headers={
            **auth_headers,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=_env_float("OPENCLAW_ALEXA_BRAIN_TIMEOUT_SECONDS", DEFAULT_BRAIN_TIMEOUT_SECONDS),
        ) as response:
            raw = response.read(MAX_BODY_BYTES).decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None
    try:
        text = _extract_anthropic_message_text(json.loads(raw or "{}"))
    except json.JSONDecodeError:
        return None
    if not text or _is_fallback_sentinel(text):
        return None
    return text


def _openclaw_infer_brain_response(request_text: str) -> str | None:
    openclaw_cli = _clean_env("OPENCLAW_CLI") or DEFAULT_OPENCLAW_CLI
    prompt = f"{_alexa_brain_system_prompt()}\n\nUser request: {request_text}"
    cmd = [
        openclaw_cli,
        "infer",
        "model",
        "run",
        "--gateway",
        "--model",
        _configured_brain_model("openai"),
        "--thinking",
        _clean_env("OPENCLAW_ALEXA_RESPONSE_THINKING") or "minimal",
        "--json",
        "--prompt",
        prompt,
    ]
    timeout_seconds = _env_float("OPENCLAW_ALEXA_BRAIN_TIMEOUT_SECONDS", DEFAULT_BRAIN_TIMEOUT_SECONDS)
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds + 0.75,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    try:
        text = _extract_infer_model_text(json.loads(completed.stdout or "{}"))
    except json.JSONDecodeError:
        return None
    if not text or _is_fallback_sentinel(text):
        return None
    return text


def _openrouter_brain_response(request_text: str) -> str | None:
    api_key = _clean_env("OPENROUTER_API_KEY")
    if not api_key:
        return None
    body = json.dumps(
        {
            "model": _configured_brain_model("openrouter"),
            "messages": [
                {"role": "system", "content": _alexa_brain_system_prompt()},
                {"role": "user", "content": request_text},
            ],
            "temperature": 0.2,
            "max_tokens": 96,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        OPENROUTER_CHAT_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": _clean_env("OPENCLAW_ALEXA_HTTP_REFERER")
            or "https://example.com/openclaw-alexa-bridge",
            "X-Title": "OpenClaw Alexa Bridge",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=_env_float("OPENCLAW_ALEXA_BRAIN_TIMEOUT_SECONDS", DEFAULT_BRAIN_TIMEOUT_SECONDS),
        ) as response:
            raw = response.read(MAX_BODY_BYTES).decode("utf-8")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None
    try:
        text = _extract_chat_completion_text(json.loads(raw or "{}"))
    except json.JSONDecodeError:
        return None
    if not text or _is_fallback_sentinel(text):
        return None
    return text


def _brain_response(message: str) -> str | None:
    if not _env_bool("OPENCLAW_ALEXA_BRAIN_ENABLED", True):
        return None
    request_text = _strip_invocation_prefix(_request_text_from_agent_message(message))
    if not request_text:
        return None
    provider = (_clean_env("OPENCLAW_ALEXA_BRAIN_PROVIDER") or DEFAULT_BRAIN_PROVIDER).lower()
    if provider in {"anthropic", "claude"}:
        return _anthropic_brain_response(request_text)
    if provider in {"openai", "gpt", "openclaw", "openclaw-infer"}:
        return _openclaw_infer_brain_response(request_text)
    if provider == "openrouter":
        if not _env_bool("OPENCLAW_ALEXA_ALLOW_OPENROUTER", False):
            return None
        return _openrouter_brain_response(request_text)
    return None


class HookOnlyProxy(http.server.BaseHTTPRequestHandler):
    server_version = "OpenClawAlexaProxy/1.1"

    def do_GET(self) -> None:
        self._send_bytes(404, b"not found\n", "text/plain")

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/hooks/agent":
            self._handle_hook_forward()
            return
        if path == DEVICE_RESPONSE_PATH:
            self._handle_device_response()
            return
        self._send_bytes(404, b"not found\n", "text/plain")

    def _read_limited_body(self) -> bytes | None:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            self._send_bytes(400, b"invalid content length\n", "text/plain")
            return None
        if length < 0 or length > MAX_BODY_BYTES:
            self._send_bytes(413, b"request body too large\n", "text/plain")
            return None
        return self.rfile.read(length)

    def _handle_hook_forward(self) -> None:
        body = self._read_limited_body()
        if body is None:
            return
        status, response_body, content_type = self._forward_to_gateway_hook(
            body,
            dict(_header_items(self.headers)),
        )
        self._send_bytes(status, response_body, content_type)

    def _handle_device_response(self) -> None:
        if not self._authorize_device_response():
            return
        body = self._read_limited_body()
        if body is None:
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "invalid json"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"ok": False, "error": "invalid payload"})
            return
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            self._send_json(400, {"ok": False, "error": "message required"})
            return

        fast_response = _fast_local_response(message.strip())
        if fast_response:
            self._send_json(200, {"ok": True, "response": _cap_speech(fast_response)})
            return

        brain_response = _brain_response(message.strip())
        if brain_response:
            self._send_json(200, {"ok": True, "response": _cap_speech(brain_response)})
            return

        response_text = self._run_sync_agent(message.strip())
        if response_text:
            self._send_json(200, {"ok": True, "response": _cap_speech(response_text)})
            return

        fallback = payload.get("fallbackPayload")
        if isinstance(fallback, dict):
            fallback_response = self._dispatch_fallback(fallback, payload.get("requestId"))
            self._send_json(200, fallback_response)
            return

        self._send_json(
            504,
            {
                "ok": False,
                "error": "the assistant did not answer before the Alexa response window closed",
            },
        )

    def _authorize_device_response(self) -> bool:
        expected = (
            _clean_env("OPENCLAW_ALEXA_DEVICE_TOKEN")
            or _clean_env("OPENCLAW_ALEXA_HOOK_TOKEN")
            or _clean_env("OPENCLAW_HOOK_TOKEN")
        )
        if not expected:
            self._send_json(500, {"ok": False, "error": "device response token is not configured"})
            return False
        header = self.headers.get("Authorization", "").strip()
        prefix = "Bearer "
        supplied = header[len(prefix) :].strip() if header.startswith(prefix) else ""
        if not supplied or not hmac.compare_digest(supplied, expected):
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return False
        return True

    def _run_sync_agent(self, message: str) -> str | None:
        timeout_seconds = _env_int("OPENCLAW_ALEXA_SYNC_TIMEOUT_SECONDS", DEFAULT_SYNC_TIMEOUT_SECONDS)
        process_timeout = timeout_seconds + 0.75
        openclaw_cli = _clean_env("OPENCLAW_CLI") or DEFAULT_OPENCLAW_CLI
        agent_id = _clean_env("OPENCLAW_ALEXA_AGENT_ID") or "example-agent"
        session_key = _clean_env("OPENCLAW_ALEXA_DEVICE_SESSION_KEY") or DEFAULT_DEVICE_SESSION_KEY
        cmd = [
            openclaw_cli,
            "agent",
            "--agent",
            agent_id,
            "--session-key",
            session_key,
            "--message",
            message,
            "--json",
            "--timeout",
            str(timeout_seconds),
        ]
        model = _clean_env("OPENCLAW_ALEXA_RESPONSE_MODEL")
        if model:
            cmd.extend(["--model", model])
        thinking = _clean_env("OPENCLAW_ALEXA_RESPONSE_THINKING")
        if thinking:
            cmd.extend(["--thinking", thinking])
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                check=False,
                text=True,
                timeout=process_timeout,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if completed.returncode != 0:
            return None
        try:
            parsed = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return None
        return _extract_response_text(parsed)

    def _dispatch_fallback(self, fallback_payload: dict, request_id: object) -> dict:
        headers = {
            "Authorization": self.headers.get("Authorization", ""),
            "Content-Type": "application/json",
        }
        if isinstance(request_id, str) and request_id.strip():
            headers["Idempotency-Key"] = request_id.strip()
        body = json.dumps(fallback_payload, separators=(",", ":")).encode("utf-8")
        status, response_body, _content_type = self._forward_to_gateway_hook(body, headers)
        if 200 <= status < 300:
            try:
                parsed = json.loads(response_body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                parsed = {"ok": True}
            if isinstance(parsed, dict):
                parsed.setdefault(
                    "speech",
                    "The assistant is still working and will reply when it is done.",
                )
                parsed["fallback"] = True
                return parsed
        return {
            "ok": False,
            "error": "the assistant did not answer quickly and the fallback hook failed",
        }

    def _forward_to_gateway_hook(self, body: bytes, headers: dict[str, str]) -> tuple[int, bytes, str]:
        target = self.server.gateway_url.rstrip("/") + "/hooks/agent"  # type: ignore[attr-defined]
        request = urllib.request.Request(target, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                response_body = response.read(MAX_BODY_BYTES)
                content_type = response.headers.get("Content-Type", "application/json")
                return response.status, response_body, content_type
        except urllib.error.HTTPError as exc:
            response_body = exc.read(MAX_BODY_BYTES)
            content_type = exc.headers.get("Content-Type", "application/json")
            return exc.code, response_body, content_type
        except urllib.error.URLError:
            return 502, b'{"ok":false,"error":"gateway unavailable"}\n', "application/json"

    def _send_json(self, status: int, payload: dict) -> None:
        self._send_bytes(
            status,
            json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n",
            "application/json",
        )

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            self.log_message("client disconnected before response body was written")


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    host = os.environ.get("OPENCLAW_HOOK_PROXY_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    port = _env_int("OPENCLAW_HOOK_PROXY_PORT", DEFAULT_PORT)
    gateway_url = os.environ.get("OPENCLAW_GATEWAY_URL", DEFAULT_GATEWAY_URL).strip() or DEFAULT_GATEWAY_URL

    with ReusableTCPServer((host, port), HookOnlyProxy) as httpd:
        httpd.gateway_url = gateway_url
        print(
            f"alexa proxy listening on http://{host}:{port}/hooks/agent and {DEVICE_RESPONSE_PATH}",
            flush=True,
        )
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
