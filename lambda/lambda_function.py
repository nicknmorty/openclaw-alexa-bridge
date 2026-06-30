"""Alexa Skills Kit entrypoint for the OpenClaw bridge."""

from __future__ import annotations

import logging
import os

from ask_sdk_core.dispatch_components import (
    AbstractExceptionHandler,
    AbstractRequestHandler,
    AbstractRequestInterceptor,
)
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.utils import is_intent_name, is_request_type

from alexa_bridge import (
    BridgeConfigError,
    OpenClawHookError,
    alexa_speech_for_openclaw_result,
    build_device_response_payload,
    build_openclaw_payload,
    load_config,
    post_device_response_request,
    post_openclaw_hook,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class UnauthorizedSkillError(PermissionError):
    """Raised when an invocation is not from the configured Alexa skill."""


def _load_expected_skill_id() -> str:
    expected_skill_id = os.environ.get("ALEXA_SKILL_ID", "").strip()
    if not expected_skill_id:
        raise BridgeConfigError("ALEXA_SKILL_ID is required")
    return expected_skill_id


EXPECTED_SKILL_ID = _load_expected_skill_id()
CONFIG = load_config()


class SkillIdRequestInterceptor(AbstractRequestInterceptor):
    """Required skill-id guard for private deployments."""

    def process(self, handler_input: HandlerInput) -> None:
        actual_skill_id = _actual_skill_id(handler_input)
        if actual_skill_id != EXPECTED_SKILL_ID:
            logger.warning("unexpected Alexa skill id: %s", actual_skill_id or "missing")
            raise UnauthorizedSkillError("unexpected Alexa skill id")


class LaunchRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input: HandlerInput):
        speak_output = "OpenClaw is ready. What should I send?"
        return handler_input.response_builder.speak(speak_output).ask(speak_output).response


def _request_id(handler_input: HandlerInput) -> str | None:
    request = handler_input.request_envelope.request
    return getattr(request, "request_id", None)


def _actual_skill_id(handler_input: HandlerInput) -> str | None:
    envelope = handler_input.request_envelope
    context = getattr(envelope, "context", None)
    system = getattr(context, "system", None)
    application = getattr(system, "application", None)
    skill_id = getattr(application, "application_id", None)
    return skill_id if isinstance(skill_id, str) else None


def _slot_value(handler_input: HandlerInput, slot_name: str) -> str | None:
    request = handler_input.request_envelope.request
    intent = getattr(request, "intent", None)
    slots = getattr(intent, "slots", None) or {}
    slot = slots.get(slot_name)
    value = getattr(slot, "value", None)
    return value if isinstance(value, str) else None


class PassThroughIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("PassThroughIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        query = _slot_value(handler_input, "Query")
        if not query or not query.strip():
            speak_output = "I didn't catch that. Try saying, ask claw bridge to check something."
            return handler_input.response_builder.speak(speak_output).ask(speak_output).response

        request_id = _request_id(handler_input)
        try:
            if CONFIG.response_mode == "device":
                payload = build_device_response_payload(query, CONFIG, request_id=request_id)
                post_result = post_device_response_request
            else:
                payload = build_openclaw_payload(query, CONFIG, request_id=request_id)
                post_result = post_openclaw_hook
            logger.info(
                "sending Alexa request to OpenClaw; request_id=%s query_chars=%s response_mode=%s",
                request_id or "none",
                len(query),
                CONFIG.response_mode,
            )
            result = post_result(payload, CONFIG, request_id=request_id)
            speak_output = alexa_speech_for_openclaw_result(result)
        except ValueError:
            speak_output = "I didn't catch that. Try again with a shorter request."
        except OpenClawHookError as exc:
            logger.warning("OpenClaw hook call failed: %s", exc)
            speak_output = "I couldn't reach OpenClaw through the hook."

        reprompt = "Anything else? Try saying, ask claw bridge to, followed by your request."
        return handler_input.response_builder.speak(speak_output).ask(reprompt).response


class HelpIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        speak_output = "Say something like, ask claw bridge to check the server."
        return handler_input.response_builder.speak(speak_output).ask(speak_output).response


class FallbackIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input: HandlerInput):
        speak_output = "Try saying, ask claw bridge to, followed by your request."
        return handler_input.response_builder.speak(speak_output).ask(speak_output).response


class CancelOrStopIntentHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_intent_name("AMAZON.CancelIntent")(handler_input) or is_intent_name(
            "AMAZON.StopIntent",
        )(handler_input)

    def handle(self, handler_input: HandlerInput):
        return handler_input.response_builder.speak("Closing OpenClaw.").response


class SessionEndedRequestHandler(AbstractRequestHandler):
    def can_handle(self, handler_input: HandlerInput) -> bool:
        return is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input: HandlerInput):
        reason = getattr(handler_input.request_envelope.request, "reason", "unknown")
        logger.info("Alexa session ended: %s", reason)
        return handler_input.response_builder.response


class UnauthorizedSkillExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input: HandlerInput, exception: Exception) -> bool:
        return isinstance(exception, UnauthorizedSkillError)

    def handle(self, handler_input: HandlerInput, exception: Exception):
        logger.warning("ending unauthorized Alexa invocation")
        return handler_input.response_builder.set_should_end_session(True).response


class CatchAllExceptionHandler(AbstractExceptionHandler):
    def can_handle(self, handler_input: HandlerInput, exception: Exception) -> bool:
        return True

    def handle(self, handler_input: HandlerInput, exception: Exception):
        logger.exception("Alexa bridge failed")
        speak_output = "Sorry, I had trouble sending that to OpenClaw."
        return handler_input.response_builder.speak(speak_output).ask(speak_output).response


sb = SkillBuilder()
sb.add_global_request_interceptor(SkillIdRequestInterceptor())
sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(PassThroughIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_exception_handler(UnauthorizedSkillExceptionHandler())
sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()
