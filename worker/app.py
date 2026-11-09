"""Waze Patches worker.

Serves a small page that says which Waze version the current candidate bundle needs, links to it
on APKMirror, and accepts the upload. Each upload is checked (package, version, Waze's signing
certificate), then the candidate bundle is test-patched onto it with Morphe. A pass is committed to
the repository as verified/<key>.json, which lets CI publish the release; a failure opens an issue
and leaves decompiled sources here for the fingerprint fix.
"""

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for

REPO = os.environ["GITHUB_REPO"]
TOKEN = os.environ["GITHUB_TOKEN"]
DATA = Path(os.environ.get("DATA_DIR", "/data"))
# SHA-256 of Waze's signing certificate, taken from a Google Play download (5.27.4.900).
WAZE_CERT = os.environ.get(
    "WAZE_CERT_SHA256", "03637f6c5d8f604e6fdb79a6ffbfa578de4e318f8da22fc6106665247f8807d7"
).lower()
PATCH = os.environ.get("PATCH_NAME", "ElevenLabs voice for navigation")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "600"))
UI_PASSWORD = os.environ.get("UI_PASSWORD", "")
MORPHE_JAR = os.environ.get("MORPHE_JAR", "/opt/morphe/morphe.jar")
JADX = os.environ.get("JADX", "/opt/jadx/bin/jadx")
MORPHE_VERSION = os.environ.get("MORPHE_VERSION", "unknown")
CHUNK_BYTES = int(os.environ.get("UPLOAD_CHUNK_MB", "20")) * 1024 * 1024
MAX_UPLOAD = 2 * 1024**3
UPLOAD_EXTS = (".apk", ".apkm", ".xapk", ".apks")

APKS = DATA / "apks"
JOBS = DATA / "jobs"
INCOMING = DATA / "incoming"
for d in (APKS, JOBS, INCOMING):
    d.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

lock = threading.Lock()
state = {"candidate": None, "verified": False, "error": None, "checked_at": None}
running: set[str] = set()


# --- GitHub ------------------------------------------------------------------

class _NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    """Asset downloads redirect to object storage, which rejects the GitHub token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.remove_header("Authorization")
        return new


_opener = urllib.request.build_opener(_NoAuthRedirect)


def gh(method, path, body=None, accept="application/vnd.github+json", raw=False):
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "waze-morphe-patches-worker",
        },
    )
    with _opener.open(req, timeout=120) as resp:
        data = resp.read()
    return data if raw else (json.loads(data) if data else None)


def gh_download(asset, dest: Path):
    data = gh("GET", f"/repos/{REPO}/releases/assets/{asset['id']}", accept="application/octet-stream", raw=True)
    dest.write_bytes(data)


def candidate_release():
    try:
        rel = gh("GET", f"/repos/{REPO}/releases/tags/candidate")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    assets = {a["name"]: a for a in rel["assets"]}
    if "candidate.json" not in assets or "patches.mpp" not in assets:
        return None
    meta = json.loads(gh("GET", f"/repos/{REPO}/releases/assets/{assets['candidate.json']['id']}",
                         accept="application/octet-stream", raw=True))
    meta["mpp_asset"] = assets["patches.mpp"]
    return meta


def verified_record(key):
    try:
        gh("GET", f"/repos/{REPO}/contents/verified/{key}.json")
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


# --- APK inspection ----------------------------------------------------------

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def badging(apk: Path):
    out = run(["aapt", "dump", "badging", str(apk)]).stdout
    m = re.search(r"package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", out)
    if not m:
        raise ValueError(f"{apk.name}: not a readable APK")
    return m.group(1), int(m.group(2)), m.group(3)


def cert_digests(apk: Path):
    out = run(["apksigner", "verify", "--print-certs", str(apk)])
    if out.returncode != 0:
        raise ValueError(f"{apk.name}: signature does not verify")
    # App signers only; Play-delivered APKs also carry a Google "Source Stamp" signer.
    return set(re.findall(r"^Signer #\d+ certificate SHA-256 digest: ([0-9a-f]{64})", out.stdout, re.M))


def inspect(upload: Path, work: Path):
    """Checks an .apk/.apkm/.xapk/.apks upload. Returns (version_name, version_code, base_apk)."""
    with zipfile.ZipFile(upload) as z:
        names = z.namelist()
        if "AndroidManifest.xml" in names:
            apks = [upload]
        else:
            members = [n for n in names if n.endswith(".apk") and "/" not in n]
            if not members:
                raise ValueError("no APKs inside this bundle")
            for n in members:
                z.extract(n, work)
            apks = [work / n for n in members]
    base = next((a for a in apks if a.name in ("base.apk", "com.waze.apk")), None) or apks[0]
    package, code, name = badging(base)
    if package != "com.waze":
        raise ValueError(f"package is {package}, not com.waze")
    for apk in apks:
        digests = cert_digests(apk)
        if digests != {WAZE_CERT}:
            raise ValueError(f"{apk.name} is not signed with Waze's certificate")
    return name, code, base


def stored_apk(version):
    for p in APKS.glob(f"waze-{version}.*"):
        return p
    return None


# --- Verification job --------------------------------------------------------

def job_dir(key) -> Path:
    return JOBS / key


def job_status(key):
    f = job_dir(key) / "status.json"
    return json.loads(f.read_text()) if f.exists() else None


def write_status(key, **fields):
    d = job_dir(key)
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(json.dumps(fields, indent=2))


def verify(cand):
    key = cand["key"]
    d = job_dir(key)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    apk = stored_apk(cand["waze_version"])
    write_status(key, state="running", started=now())
    log = d / "morphe.log"
    try:
        mpp = d / "patches.mpp"
        gh_download(cand["mpp_asset"], mpp)
        with open(log, "w") as out:
            proc = subprocess.run(
                ["java", "-jar", MORPHE_JAR, "patch", "-p", str(mpp), "--exclusive", "-e", PATCH,
                 "--unsigned", "-r", str(d / "result.json"), "-t", str(d / "tmp"), "-o", str(d / "patched.apk"),
                 str(apk)],
                stdout=out, stderr=subprocess.STDOUT, timeout=3600,
            )
        tail = log.read_text(errors="replace")[-6000:]
        ok = proc.returncode == 0 and (d / "patched.apk").exists() and patch_succeeded(d / "result.json")
        shutil.rmtree(d / "tmp", ignore_errors=True)
        (d / "patched.apk").unlink(missing_ok=True)
        if ok:
            record = {
                "ok": True, "key": key, "version": cand["version"], "waze_version": cand["waze_version"],
                "nl_tag": cand["nl_tag"], "commit": cand["commit"],
                "apk_sha256": sha256(apk), "signing_cert_sha256": WAZE_CERT,
                "morphe": MORPHE_VERSION, "verified_at": now(),
            }
            gh("PUT", f"/repos/{REPO}/contents/verified/{key}.json", {
                "message": f"verify: {cand['version']} on Waze {cand['waze_version']}",
                "content": base64.b64encode((json.dumps(record, indent=2) + "\n").encode()).decode(),
            })
            write_status(key, state="verified", finished=now(), log=tail)
        else:
            diagnostics(apk, d)
            issue = open_issue(cand, tail)
            write_status(key, state="failed", finished=now(), log=tail, issue=issue)
    except Exception as e:  # keep the worker alive; show the failure on the page
        write_status(key, state="error", finished=now(), log=f"{type(e).__name__}: {e}")
    finally:
        with lock:
            running.discard(key)


def patch_succeeded(result: Path):
    """Morphe's result file: {"success": bool, "appliedPatches": [names], "failedPatches": [...]}."""
    try:
        data = json.loads(result.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return data.get("success") is True and PATCH in data.get("appliedPatches", []) \
        and not data.get("failedPatches")


def diagnostics(apk: Path, d: Path):
    """Decompiles the sound and settings code the patch fingerprints target, for the fix."""
    with tempfile.TemporaryDirectory(dir=DATA) as tmp:
        work = Path(tmp)
        base = apk
        if apk.suffix != ".apk":
            with zipfile.ZipFile(apk) as z:
                name = next(n for n in z.namelist() if n in ("base.apk", "com.waze.apk"))
                z.extract(name, work)
                base = work / name
        out = work / "jadx"
        run([JADX, "--no-res", "-j", "2", "-d", str(out), str(base)], timeout=3600)
        src = out / "sources" / "com" / "waze"
        with zipfile.ZipFile(d / "diagnostics.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for sub in ("sound", "settings"):
                for f in (src / sub).rglob("*.java"):
                    z.write(f, f.relative_to(out / "sources"))


def open_issue(cand, tail):
    title = f"Verification failed: {cand['version']} on Waze {cand['waze_version']}"
    existing = gh("GET", f"/repos/{REPO}/issues?state=open&per_page=100")
    for issue in existing:
        if issue["title"] == title:
            return issue["html_url"]
    body = (
        f"The ElevenLabs patch did not apply to Waze {cand['waze_version']}.\n\n"
        f"- inputs: `{cand['key']}`\n- Waze target from nl {cand['nl_tag']}\n"
        f"- Decompiled `com/waze/sound` and `com/waze/settings`: download `diagnostics.zip` from the worker page.\n\n"
        f"Fix the fingerprints in `patches/src/main/kotlin/app/waze/elevenlabs/Fingerprints.kt`, push, and the next candidate is verified automatically.\n\n"
        f"<details><summary>Morphe log (tail)</summary>\n\n```\n{tail}\n```\n</details>\n"
    )
    return gh("POST", f"/repos/{REPO}/issues", {"title": title, "body": body})["html_url"]


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path: Path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- Polling -----------------------------------------------------------------

def refresh():
    try:
        cand = candidate_release()
        verified = bool(cand) and verified_record(cand["key"])
        with lock:
            state.update(candidate=cand, verified=verified, error=None, checked_at=now())
    except Exception as e:
        with lock:
            state.update(error=f"{type(e).__name__}: {e}", checked_at=now())
    maybe_start()


def maybe_start(force=False):
    with lock:
        cand, verified = state["candidate"], state["verified"]
        if not cand or verified or not stored_apk(cand["waze_version"]) or cand["key"] in running:
            return False
        status = job_status(cand["key"])
        if status and status["state"] in ("verified", "failed", "error") and not force:
            return False
        running.add(cand["key"])
    threading.Thread(target=verify, args=(cand,), daemon=True).start()
    return True


def sweep_incoming():
    """Drops uploads that were abandoned more than a day ago."""
    cutoff = time.time() - 86400
    for f in INCOMING.iterdir():
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


def poller():
    while True:
        refresh()
        sweep_incoming()
        time.sleep(POLL_SECONDS)


# --- Web ---------------------------------------------------------------------

def _secret_key():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    f = DATA / "secret_key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        f.chmod(0o600)
    return f.read_text().strip()


app.secret_key = _secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    # Lax keeps the cookie off cross-site POSTs, which covers CSRF for this single-user page.
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "") == "1",
    PERMANENT_SESSION_LIFETIME=dt.timedelta(days=30),
)
# Sessions carry a digest of the password, so changing UI_PASSWORD signs everyone out.
_PASSWORD_TAG = hmac.new(app.secret_key.encode(), UI_PASSWORD.encode(), hashlib.sha256).hexdigest()[:32]


@app.before_request
def auth():
    if not UI_PASSWORD or request.endpoint in ("login", "static"):
        return None
    if session.get("auth") == _PASSWORD_TAG:
        return None
    if request.method != "GET" or request.path.startswith("/api/"):
        return jsonify(error="Signed out. Reload the page and sign in."), 401
    return redirect(url_for("login", next=request.full_path.rstrip("?")))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not UI_PASSWORD:
        return redirect("/")
    nxt = request.values.get("next", "/")
    # Local paths only; browsers treat "//host" and "/\host" as another site.
    if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt:
        nxt = "/"
    if request.method == "GET":
        return render_template("login.html", error=None, next=nxt)
    if not hmac.compare_digest(request.form.get("password", "").encode(), UI_PASSWORD.encode()):
        time.sleep(1)  # slows guessing
        return render_template("login.html", error="Wrong password.", next=nxt), 401
    session.clear()
    session.permanent = True
    session["auth"] = _PASSWORD_TAG
    return redirect(nxt)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def apkmirror_links(version):
    dashed = version.replace(".", "-")
    return {
        "release": "https://www.apkmirror.com/apk/waze/waze-gps-maps-traffic-alerts-live-navigation/"
                   f"waze-navigation-live-traffic-{dashed}-release/",
        "search": f"https://www.apkmirror.com/?post_type=app_release&searchtype=apk&s=waze+{version}",
    }


def view_state():
    with lock:
        cand = dict(state["candidate"]) if state["candidate"] else None
        verified, error, checked = state["verified"], state["error"], state["checked_at"]
        busy = bool(cand) and cand["key"] in running
    if cand:
        cand.pop("mpp_asset", None)
    job = job_status(cand["key"]) if cand else None
    have = bool(cand) and stored_apk(cand["waze_version"]) is not None
    if not cand:
        phase = "idle"
    elif verified:
        phase = "verified"
    elif busy or (job and job["state"] == "running"):
        phase = "running"
    elif job and job["state"] in ("failed", "error"):
        phase = job["state"]
    elif have:
        phase = "queued"
    else:
        phase = "needs_apk"
    return {
        "candidate": cand, "phase": phase, "job": job, "error": error, "checked_at": checked,
        "links": apkmirror_links(cand["waze_version"]) if cand else None,
        "diagnostics": bool(cand) and (job_dir(cand["key"]) / "diagnostics.zip").exists(),
        "apks": sorted((p.name, round(p.stat().st_size / 1e6)) for p in APKS.iterdir()),
    }


@app.get("/")
def index():
    return render_template("index.html", s=view_state(), chunk_bytes=CHUNK_BYTES, auth=bool(UI_PASSWORD))


@app.get("/api/status")
def status():
    return jsonify(view_state())


# Uploads arrive in chunks so each request stays under a reverse proxy's body limit
# (Cloudflare: 100 MB) and finishes well inside its ~100 s timeout.

def _upload_paths(upload_id):
    if not re.fullmatch(r"[0-9a-f]{32}", upload_id):
        abort(404)
    meta, part = INCOMING / f"{upload_id}.json", INCOMING / f"{upload_id}.part"
    if not meta.exists() or not part.exists():
        abort(404)
    return json.loads(meta.read_text()), part


@app.post("/upload/start")
def upload_start():
    body = request.get_json(silent=True) or {}
    name, size = str(body.get("name", "")), body.get("size")
    ext = Path(name).suffix.lower()
    if ext not in UPLOAD_EXTS:
        return jsonify(error="Upload an .apk, .apkm, .xapk or .apks file."), 400
    if not isinstance(size, int) or not 0 < size <= MAX_UPLOAD:
        return jsonify(error="File size is missing or too large."), 400
    upload_id = uuid.uuid4().hex
    (INCOMING / f"{upload_id}.json").write_text(json.dumps({"ext": ext, "size": size, "created": time.time()}))
    (INCOMING / f"{upload_id}.part").touch()
    return jsonify(id=upload_id, chunk_bytes=CHUNK_BYTES)


@app.put("/upload/<upload_id>")
def upload_chunk(upload_id):
    meta, part = _upload_paths(upload_id)
    offset = request.args.get("offset", type=int)
    have = part.stat().st_size
    if offset != have:  # lost or repeated chunk: tell the client where to resume
        return jsonify(error="Offset mismatch.", offset=have), 409
    written = 0
    with open(part, "ab") as out:
        while chunk := request.stream.read(1 << 20):
            written += len(chunk)
            if written > CHUNK_BYTES or have + written > meta["size"]:
                out.truncate(have)
                return jsonify(error="Chunk too large."), 413
            out.write(chunk)
    return jsonify(offset=have + written)


@app.post("/upload/<upload_id>/finish")
def upload_finish(upload_id):
    meta, part = _upload_paths(upload_id)
    if part.stat().st_size != meta["size"]:
        return jsonify(error="Upload incomplete.", offset=part.stat().st_size), 409
    ext = meta["ext"]
    tmp = INCOMING / f"{upload_id}{ext}"
    part.rename(tmp)
    (INCOMING / f"{upload_id}.json").unlink(missing_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=DATA) as work:
            version, code, _ = inspect(tmp, Path(work))
    except (ValueError, zipfile.BadZipFile) as e:
        tmp.unlink(missing_ok=True)
        return jsonify(error=f"Rejected: {e}"), 422
    for old in APKS.glob(f"waze-{version}.*"):
        old.unlink()
    # Morphe merges any zip of split APKs; store every bundle format under the extension it expects.
    tmp.rename(APKS / f"waze-{version}{'.apk' if ext == '.apk' else '.apkm'}")
    started = maybe_start(force=True)
    with lock:
        cand = state["candidate"]
    wanted = cand["waze_version"] if cand else None
    note = "" if version == wanted else f" The candidate needs {wanted}, so it was stored but not tested."
    return jsonify(message=f"Stored Waze {version} ({code}).{' Verification started.' if started else ''}{note}")


@app.post("/retry")
def retry():
    refresh()
    return jsonify(started=maybe_start(force=True))


@app.get("/diagnostics.zip")
def diagnostics_zip():
    with lock:
        cand = state["candidate"]
    f = job_dir(cand["key"]) / "diagnostics.zip" if cand else None
    if not f or not f.exists():
        abort(404)
    return send_file(f, as_attachment=True, download_name=f"waze-{cand['waze_version']}-diagnostics.zip")


threading.Thread(target=poller, daemon=True).start()

if __name__ == "__main__":
    from waitress import serve

    serve(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
          max_request_body_size=CHUNK_BYTES + (1 << 20))
