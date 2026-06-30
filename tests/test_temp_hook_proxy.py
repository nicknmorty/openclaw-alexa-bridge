import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PROXY_PATH = ROOT / "scripts" / "temp-hook-proxy.py"

spec = importlib.util.spec_from_file_location("temp_hook_proxy", PROXY_PATH)
temp_hook_proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(temp_hook_proxy)


class TempHookProxyFastResponseTests(unittest.TestCase):
    def test_fast_response_handles_say_with_invocation_prefix(self):
        self.assertEqual(
            temp_hook_proxy._fast_local_response(
                "Voice request via Alexa. Request: the claw bridge to say ping",
            ),
            "ping",
        )
        self.assertEqual(
            temp_hook_proxy._fast_local_response("Request: claw bridge say ping"),
            "ping",
        )

    def test_fast_response_handles_time_variants_with_invocation_prefix(self):
        with patch.object(temp_hook_proxy, "datetime") as datetime_mock:
            now = datetime_mock.now.return_value
            now.strftime.return_value = "11:40 PM"

            self.assertEqual(
                temp_hook_proxy._fast_local_response(
                    "Voice request via Alexa. Request: the claw bridge what time it is",
                ),
                "It is 11:40 PM.",
            )
            self.assertEqual(
                temp_hook_proxy._fast_local_response("Request: claw bridge what time is it"),
                "It is 11:40 PM.",
            )

    def test_extract_chat_completion_text(self):
        self.assertEqual(
            temp_hook_proxy._extract_chat_completion_text(
                {"choices": [{"message": {"content": "Hello there"}}]},
            ),
            "Hello there",
        )

    def test_extract_anthropic_message_text(self):
        self.assertEqual(
            temp_hook_proxy._extract_anthropic_message_text(
                {"content": [{"type": "text", "text": "Hello there"}]},
            ),
            "Hello there",
        )

    @patch.dict(os.environ, {"ANTHROPIC_OAUTH_TOKEN": "secret"}, clear=True)
    @patch.object(temp_hook_proxy.urllib.request, "urlopen")
    def test_anthropic_brain_response_returns_text(self, urlopen_mock):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        response.read.return_value = (
            b'{"content":[{"type":"text","text":"Sure, I can hear you."}]}'
        )
        urlopen_mock.return_value = response

        self.assertEqual(
            temp_hook_proxy._anthropic_brain_response("can you hear me"),
            "Sure, I can hear you.",
        )
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        self.assertEqual(request.full_url, temp_hook_proxy.ANTHROPIC_MESSAGES_URL)

    @patch.dict(os.environ, {"ANTHROPIC_OAUTH_TOKEN": "secret"}, clear=True)
    @patch.object(temp_hook_proxy.urllib.request, "urlopen")
    def test_anthropic_brain_response_falls_back_on_sentinel(self, urlopen_mock):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        response.read.return_value = b'{"content":[{"type":"text","text":"FALLBACK_TO_ASYNC_AGENT"}]}'
        urlopen_mock.return_value = response

        self.assertIsNone(temp_hook_proxy._anthropic_brain_response("send a text"))

    @patch.dict(os.environ, {"OPENCLAW_ALEXA_BRAIN_PROVIDER": "anthropic"}, clear=True)
    @patch.object(temp_hook_proxy, "_anthropic_brain_response", return_value="A model answer.")
    def test_brain_response_strips_invocation_prefix(self, brain_mock):
        self.assertEqual(
            temp_hook_proxy._brain_response("Request: the claw bridge explain Saturn"),
            "A model answer.",
        )
        brain_mock.assert_called_once_with("explain Saturn")

    @patch.dict(
        os.environ,
        {"OPENCLAW_ALEXA_BRAIN_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "secret"},
        clear=True,
    )
    @patch.object(temp_hook_proxy, "_openrouter_brain_response", return_value="A model answer.")
    def test_openrouter_provider_requires_explicit_allow_flag(self, brain_mock):
        self.assertIsNone(temp_hook_proxy._brain_response("Request: explain Saturn"))
        brain_mock.assert_not_called()

    @patch.dict(
        os.environ,
        {
            "OPENCLAW_ALEXA_BRAIN_PROVIDER": "openrouter",
            "OPENCLAW_ALEXA_ALLOW_OPENROUTER": "true",
            "OPENROUTER_API_KEY": "secret",
        },
        clear=True,
    )
    @patch.object(temp_hook_proxy, "_openrouter_brain_response", return_value="A model answer.")
    def test_openrouter_provider_can_be_explicitly_allowed(self, brain_mock):
        self.assertEqual(
            temp_hook_proxy._brain_response("Request: explain Saturn"),
            "A model answer.",
        )
        brain_mock.assert_called_once_with("explain Saturn")

    @patch.dict(os.environ, {"OPENCLAW_ALEXA_BRAIN_PROVIDER": "openai"}, clear=True)
    @patch.object(temp_hook_proxy, "_openclaw_infer_brain_response", return_value="A GPT answer.")
    def test_openai_provider_uses_openclaw_infer(self, brain_mock):
        self.assertEqual(
            temp_hook_proxy._brain_response("Request: explain Saturn"),
            "A GPT answer.",
        )
        brain_mock.assert_called_once_with("explain Saturn")


if __name__ == "__main__":
    unittest.main()
