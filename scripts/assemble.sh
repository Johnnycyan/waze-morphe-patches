#!/usr/bin/env bash
# Builds the combined Gradle project in build/combined from the tags pinned in upstream.lock:
#   - nl-morphe-patches, pruned to its Waze patches and the code they need
#   - morphe-waze-system-tts, grafted in with its extension renamed so it cannot clash
# Usage: scripts/assemble.sh <version>
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:?usage: assemble.sh <version>}"
# shellcheck disable=SC1091
source <(grep -E '^[A-Z_]+=' "$ROOT/upstream.lock")

WORK="$ROOT/build"
SRC="$WORK/upstream"
OUT="$WORK/combined"
rm -rf "$SRC" "$OUT"
mkdir -p "$SRC"

git -c advice.detachedHead=false -c core.autocrlf=false clone -q --depth 1 --branch "$NL_TAG" "$NL_REPO" "$SRC/nl"
git -c advice.detachedHead=false -c core.autocrlf=false clone -q --depth 1 --branch "$TTS_TAG" "$TTS_REPO" "$SRC/tts"
rm -rf "$SRC/nl/.git" "$SRC/tts/.git"

# --- NL base, pruned to Waze -------------------------------------------------
cp -r "$SRC/nl" "$OUT"
cd "$OUT"

# Upstream CI, docs and tooling are not part of this build.
rm -rf .github .gitlab .gitlab-ci.yml .forgejo scripts tests node_modules package.json package-lock.json
keep_only() { # keep_only <dir> <names...>: delete every child of <dir> not listed
    local dir="$1"; shift
    for entry in "$dir"/*; do
        local name; name="$(basename "$entry")"
        local keep=0
        for k in "$@"; do [[ "$name" == "$k" ]] && keep=1; done
        [[ $keep == 1 ]] || rm -rf "$entry"
    done
}
keep_only patches/src/main/kotlin patches util
keep_only patches/src/main/kotlin/patches waze shared
keep_only patches/src/main/resources waze licenses
for lang in kotlin java; do
    [[ -d patches/src/test/$lang ]] || continue
    keep_only patches/src/test/$lang patches
    [[ -d patches/src/test/$lang/patches ]] && keep_only patches/src/test/$lang/patches waze
done
rm -rf patches/src/test/resources
# Upstream's generated lists describe the full NL bundle; generatePatchesList rewrites them.
rm -f patches-list.json patches-bundle.json
keep_only extensions waze
keep_only docs waze # Waze tests read docs/waze fixtures.

# --- TTS graft ---------------------------------------------------------------
TTS_EXT=waze-system-tts
cp -r "$SRC/tts/patches/src/main/kotlin/app" patches/src/main/kotlin/app
cp -r "$SRC/tts/extensions/extension" "extensions/$TTS_EXT"
grep -rl 'extensions/extension.mpe' patches/src/main/kotlin/app "extensions/$TTS_EXT" \
    | xargs sed -i "s#extensions/extension\.mpe#extensions/$TTS_EXT.mpe#g"

# Retarget the TTS patch to the Waze version the NL patches support. git apply refuses on any
# mismatch, so an upstream TTS change touching the same lines fails the build instead of
# silently shipping fingerprints for the wrong Waze version.
# build/combined lives inside this repository's checkout, where git apply would resolve paths
# against the outer repo and silently skip them; its own repo makes it patch this tree.
git init -q .
for p in "$ROOT"/overlay/*.patch; do
    [[ -e "$p" ]] || continue
    git apply --verbose "$p"
    # Proves every hunk landed: only an applied patch reverses cleanly.
    git apply --reverse --check "$p" || { echo "overlay $(basename "$p") did not apply" >&2; exit 1; }
done

# --- Bundle identity ---------------------------------------------------------
ABOUT="$ROOT/overlay/about.gradle.kts" perl -0pi -e '
    open my $f, "<", $ENV{ABOUT} or die; local $/; my $about = <$f>; $about =~ s/\s+\z//;
    s/about \{.*?\n    \}/$about/s or die "about block not found\n";' patches/build.gradle.kts
sed -i "s/^version = .*/version = $VERSION/" gradle.properties
sed -i 's/^rootProject.name = .*/rootProject.name = "waze-morphe-patches"/' settings.gradle.kts

# The single Waze version NL derives its patches against; the worker asks for exactly this APK.
WAZE_VERSION="$(sed -nE 's/.*AppTarget\(version = "([^"]+)".*/\1/p' patches/src/main/kotlin/patches/waze/WazeConstants.kt)"
[[ "$WAZE_VERSION" =~ ^[0-9.]+$ ]] || { echo "cannot read NL's Waze target version" >&2; exit 1; }
echo "$WAZE_VERSION" > "$WORK/waze-version.txt"

# Checks the graft and prune actually happened; a silent no-op would ship the wrong bundle.
grep -q "extensions/$TTS_EXT.mpe" patches/src/main/kotlin/app/template/patches/waze/SystemTtsPatch.kt
grep -q 'Waze Patches' patches/build.gradle.kts
echo "Assembled $OUT (nl $NL_TAG + tts $TTS_TAG, Waze $WAZE_VERSION) as $VERSION"
