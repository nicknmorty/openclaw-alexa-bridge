#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT/build/lambda"
ZIP_PATH="$ROOT/build/openclaw-alexa-lambda.zip"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LAMBDA_PYTHON_VERSION="${LAMBDA_PYTHON_VERSION:-3.11}"
LAMBDA_PLATFORM="${LAMBDA_PLATFORM:-manylinux2014_x86_64}"

rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

"$PYTHON_BIN" -m pip install \
  --disable-pip-version-check \
  --only-binary=:all: \
  --platform "$LAMBDA_PLATFORM" \
  --implementation cp \
  --python-version "$LAMBDA_PYTHON_VERSION" \
  -r "$ROOT/lambda/requirements.txt" \
  -t "$BUILD_DIR"
cp "$ROOT"/lambda/*.py "$BUILD_DIR"/
find "$BUILD_DIR" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$BUILD_DIR" -type f -name '*.pyc' -delete

rm -f "$ZIP_PATH"
(
  cd "$BUILD_DIR"
  zip -qr "$ZIP_PATH" .
)

echo "$ZIP_PATH"
