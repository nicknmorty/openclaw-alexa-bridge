import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambda"))

from alexa_bridge import (  # noqa: E402
    BridgeConfig,
    BridgeConfigError,
    OpenClawHookError,
    MAX_QUERY_CHARS,
    MAX_SPEECH_CHARS,
    alexa_speech_for_openclaw_result,
    build_agent_message,
    build_device_agent_message,
    build_device_response_payload,
    build_openclaw_headers,
    build_openclaw_payload,
    load_config,
    normalize_query,
    post_openclaw_hook,
)


class AlexaBridgeTests(unittest.TestCase):
    def test_load_config_requires_hook_url_and_token(self):
        with self.assertRaises(BridgeConfigError):
            load_config({"OPENCLAW_HOOK_TOKEN": "secret"})
        with self.assertRaises(BridgeConfigError):
            load_config({"OPENCLAW_HOOK_URL": "https://example.test/hooks/agent"})

    def test_load_config_requires_https_hook_url(self):
        with self.assertRaises(BridgeConfigError):
            load_config(
                {
                    "OPENCLAW_HOOK_URL": "http://example.test/hooks/agent",
                    "OPENCLAW_HOOK_TOKEN": "secret",
                },
            )

    def test_load_config_requires_hooks_agent_path(self):
        with self.assertRaises(BridgeConfigError):
            load_config(
                {
                    "OPENCLAW_HOOK_URL": "https://example.test/hooks",
                    "OPENCLAW_HOOK_TOKEN": "secret",
                },
            )

    def test_load_config_rejects_query_and_fragment(self):
        for hook_url in (
            "https://example.test/hooks/agent?token=leak",
            "https://example.test/hooks/agent#fragment",
        ):
            with self.subTest(hook_url=hook_url):
                with self.assertRaises(BridgeConfigError):
                    load_config(
                        {
                            "OPENCLAW_HOOK_URL": hook_url,
                            "OPENCLAW_HOOK_TOKEN": "secret",
                },
            )

    def test_load_config_rejects_invalid_wake_mode(self):
        with self.assertRaises(BridgeConfigError):
            load_config(
                {
                    "OPENCLAW_HOOK_URL": "https://example.test/hooks/agent",
                    "OPENCLAW_HOOK_TOKEN": "secret",
                    "OPENCLAW_WAKE_MODE": "later",
                },
            )

    def test_load_config_rejects_invalid_boolean(self):
        with self.assertRaises(BridgeConfigError):
            load_config(
                {
                    "OPENCLAW_HOOK_URL": "https://example.test/hooks/agent",
                    "OPENCLAW_HOOK_TOKEN": "secret",
                    "OPENCLAW_DELIVER": "treu",
                },
            )

    def test_load_config_caps_request_timeout_for_alexa(self):
        with self.assertRaises(BridgeConfigError):
            load_config(
                {
                    "OPENCLAW_HOOK_URL": "https://example.test/hooks/agent",
                    "OPENCLAW_HOOK_TOKEN": "secret",
                    "OPENCLAW_REQUEST_TIMEOUT_SECONDS": "8",
                },
            )

    def test_load_config_device_mode_requires_response_url(self):
        with self.assertRaises(BridgeConfigError):
            load_config(
                {
                    "OPENCLAW_HOOK_URL": "https://example.test/hooks/agent",
                    "OPENCLAW_HOOK_TOKEN": "secret",
                    "OPENCLAW_RESPONSE_MODE": "device",
                },
            )

    def test_load_config_device_mode_accepts_response_url(self):
        config = load_config(
            {
                "OPENCLAW_HOOK_URL": "https://example.test/hooks/agent",
                "OPENCLAW_HOOK_TOKEN": "secret",
                "OPENCLAW_RESPONSE_MODE": "device",
                "OPENCLAW_DEVICE_RESPONSE_URL": "https://example.test/alexa/respond",
            },
        )

        self.assertEqual(config.response_mode, "device")
        self.assertEqual(config.device_response_url, "https://example.test/alexa/respond")

    def test_load_config_rejects_invalid_response_mode(self):
        with self.assertRaises(BridgeConfigError):
            load_config(
                {
                    "OPENCLAW_HOOK_URL": "https://example.test/hooks/agent",
                    "OPENCLAW_HOOK_TOKEN": "secret",
                    "OPENCLAW_RESPONSE_MODE": "loudly",
                },
            )

    def test_bridge_config_repr_masks_token(self):
        config = BridgeConfig(
            openclaw_hook_url="https://example.test/hooks/agent",
            openclaw_hook_token="secret-token",
        )

        self.assertNotIn("secret-token", repr(config))

    def test_build_agent_message_adds_visible_reply_prefix(self):
        config = BridgeConfig(
            openclaw_hook_url="https://example.test/hooks/agent",
            openclaw_hook_token="secret",
        )

        message = build_agent_message("check the server", config)

        self.assertIn("Voice request via Alexa", message)
        self.assertIn("start your answer with Alexa -> OpenClaw:", message)
        self.assertTrue(message.endswith("Request: check the server"))

    def test_build_agent_message_prefix_can_be_disabled(self):
        config = BridgeConfig(
            openclaw_hook_url="https://example.test/hooks/agent",
            openclaw_hook_token="secret",
            visible_reply_prefix=None,
        )

        self.assertEqual(build_agent_message(" check the server ", config), "check the server")

    def test_build_device_agent_message_requests_spoken_answer(self):
        config = BridgeConfig(
            openclaw_hook_url="https://example.test/hooks/agent",
            openclaw_hook_token="secret",
        )

        message = build_device_agent_message(" check the server ", config)

        self.assertIn("Reply with text Alexa can speak aloud", message)
        self.assertIn("Request: check the server", message)
        self.assertNotIn("Alexa -> OpenClaw:", message)

    def test_load_config_allows_custom_visible_reply_prefix(self):
        config = load_config(
            {
                "OPENCLAW_HOOK_URL": "https://example.test/hooks/agent",
                "OPENCLAW_HOOK_TOKEN": "secret",
                "OPENCLAW_VISIBLE_REPLY_PREFIX": "[Alexa]",
            },
        )

        self.assertEqual(config.visible_reply_prefix, "[Alexa]")

    def test_build_openclaw_payload_uses_hook_contract(self):
        config = BridgeConfig(
            openclaw_hook_url="https://example.test/hooks/agent",
            openclaw_hook_token="secret",
            delivery_target="example-destination",
            skill_name="Kitchen Echo",
        )

        payload = build_openclaw_payload(
            " check the server ",
            config,
            request_id="amzn1.echo-api.request.123",
        )

        self.assertEqual(
            payload,
            {
                "message": (
                    "Voice request via Alexa. "
                    "When producing a visible async reply, start your answer with Alexa -> OpenClaw: "
                    "Request: check the server"
                ),
                "name": "Kitchen Echo",
                "wakeMode": "now",
                "deliver": True,
                "channel": "telegram",
                "idempotencyKey": "amzn1.echo-api.request.123",
                "to": "example-destination",
                "timeoutSeconds": 600,
            },
        )
        self.assertNotIn("sessionKey", payload)

    def test_optional_agent_and_session_keys_are_passed_when_configured(self):
        config = BridgeConfig(
            openclaw_hook_url="https://example.test/hooks/agent",
            openclaw_hook_token="secret",
            agent_id="example-agent",
            session_key="hook:alexa",
        )

        payload = build_openclaw_payload("status", config)

        self.assertEqual(payload["agentId"], "example-agent")
        self.assertEqual(payload["sessionKey"], "hook:alexa")

    def test_build_device_response_payload_includes_telegram_fallback(self):
        config = BridgeConfig(
            openclaw_hook_url="https://example.test/hooks/agent",
            openclaw_hook_token="secret",
            delivery_target="example-destination",
            skill_name="Kitchen Echo",
        )

        payload = build_device_response_payload("check the server", config, request_id="req-1")

        self.assertEqual(payload["name"], "Kitchen Echo")
        self.assertEqual(payload["requestId"], "req-1")
        self.assertIn("Reply with text Alexa can speak aloud", payload["message"])
        fallback = payload["fallbackPayload"]
        self.assertEqual(fallback["to"], "example-destination")
        self.assertIn("start your answer with Alexa -> OpenClaw:", fallback["message"])

    def test_agent_timeout_none_is_omitted(self):
        config = BridgeConfig(
            openclaw_hook_url="https://example.test/hooks/agent",
            openclaw_hook_token="secret",
            agent_timeout_seconds=None,
        )

        payload = build_openclaw_payload("status", config)

        self.assertNotIn("timeoutSeconds", payload)

    def test_build_headers_uses_bearer_and_idempotency_header(self):
        config = BridgeConfig(
            openclaw_hook_url="https://example.test/hooks/agent",
            openclaw_hook_token="secret",
        )

        headers = build_openclaw_headers(config, request_id="req-1")

        self.assertEqual(headers["Authorization"], "Bearer secret")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["Idempotency-Key"], "req-1")

    def test_normalize_query_rejects_blank_speech(self):
        with self.assertRaises(ValueError):
            normalize_query("   ")

    def test_normalize_query_truncates_long_speech(self):
        speech = "x" * (MAX_QUERY_CHARS + 100)

        self.assertEqual(len(normalize_query(speech)), MAX_QUERY_CHARS)

    def test_alexa_speech_for_async_run(self):
        speech = alexa_speech_for_openclaw_result({"ok": True, "runId": "run-123"})

        self.assertIn("sent that to OpenClaw", speech)

    def test_alexa_speech_prefers_device_response_over_run_id(self):
        speech = alexa_speech_for_openclaw_result(
            {"ok": True, "runId": "run-123", "response": "The server is up."},
        )

        self.assertEqual(speech, "The server is up.")

    def test_alexa_speech_for_direct_response_is_capped(self):
        speech = alexa_speech_for_openclaw_result({"ok": True, "response": "word " * 3000})

        self.assertLessEqual(len(speech), MAX_SPEECH_CHARS + 3)
        self.assertTrue(speech.endswith("..."))

    def test_alexa_speech_for_ok_without_text(self):
        speech = alexa_speech_for_openclaw_result({"ok": True})

        self.assertEqual(speech, "OpenClaw accepted the request.")

    def test_alexa_speech_for_rejection(self):
        speech = alexa_speech_for_openclaw_result({"ok": False})

        self.assertEqual(speech, "OpenClaw rejected that request.")

    @patch("alexa_bridge.urlopen")
    def test_post_openclaw_hook_success(self, urlopen_mock):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        response.read.return_value = b'{"ok":true,"runId":"run-1"}'
        urlopen_mock.return_value = response
        config = BridgeConfig(
            openclaw_hook_url="https://example.test/hooks/agent",
            openclaw_hook_token="secret",
        )

        result = post_openclaw_hook({"message": "status"}, config, request_id="req-1")

        self.assertEqual(result["runId"], "run-1")
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        self.assertEqual(request.headers["Idempotency-key"], "req-1")

    @patch("alexa_bridge.urlopen")
    def test_post_openclaw_hook_http_error_includes_status(self, urlopen_mock):
        urlopen_mock.side_effect = HTTPError(
            "https://example.test/hooks/agent",
            401,
            "unauthorized",
            {},
            BytesIO(b'{"ok":false}'),
        )
        config = BridgeConfig(
            openclaw_hook_url="https://example.test/hooks/agent",
            openclaw_hook_token="secret",
        )

        with self.assertRaises(OpenClawHookError) as ctx:
            post_openclaw_hook({"message": "status"}, config)

        self.assertEqual(ctx.exception.status, 401)

    @patch("alexa_bridge.urlopen")
    def test_post_openclaw_hook_network_error(self, urlopen_mock):
        urlopen_mock.side_effect = URLError("offline")
        config = BridgeConfig(
            openclaw_hook_url="https://example.test/hooks/agent",
            openclaw_hook_token="secret",
        )

        with self.assertRaises(OpenClawHookError):
            post_openclaw_hook({"message": "status"}, config)

    @patch("alexa_bridge.urlopen")
    def test_post_openclaw_hook_non_json_response(self, urlopen_mock):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        response.read.return_value = b"not json"
        urlopen_mock.return_value = response
        config = BridgeConfig(
            openclaw_hook_url="https://example.test/hooks/agent",
            openclaw_hook_token="secret",
        )

        with self.assertRaises(OpenClawHookError):
            post_openclaw_hook({"message": "status"}, config)


if __name__ == "__main__":
    unittest.main()
