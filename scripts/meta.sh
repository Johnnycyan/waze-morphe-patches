#!/usr/bin/env bash
# Prints KEY=... and BASE_VERSION=... for the committed inputs.
# KEY identifies exactly what goes into the bundle (Waze target, sources, build files), so a
# verification record or release made for one set of inputs is never reused for another.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source <(grep -E '^[A-Z_]+=' upstream.lock)

# Git blob hashes of the tracked inputs: independent of checkout line endings.
key="$(git ls-files -s -- upstream.lock patches extensions gradle settings.gradle.kts build.gradle.kts \
        gradle.properties VERSION scripts/build.sh | sha256sum | cut -c1-16)"
echo "KEY=$key"
echo "BASE_VERSION=$(cat VERSION)-waze.$WAZE_VERSION"
