# Waze Morphe Patches

One Morphe patch bundle for Waze that combines:

- the Waze patches from [nl-morphe-patches](https://gitlab.com/johninnld/nl-morphe-patches), and
- [morphe-waze-system-tts](https://github.com/CasketPizza/morphe-waze-system-tts) (Android system TTS
  for navigation), retargeted to the Waze version the NL patches support.

New upstream releases are picked up automatically. Each one is checked against the real Waze APK
before it is released.

## Add to Morphe Manager

Add this repository as a patch source. Manager reads `patches-bundle.json` from `main`.

## How it works

```
upstream release ──► pipeline.yml ──► "candidate" prerelease ──► worker page asks for Waze X
                                                                       │ you upload the APKM
                     release v… ◄── pipeline.yml ◄── verified/<key>.json ◄── worker test-patches
```

1. **Sync** (`.github/workflows/pipeline.yml`, every 4 hours): `scripts/sync.sh` moves
   `upstream.lock` to the newest release tag of each upstream.
2. **Build**: `scripts/assemble.sh` clones both upstreams at those tags, keeps only NL's Waze code,
   grafts the TTS patch in with its extension renamed to `waze-system-tts.mpe`, and applies
   `overlay/*.patch`. The bundle is published as the `candidate` prerelease together with
   `candidate.json`, which names the Waze version NL targets.
3. **Verify** (`worker/`): the worker's page shows that Waze version with an APKMirror link. Upload
   the APKM there. The worker checks the package name, version and Waze's signing certificate, then
   applies the TTS patch from the candidate with Morphe. On success it commits
   `verified/<key>.json`. On failure it opens an issue and offers the decompiled `com/waze/sound`
   and `com/waze/settings` sources for download.
4. **Release**: the push of `verified/<key>.json` re-runs the pipeline, which publishes
   `v<nl>-tts.<tts>` and updates `patches-bundle.json` / `patches-list.json`.

`<key>` is a hash of `upstream.lock`, `scripts/assemble.sh`, `scripts/build.sh` and `overlay/`, so a verification
only ever covers the exact inputs it was run against.

### Why the TTS patch is overlaid

Upstream TTS targets Waze 5.24.5.0 by obfuscated names (`com/waze/sound/fb`, `h.g.a.a`, ...),
which Waze renames in every build. `overlay/tts-structural-fingerprints.patch` finds each hook by
what survives renaming instead: Kotlin parameter null-check strings (`"onPlayCompleted"`,
`"cacheKey"`), log strings and framework calls. It also passes the resolved names into the
extension at patch time. If upstream TTS changes the same lines, `git apply` fails and the pipeline
stops instead of shipping a bundle for the wrong Waze version.

## Setup

### 1. Repository

Push this repository to GitHub and make sure Actions can write to the repository (Settings →
Actions → General → Workflow permissions → Read and write).

### 2. Worker token

Create a fine-grained personal access token for **this repository only** with:

- Contents: read and write (commits `verified/*.json`, reads the candidate release)
- Issues: read and write (reports failed verifications)

The worker uses a personal token on purpose. Commits made with the Actions token don't trigger
workflows, but the worker's commit must start the release run.

### 3. Run the worker

The image is built by `.github/workflows/worker-image.yml` and published to
`ghcr.io/johnnycyan/waze-morphe-patches-worker`. If the package is private, run
`docker login ghcr.io` on the host first.

```bash
cd worker
printf 'GITHUB_TOKEN=github_pat_...\nUI_PASSWORD=choose-one\n' > .env
docker compose up -d
```

Open `http://<host>:8080` and sign in with `UI_PASSWORD`. The page refreshes itself when the state
changes. Uploads are sent in chunks (20 MB by default), so they work behind a Cloudflare Tunnel,
which caps each request at 100 MB. An interrupted chunk is retried and resumed automatically.

| Variable | Default | Purpose |
| --- | --- | --- |
| `GITHUB_REPO` | required | `owner/name` of this repository |
| `GITHUB_TOKEN` | required | token from step 2 |
| `UI_PASSWORD` | empty | password for the sign-in page; empty disables sign-in |
| `COOKIE_SECURE` | empty | `1` sends the session cookie only over HTTPS. Set it if you only use the tunnel's HTTPS address, and leave it empty if you also open the page over plain HTTP on your LAN |
| `UPLOAD_CHUNK_MB` | `20` | upload chunk size; keep it well under your proxy's request limit |
| `SECRET_KEY` | generated | signs session cookies; generated into `/data/secret_key` when unset |
| `POLL_SECONDS` | `600` | how often to check for a new candidate |
| `WAZE_CERT_SHA256` | Waze's cert | expected signing certificate of uploads |

Uploaded APKs are kept in `worker/data/apks`. They are never committed or published.

## Local build

```bash
bash scripts/assemble.sh 0.0.0-dev
bash scripts/build.sh 0.0.0-dev
```

The bundle lands in `out/patches.mpp`.
