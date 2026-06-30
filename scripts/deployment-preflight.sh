#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ENV_FILE="${OPENCLAW_ENV_FILE:-}"

warnings=0

warn() {
  warnings=$((warnings + 1))
  printf 'WARN: %s\n' "$*"
}

info() {
  printf 'INFO: %s\n' "$*"
}

section() {
  printf '\n== %s ==\n' "$*"
}

has_env_file_key() {
  local key="$1"
  [[ -n "$ENV_FILE" ]] || return 1
  [[ -f "$ENV_FILE" ]] && grep -Eq "^[[:space:]]*(export[[:space:]]+)?${key}=" "$ENV_FILE"
}

cd "$ROOT"

section "Local source"
"$PYTHON_BIN" -m unittest discover -s tests
"$PYTHON_BIN" -m py_compile lambda/alexa_bridge.py lambda/lambda_function.py scripts/inspect-hooks-config.py
bash -n scripts/package-lambda.sh
bash -n scripts/deployment-preflight.sh
"$PYTHON_BIN" -m py_compile scripts/temp-hook-proxy.py
"$PYTHON_BIN" -m json.tool config/openclaw-alexa-hooks.patch.json5 >/dev/null
"$PYTHON_BIN" -m json.tool ask-resources.json >/dev/null
"$PYTHON_BIN" -m json.tool skill-package/skill.json >/dev/null
"$PYTHON_BIN" -m json.tool skill-package/interactionModels/custom/en-US.json >/dev/null
info "source checks passed"

section "Lambda package"
if [[ -f build/openclaw-alexa-lambda.zip ]]; then
  info "build/openclaw-alexa-lambda.zip exists"
else
  warn "build/openclaw-alexa-lambda.zip is missing; run scripts/package-lambda.sh before deploy"
fi

section "ASK CLI"
if command -v ask >/dev/null 2>&1; then
  info "ask CLI found: $(command -v ask)"
else
  warn "ask CLI not found; use Alexa Developer Console manually or install/configure ASK CLI"
fi

section "OpenClaw hook token"
if [[ -n "${OPENCLAW_ALEXA_HOOK_TOKEN:-}" ]]; then
  info "OPENCLAW_ALEXA_HOOK_TOKEN is present in the process environment"
elif has_env_file_key "OPENCLAW_ALEXA_HOOK_TOKEN"; then
  info "OPENCLAW_ALEXA_HOOK_TOKEN is present in $ENV_FILE"
else
  warn "OPENCLAW_ALEXA_HOOK_TOKEN is not present in the process environment"
fi

section "OpenClaw config"
if [[ "${OPENCLAW_PREFLIGHT_LIVE_CONFIG:-}" == "1" ]] && command -v openclaw >/dev/null 2>&1; then
  hooks_json="$(openclaw config get hooks --json)"
  OPENCLAW_ALEXA_HOOKS_JSON="$hooks_json" OPENCLAW_ALEXA_HOOKS_SOURCE=resolved "$PYTHON_BIN" scripts/inspect-hooks-config.py
  openclaw config validate
else
  info "skipping live config inspection; set OPENCLAW_PREFLIGHT_LIVE_CONFIG=1 to enable it"
fi

section "Summary"
if (( warnings == 0 )); then
  info "deployment preflight completed with no warnings"
else
  warn "deployment preflight completed with $warnings warning(s)"
fi
