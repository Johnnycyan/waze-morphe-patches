#!/usr/bin/env bash
# Points upstream.lock at the newest release tag of each upstream. Prints "changed" or "unchanged".
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK="$ROOT/upstream.lock"
# shellcheck disable=SC1090
source <(grep -E '^[A-Z_]+=' "$LOCK")

latest_tag() {
    git ls-remote --tags --refs "$1" 'v*' | sed 's#.*refs/tags/##' \
        | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1
}

nl="$(latest_tag "$NL_REPO")"
tts="$(latest_tag "$TTS_REPO")"
[[ -n "$nl" && -n "$tts" ]] || { echo "could not resolve upstream tags" >&2; exit 1; }

if [[ "$nl" == "$NL_TAG" && "$tts" == "$TTS_TAG" ]]; then
    echo unchanged
    exit 0
fi
sed -i -e "s/^NL_TAG=.*/NL_TAG=$nl/" -e "s/^TTS_TAG=.*/TTS_TAG=$tts/" "$LOCK"
echo changed
