# OpenClaw Alexa Bridge

Alexa Skill and AWS Lambda bridge for forwarding spoken requests to an authenticated OpenClaw `/hooks/agent` endpoint.

```text
Echo device -> Alexa Skill -> Python Lambda -> HTTPS /hooks/agent -> OpenClaw agent
```

The bridge keeps Alexa responsible for wake word handling, speech recognition, and spoken responses. OpenClaw handles the agent request.

## What Is Included

- Alexa custom skill package with a simple pass-through intent.
- Python Lambda entrypoint using the Alexa Skills Kit SDK.
- Pure-Python helpers for OpenClaw hook payloads, request validation, and response handling.
- Optional local proxy for narrow `/hooks/agent` forwarding and short `/alexa/respond` device responses.
- Unit tests for payload building, Lambda behavior, and proxy helpers.
- Generic OpenClaw hook config example.
- Deployment and publication guides.

Private deployment history, local tunnel scripts, built artifacts, account IDs, live URLs, and environment-specific status notes are intentionally not included.

## Requirements

- Python 3.11 or newer for local tests and Lambda packaging.
- AWS Lambda with Python 3.11 runtime.
- Alexa Developer account and ASK CLI or Alexa Developer Console access.
- An OpenClaw Gateway reachable by an HTTPS endpoint that forwards only the intended hook paths.

Install local test dependencies:

```bash
python3 -m pip install -r lambda/requirements.txt
```

## Lambda Environment

Required:

- `ALEXA_SKILL_ID`: expected Alexa skill id. Invocations from other skills are rejected.
- `OPENCLAW_HOOK_URL`: public HTTPS URL ending in `/hooks/agent`.
- `OPENCLAW_HOOK_TOKEN`: bearer token matching the OpenClaw hook config.

Common optional values:

- `OPENCLAW_DELIVERY_CHANNEL`: OpenClaw delivery channel.
- `OPENCLAW_DELIVERY_TARGET`: channel-specific destination.
- `OPENCLAW_SKILL_NAME`: source name shown to OpenClaw. Defaults to `Alexa`.
- `OPENCLAW_VISIBLE_REPLY_PREFIX`: prefix for visible async replies. Defaults to `Alexa -> OpenClaw:`.
- `OPENCLAW_RESPONSE_MODE=telegram`: send to OpenClaw asynchronously and have Alexa acknowledge.
- `OPENCLAW_RESPONSE_MODE=device`: ask an `/alexa/respond` proxy for a short spoken answer first.
- `OPENCLAW_DEVICE_RESPONSE_URL`: HTTPS URL ending in `/alexa/respond`, required for device mode.
- `OPENCLAW_REQUEST_TIMEOUT_SECONDS`: request timeout, max `7`.
- `OPENCLAW_AGENT_TIMEOUT_SECONDS`: async OpenClaw timeout.
- `OPENCLAW_AGENT_ID`: target agent id, if your hook allows more than one.
- `OPENCLAW_SESSION_KEY`: optional session key. Prefer OpenClaw-side defaults when possible.

Do not put real tokens, skill IDs, account IDs, or live URLs into tracked files.

## OpenClaw Hook Config

Use the config example in `config/openclaw-alexa-hooks.patch.json5` as a starting point. Replace placeholder agent/session values with your own deployment values and store the token in your private environment.

```json
{
  "hooks": {
    "enabled": true,
    "path": "/hooks",
    "token": "${OPENCLAW_ALEXA_HOOK_TOKEN}",
    "allowedAgentIds": ["example-agent"],
    "defaultSessionKey": "hook:alexa",
    "allowedSessionKeyPrefixes": ["hook:alexa"],
    "allowRequestSessionKey": false,
    "maxBodyBytes": 65536
  }
}
```

After any real config change, run:

```bash
openclaw config validate
```

## Test

```bash
python3 -m unittest discover -s tests
python3 -m py_compile lambda/alexa_bridge.py lambda/lambda_function.py scripts/inspect-hooks-config.py scripts/temp-hook-proxy.py
bash -n scripts/package-lambda.sh scripts/deployment-preflight.sh
```

Or run the helper:

```bash
scripts/deployment-preflight.sh
```

To include live OpenClaw config inspection, opt in explicitly:

```bash
OPENCLAW_PREFLIGHT_LIVE_CONFIG=1 scripts/deployment-preflight.sh
```

## Package Lambda

```bash
./scripts/package-lambda.sh
```

The zip is written to `build/openclaw-alexa-lambda.zip`.

The package script defaults to Python 3.11 Lambda x86_64 wheels:

```bash
LAMBDA_PYTHON_VERSION=3.11 LAMBDA_PLATFORM=manylinux2014_x86_64 ./scripts/package-lambda.sh
```

Use `LAMBDA_PLATFORM=manylinux2014_aarch64` only if the Lambda is configured for arm64.

## Optional Device Response Proxy

`scripts/temp-hook-proxy.py` can expose two local-only paths behind your own HTTPS ingress:

- `POST /hooks/agent`: forwards to the local OpenClaw Gateway hook.
- `POST /alexa/respond`: returns a short spoken response when possible, then falls back to the async hook.

Important proxy environment variables:

- `OPENCLAW_GATEWAY_URL`: local Gateway URL. Defaults to `http://127.0.0.1:18789`.
- `OPENCLAW_HOOK_PROXY_HOST`: bind host. Defaults to `127.0.0.1`.
- `OPENCLAW_HOOK_PROXY_PORT`: bind port. Defaults to `18089`.
- `OPENCLAW_ALEXA_DEVICE_TOKEN` or `OPENCLAW_HOOK_TOKEN`: bearer token for `/alexa/respond`.
- `OPENCLAW_CLI`: OpenClaw CLI path. Defaults to `openclaw`.
- `OPENCLAW_ALEXA_AGENT_ID`: target agent for short synchronous responses. Defaults to `example-agent`.
- `OPENCLAW_ALEXA_TIME_ZONE`: timezone used by tiny local time responses. Defaults to `UTC`.
- `OPENCLAW_ALEXA_BRAIN_ENABLED=false`: disables model-backed short response attempts.

Expose only the two intended paths through a TLS terminator or tunnel you control. Do not expose the full Gateway.

## Guides

- `docs/DEPLOYMENT.md`: end-to-end deployment checklist.
- `docs/PUBLICATION_GUIDE.md`: how this repo was sanitized into a public release without private history or environment details.
