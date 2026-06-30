# Deployment Guide

This guide describes a generic Alexa-to-OpenClaw deployment. Replace every placeholder with values from your own environment and keep real values out of git.

## 1. Create The Alexa Skill

1. Create a custom Alexa skill in the Alexa Developer Console.
2. Use the skill package in `skill-package/`.
3. Keep the invocation name or choose your own.
4. Copy the generated Alexa skill id into private Lambda environment as `ALEXA_SKILL_ID`.

The interaction model uses one pass-through intent:

- Intent: `PassThroughIntent`
- Slot: `Query`
- Slot type: `AMAZON.SearchQuery`

## 2. Configure OpenClaw Hooks

Create a private token and configure OpenClaw to accept only the intended hook session and agent.

Example token location:

```bash
OPENCLAW_ALEXA_HOOK_TOKEN=<generated-secret>
```

Example config patch:

```bash
openclaw config patch --file config/openclaw-alexa-hooks.patch.json5 --dry-run
```

After applying a real config change:

```bash
openclaw config validate
```

## 3. Provide A Narrow HTTPS Ingress

Alexa needs an HTTPS URL. The safest shape is a narrow proxy or tunnel that exposes only:

- `/hooks/agent`
- `/alexa/respond`, only if using device response mode

Do not expose the full OpenClaw Gateway unless you have separately reviewed the Gateway security posture for public access.

The Lambda URL values should look like:

```text
OPENCLAW_HOOK_URL=https://example.com/hooks/agent
OPENCLAW_DEVICE_RESPONSE_URL=https://example.com/alexa/respond
```

## 4. Configure Lambda

Create a Python 3.11 Lambda with handler:

```text
lambda_function.lambda_handler
```

Required Lambda environment:

```text
ALEXA_SKILL_ID=amzn1.ask.skill.example
OPENCLAW_HOOK_URL=https://example.com/hooks/agent
OPENCLAW_HOOK_TOKEN=<generated-secret>
```

Async mode:

```text
OPENCLAW_RESPONSE_MODE=telegram
OPENCLAW_DELIVERY_CHANNEL=<channel>
OPENCLAW_DELIVERY_TARGET=<destination>
OPENCLAW_VISIBLE_REPLY_PREFIX=Alexa -> OpenClaw:
```

Device response mode:

```text
OPENCLAW_RESPONSE_MODE=device
OPENCLAW_DEVICE_RESPONSE_URL=https://example.com/alexa/respond
OPENCLAW_REQUEST_TIMEOUT_SECONDS=7
```

## 5. Package And Upload

Build the Lambda zip:

```bash
./scripts/package-lambda.sh
```

Upload `build/openclaw-alexa-lambda.zip` to Lambda, then add the Lambda ARN as the Alexa custom skill endpoint.

## 6. Test

Run local tests first:

```bash
python3 -m unittest discover -s tests
```

Then test in the Alexa Developer Console:

```text
Alexa, open claw bridge
Alexa, ask claw bridge to check the queue
```

Expected async behavior:

1. Alexa acknowledges the request.
2. OpenClaw receives the hook payload.
3. The configured OpenClaw delivery target receives the visible response.

Expected device mode behavior:

1. Alexa calls Lambda.
2. Lambda calls `/alexa/respond`.
3. The proxy returns a short spoken response if it can.
4. If the request needs longer work, the proxy falls back to `/hooks/agent`.

## 7. Operational Safety

- Keep `OPENCLAW_HOOK_TOKEN` private.
- Restrict allowed agents and session keys in OpenClaw.
- Reject unexpected `ALEXA_SKILL_ID` values in Lambda.
- Keep hook request body limits low.
- Do not log bearer tokens.
- Rotate the hook token if it is exposed.
- Re-run `openclaw config validate` after every config change.
