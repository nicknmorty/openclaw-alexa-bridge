import importlib
import os
import sys
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lambda"))


class FakeResponseBuilder:
    def __init__(self):
        self.speech = None
        self.reprompt = None
        self.should_end_session = None

    def speak(self, text):
        self.speech = text
        return self

    def ask(self, text):
        self.reprompt = text
        return self

    def set_should_end_session(self, value):
        self.should_end_session = value
        return self

    @property
    def response(self):
        return self


def install_ask_sdk_stubs():
    root = types.ModuleType("ask_sdk_core")
    dispatch = types.ModuleType("ask_sdk_core.dispatch_components")
    handler_input = types.ModuleType("ask_sdk_core.handler_input")
    skill_builder = types.ModuleType("ask_sdk_core.skill_builder")
    utils = types.ModuleType("ask_sdk_core.utils")

    class AbstractExceptionHandler:
        pass

    class AbstractRequestHandler:
        pass

    class AbstractRequestInterceptor:
        pass

    class HandlerInput:
        pass

    class SkillBuilder:
        def add_global_request_interceptor(self, interceptor):
            return None

        def add_request_handler(self, handler):
            return None

        def add_exception_handler(self, handler):
            return None

        def lambda_handler(self):
            return lambda *args, **kwargs: None

    def is_intent_name(name):
        def predicate(handler_input):
            request = handler_input.request_envelope.request
            intent = getattr(request, "intent", None)
            return getattr(intent, "name", None) == name

        return predicate

    def is_request_type(name):
        def predicate(handler_input):
            request = handler_input.request_envelope.request
            return getattr(request, "request_type", None) == name

        return predicate

    dispatch.AbstractExceptionHandler = AbstractExceptionHandler
    dispatch.AbstractRequestHandler = AbstractRequestHandler
    dispatch.AbstractRequestInterceptor = AbstractRequestInterceptor
    handler_input.HandlerInput = HandlerInput
    skill_builder.SkillBuilder = SkillBuilder
    utils.is_intent_name = is_intent_name
    utils.is_request_type = is_request_type

    sys.modules["ask_sdk_core"] = root
    sys.modules["ask_sdk_core.dispatch_components"] = dispatch
    sys.modules["ask_sdk_core.handler_input"] = handler_input
    sys.modules["ask_sdk_core.skill_builder"] = skill_builder
    sys.modules["ask_sdk_core.utils"] = utils


def request_input(*, app_id="amzn1.ask.skill.test", query="status", request_type="IntentRequest"):
    slot = types.SimpleNamespace(value=query)
    intent = types.SimpleNamespace(name="PassThroughIntent", slots={"Query": slot})
    request = types.SimpleNamespace(
        request_id="req-1",
        request_type=request_type,
        intent=intent,
    )
    application = types.SimpleNamespace(application_id=app_id)
    system = types.SimpleNamespace(application=application)
    context = types.SimpleNamespace(system=system)
    envelope = types.SimpleNamespace(context=context, request=request)
    return types.SimpleNamespace(
        request_envelope=envelope,
        response_builder=FakeResponseBuilder(),
    )


class LambdaFunctionTests(unittest.TestCase):
    def setUp(self):
        install_ask_sdk_stubs()
        sys.modules.pop("lambda_function", None)
        self.env = patch.dict(
            os.environ,
            {
                "ALEXA_SKILL_ID": "amzn1.ask.skill.test",
                "OPENCLAW_HOOK_URL": "https://example.test/hooks/agent",
                "OPENCLAW_HOOK_TOKEN": "secret",
            },
            clear=True,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        sys.modules.pop("lambda_function", None)

    def import_lambda_function(self):
        return importlib.import_module("lambda_function")

    def test_import_requires_alexa_skill_id(self):
        os.environ.pop("ALEXA_SKILL_ID")

        with self.assertRaises(Exception) as ctx:
            self.import_lambda_function()

        self.assertIn("ALEXA_SKILL_ID", str(ctx.exception))

    def test_skill_id_interceptor_accepts_expected_id(self):
        module = self.import_lambda_function()

        module.SkillIdRequestInterceptor().process(request_input())

    def test_skill_id_interceptor_rejects_unexpected_id(self):
        module = self.import_lambda_function()

        with self.assertRaises(module.UnauthorizedSkillError):
            module.SkillIdRequestInterceptor().process(request_input(app_id="wrong"))

    def test_unauthorized_handler_ends_session_without_speech(self):
        module = self.import_lambda_function()
        handler_input = request_input(app_id="wrong")
        handler = module.UnauthorizedSkillExceptionHandler()

        response = handler.handle(handler_input, module.UnauthorizedSkillError("wrong"))

        self.assertIs(response, handler_input.response_builder)
        self.assertTrue(response.should_end_session)
        self.assertIsNone(response.speech)

    def test_pass_through_posts_query_to_openclaw(self):
        module = self.import_lambda_function()
        handler_input = request_input(query="check the server")
        handler = module.PassThroughIntentHandler()

        with patch.object(module, "post_openclaw_hook", return_value={"ok": True, "runId": "run-1"}) as post_mock:
            response = handler.handle(handler_input)

        payload = post_mock.call_args.args[0]
        self.assertIn("start your answer with Alexa -> OpenClaw:", payload["message"])
        self.assertTrue(payload["message"].endswith("Request: check the server"))
        self.assertEqual(payload["idempotencyKey"], "req-1")
        self.assertIn("sent that to OpenClaw", response.speech)
        self.assertIn("ask claw bridge to", response.reprompt)

    def test_pass_through_device_mode_posts_to_device_response_proxy(self):
        os.environ["OPENCLAW_RESPONSE_MODE"] = "device"
        os.environ["OPENCLAW_DEVICE_RESPONSE_URL"] = "https://example.test/alexa/respond"
        module = self.import_lambda_function()
        handler_input = request_input(query="check the server")
        handler = module.PassThroughIntentHandler()

        with patch.object(
            module,
            "post_device_response_request",
            return_value={"ok": True, "response": "The server is up."},
        ) as post_mock:
            response = handler.handle(handler_input)

        payload = post_mock.call_args.args[0]
        self.assertIn("Reply with text Alexa can speak aloud", payload["message"])
        self.assertIn("start your answer with Alexa -> OpenClaw:", payload["fallbackPayload"]["message"])
        self.assertEqual(payload["requestId"], "req-1")
        self.assertEqual(response.speech, "The server is up.")
        self.assertIn("ask claw bridge to", response.reprompt)

    def test_pass_through_empty_query_reprompts(self):
        module = self.import_lambda_function()
        handler_input = request_input(query=" ")
        handler = module.PassThroughIntentHandler()

        response = handler.handle(handler_input)

        self.assertIn("didn't catch", response.speech)
        self.assertIn("didn't catch", response.reprompt)

    def test_pass_through_hook_error_speaks_reachability_failure(self):
        module = self.import_lambda_function()
        handler_input = request_input(query="status")
        handler = module.PassThroughIntentHandler()

        with patch.object(module, "post_openclaw_hook", side_effect=module.OpenClawHookError("offline")):
            response = handler.handle(handler_input)

        self.assertIn("couldn't reach OpenClaw", response.speech)


if __name__ == "__main__":
    unittest.main()
