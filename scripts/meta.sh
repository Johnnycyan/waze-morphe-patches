#!/usr/bin/env bash
# Prints KEY=... and BASE_VERSION=... for the current inputs.
# KEY identifies exactly what goes into the bundle (pinned upstream tags, overlay, assembly), so a
# verification record or release made for one set of inputs is never reused for another.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source <(grep -E '^[A-Z_]+=' upstream.lock)

key="$( { cat upstream.lock scripts/assemble.sh; find overlay -type f | LC_ALL=C sort | xargs cat; } \
    | tr -d '\r' | sha256sum | cut -c1-16)"
echo "KEY=$key"
echo "BASE_VERSION=${NL_TAG#v}-tts.${TTS_TAG#v}"
