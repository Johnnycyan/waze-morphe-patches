#!/usr/bin/env bash
# Builds the tree from assemble.sh into out/: patches.mpp and patches-list.json.
# Usage: scripts/build.sh <version>   (same version as passed to assemble.sh)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:?usage: build.sh <version>}"
# NL's build always generates a Buienalarm key file, although that patch is pruned here.
export SHARED_MAPS_API_KEY="${SHARED_MAPS_API_KEY:-AIzaSyDOCSGATEPLACEHOLDER00000000000000}"

cd "$ROOT/build/combined"
chmod +x gradlew
# buildAndroid adds classes.dex, which Morphe Manager on Android loads patches from; a plain
# build bundles JVM classes only, which Manager lists as no patches.
./gradlew :patches:buildAndroid generatePatchesList --no-daemon

mpp="patches/build/libs/patches-$VERSION.mpp"
unzip -l "$mpp" | grep -q ' classes.dex$' || { echo "$mpp has no classes.dex" >&2; exit 1; }
mkdir -p "$ROOT/out"
cp "$mpp" "$ROOT/out/patches.mpp"
cp patches-list.json "$ROOT/out/patches-list.json"
