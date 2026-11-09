#!/usr/bin/env bash
# Follows the Waze version nl-morphe-patches targets: reads WazeConstants.kt at NL's newest
# release tag and moves upstream.lock to its newest Waze version. Only a new Waze version is
# written, so NL releases that keep the same version do not trigger a rebuild.
# Prints "changed" or "unchanged".
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK="$ROOT/upstream.lock"
# shellcheck disable=SC1090
source <(grep -E '^[A-Z_]+=' "$LOCK")

tag="$(git ls-remote --tags --refs "$NL_REPO" 'v*' | sed 's#.*refs/tags/##' \
    | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)"
[[ -n "$tag" ]] || { echo "could not resolve the newest nl-morphe-patches tag" >&2; exit 1; }

constants="${NL_REPO%.git}/-/raw/$tag/patches/src/main/kotlin/patches/waze/WazeConstants.kt"
waze="$(curl -fsSL "$constants" | grep -oE 'AppTarget\(version = "[0-9.]+"' \
    | grep -oE '[0-9]+(\.[0-9]+)+' | sort -V | tail -1)"
[[ -n "$waze" ]] || { echo "no Waze AppTarget in $constants" >&2; exit 1; }

if [[ "$waze" == "$WAZE_VERSION" ]]; then
    echo unchanged
    exit 0
fi
sed -i -e "s/^NL_TAG=.*/NL_TAG=$tag/" -e "s/^WAZE_VERSION=.*/WAZE_VERSION=$waze/" "$LOCK"
echo changed
