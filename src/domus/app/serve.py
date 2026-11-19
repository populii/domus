"""Serve the viewer, the built model and a small store for what people draw on it.

    domus serve [--host 127.0.0.1] [--port 8765]

Pages:
    /                         the viewer (viewer/index.html is a page body: the claude.ai Artifact
                              publish and this server each add the document skeleton). A model another Domus built
                              is built again when it's opened, one build at a time, and shown as it was meanwhile
    /vendor/...               its bundled libraries
    /data/...                 the built model (the site's output folder), or the bundled demo if nothing is built
    /demo                     the viewer on whatever model there is
    /scan                     the guided scan page for a phone
    /api/scan/<name>          POST: a scan's video or tracked positions, saved to sources/raw/
    /api/scan-job             GET: how the scan's processing is going; POST {file}: start it, one at a time
    /api/create               GET: how the address box's build is going; POST {address}: start one, in an empty site only
    /api/sketch               POST {items}: what a sketch comes to (domus.options.sketch)
    /api/sketch/option        GET: how its build is going; POST {name, items}: save a sketch as an option and build it
    /api/changes              GET: the changes proposed to the site's data, the edits made to it (docs/changes.md), and
                              how the change asked for last is going
    /api/proposals/<id>/accept POST: accept a proposed change and build the model again, as `domus site accept` does
    /api/proposals/<id>/reject POST {why}: reject it, as `domus site reject` does
    /api/edits/<id>/undo      POST: undo an edit and build the model again, as `domus site undo` does
    /healthz                  liveness, for Docker and Traefik
    /api/version              the app's version, and the model's build.json: when it was built and by which Domus
    /api/user                 who the login proxy says this is (only with DOMUS_TRUST_PROXY=1)
    /api/db/<coll>            GET: the documents of a collection
    /api/db/<coll>/<doc>      GET or PUT: one JSON document

With DOMUS_SITES set to a folder, one server holds many sites, each a folder in it. `/` lists them and asks for a new address (POST
/api/sites {address}), and everything above then lives under /s/<name>/ for that site. /demo stays at the top.

Files under /vendor and /data go out gzipped when they're big text and the browser takes it.

Standard library only, so serving needs nothing extra. There is no login here: run it behind a
login proxy, or leave it bound to localhost.
"""

import argparse
import datetime
import email.utils
import functools
import gzip
import html
import http.server
import io
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import tomllib
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .. import __version__
from ..errors import DomusError

VIEWER = Path(__file__).resolve().parent / "viewer"
HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<link rel="icon" href="icon.png">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>:root{color-scheme:light;padding:env(safe-area-inset-top,0px) 0 env(safe-area-inset-bottom,0px)}
body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>
</head><body>
"""
# The viewer's look (viewer/index.html :root) for the pages around it.
PAGE_STYLE = """<style>
:root { --paper: #eef0ec; --panel: #f8f9f6; --ink: #1b2733; --muted: #5d6871; --rule: #cfd5d3; --accent: #0e6f86; --accent-ink: #ffffff;
  --font-display: "Barlow Condensed", "Arial Narrow", "Roboto Condensed", sans-serif; --font-body: "Barlow", "Segoe UI", system-ui, sans-serif;
  --font-data: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace; }
@media (prefers-color-scheme: dark) { :root { --paper: #0f1518; --panel: #141c20; --ink: #dde6e9; --muted: #95a3a9; --rule: #2a363c;
  --accent: #43b9d6; --accent-ink: #062027; color-scheme: dark; } }
body { background: var(--paper); color: var(--ink); font: 14px/1.5 var(--font-body); }
main { max-width: 36rem; margin: 3rem auto; background: var(--panel); border: 1px solid var(--rule); box-sizing: border-box; }
@media (max-width: 40rem) { main { margin: 0; border-width: 0; min-height: 100vh; } }
main > section { padding: 14px 18px; border-bottom: 1px solid var(--rule); display: grid; gap: 8px; }
main > section:last-child { border-bottom: 0; }
h1 { font: 600 26px/1.05 var(--font-display); letter-spacing: 0.01em; margin: 0; }
h2 { font: 600 13px var(--font-display); text-transform: uppercase; letter-spacing: 0.08em; margin: 0; color: var(--muted); }
p { margin: 0; }
a { color: var(--accent); }
.back { font-size: 12.5px; }
.sites { list-style: none; margin: 0; padding: 0; }
.sites li { border-top: 1px dashed var(--rule); }
.sites li:first-child { border-top: 0; }
.sites a { display: flex; justify-content: space-between; gap: 12px; padding: 8px 0; text-decoration: none; font-weight: 500; }
.sites a:hover { text-decoration: underline; }
.state { font: 11.5px var(--font-data); color: var(--muted); }
label { color: var(--muted); font-size: 12.5px; }
input { width: 100%; box-sizing: border-box; font: inherit; color: var(--ink); background: transparent; border: 1px solid var(--rule); border-radius: 3px; padding: 6px 9px; }
input:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
button { font: 500 13px var(--font-body); color: var(--ink); background: transparent; border: 1px solid var(--rule); border-radius: 3px; padding: 5px 9px; cursor: pointer; }
button:hover { border-color: var(--accent); }
button.go { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.choices button { display: block; width: 100%; margin: 0 0 6px; text-align: left; }
form { display: grid; gap: 8px; }
.row { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
pre { white-space: pre-wrap; font: 11.5px/1.5 var(--font-data); color: var(--muted); margin: 0; }
pre:empty, #s:empty, #w:empty { display: none; }
</style>
"""
EMPTY = PAGE_STYLE + """<main><section><!--back--><h1>No model built yet</h1>
<p>This site project has no built model. Run <code>domus build</code>, then reload.</p></section></main>"""
NEW_SITE = PAGE_STYLE + """<main><section><!--back-->
<h1>Build a 3D model of your place</h1>
<p>Enter an address. In New Zealand Domus finds its boundary and buildings and fetches LINZ's LiDAR and aerial photo;
anywhere else it takes OpenStreetMap's buildings and a 30 m ground model, a rougher start. Then it builds the model,
which takes a few minutes.</p>
<form id="f"><label for="a">Address</label>
<input id="a" required maxlength="200" autocomplete="street-address">
<div class="row"><button class="go">Build the model</button> <a href="./demo">or look at a made-up demo</a></div></form>
<p id="s" role="status"></p><div id="w" class="choices"></div><pre id="l"></pre>
<script>
const s = document.getElementById("s"), l = document.getElementById("l"), f = document.getElementById("f"), w = document.getElementById("w");
async function start(address) {
  w.replaceChildren();
  const r = await fetch("api/create", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ address }) });
  if (!r.ok) { s.textContent = (await r.json()).error; return; }
  poll();
}
async function poll() {
  const j = await (await fetch("api/create")).json();
  if (j.state === "choose") {
    s.textContent = "That matches more than one place. Which one?";
    l.textContent = "";
    f.hidden = false;
    w.replaceChildren(...j.places.map((p) => {
      const b = document.createElement("button");
      b.textContent = p;
      b.addEventListener("click", () => start(p));
      return b;
    }));
    return;
  }
  l.textContent = j.log || "";
  if (j.state === "done") { s.textContent = "Done."; location.href = "./"; return; }
  if (j.state === "failed") { s.textContent = "That didn't work. The last lines say why."; f.hidden = false; return; }
  if (j.state === "running") { s.textContent = "Building. You can leave this page and come back."; f.hidden = true; setTimeout(poll, 3000); }
}
f.addEventListener("submit", async (e) => {
  e.preventDefault();
  start(document.getElementById("a").value);
});
poll();
</script></section></main>"""
SITES_PAGE = PAGE_STYLE + """<main><section><h1>Domus</h1><p class="state">3D site models</p></section>
<section><h2>Sites</h2>
@SITES@
</section>
<section><h2>Build a new one</h2>
<p>Enter an address. In New Zealand Domus finds its boundary and buildings and fetches LINZ's LiDAR and aerial photo; anywhere else it takes OpenStreetMap's buildings and a 30 m ground model, a rougher start. Then it builds the model, which takes a few minutes.</p>
<form id="f"><label for="a">Address</label>
<input id="a" required maxlength="200" autocomplete="street-address">
<div class="row"><button class="go">Build the model</button> <a href="demo">or look at a made-up demo</a></div></form>
<p id="s" role="status"></p>
<script>
document.getElementById("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const r = await fetch("api/sites", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ address: document.getElementById("a").value }) });
  const j = await r.json();
  if (r.ok) location.href = j.url; else document.getElementById("s").textContent = j.error;
});
</script></section></main>"""
BACK_LINK = """<p class="back"><a href="../../">&larr; All sites</a></p>"""
MAX_DOC = 2 * 1024 * 1024
CREATE_LOCK = threading.Lock()
MAX_SKETCH = 512 * 1024
WORKER_IDLE_S = 600
WORKER_TIMEOUT_S = 60
MAX_SCAN = 4 * 1024 * 1024 * 1024
KEEP_FREE = 1024 * 1024 * 1024      # an upload that would leave less room than this on the disk is refused
HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    # Nothing loads from, or goes to, another host, and no other site can frame the app. 'wasm-unsafe-eval' lets the
    # viewer compile its own WebAssembly (Manifold, in vendor/manifold-3d): it allows no eval of script.
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; "
                               "style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' data: blob:; "
                               "worker-src 'self' blob:; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
}
# Text files this big or bigger go out gzipped to a browser that takes it: the demo's model.json shrinks to a quarter,
# and Manifold's WebAssembly to two fifths.
GZIP_TYPES = {".css", ".geojson", ".html", ".js", ".json", ".mjs", ".svg", ".wasm"}
GZIP_MIN = 1024
SCAN_NAME = re.compile(r"^[A-Za-z0-9-]{1,40}\.(webm|mp4|json)$")
EDIT_RECORD = r"\d+-[a-z][a-z-]*\.json"        # an edit's record in .domus/edits/: its number and kind, such as 0003-ifc-move.json
CHANGE_PATH = re.compile(r"^/api/(?:proposals/(\d{1,6})/(accept|reject)|edits/(\d{1,6})/(undo))$")
MAX_WHY = 500
EDITS_SHOWN = 50
NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SITE_PATH = re.compile(r"^/s/([a-z0-9-]{1,64})(/.*)?$")


class Store:
    """JSON documents in SQLite, addressed as collection/doc: sketches and their saved copies."""

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS docs (coll TEXT, doc TEXT, body TEXT NOT NULL,
                               updated TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (coll, doc))""")

    def get(self, coll, doc):
        with self.lock:
            row = self.db.execute("SELECT body FROM docs WHERE coll=? AND doc=?", (coll, doc)).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, coll):
        with self.lock:
            rows = self.db.execute("SELECT doc, body FROM docs WHERE coll=? ORDER BY doc", (coll,)).fetchall()
        return [{"id": d, "data": json.loads(b)} for d, b in rows]

    def put(self, coll, doc, body):
        with self.lock, self.db:
            self.db.execute("INSERT INTO docs (coll, doc, body) VALUES (?, ?, ?) ON CONFLICT (coll, doc) DO UPDATE "
                            "SET body=excluded.body, updated=CURRENT_TIMESTAMP", (coll, doc, json.dumps(body)))


def read_address(raw):
    """(address, None), or (None, what's wrong with it)."""
    try:
        address = json.loads(raw)["address"].strip()
    except (ValueError, KeyError, TypeError, AttributeError):
        return None, "send an address"
    if not address or len(address) > 200 or address.startswith("-"):
        return None, "That doesn't look like an address."
    return address, None


def max_sites():
    return int(os.environ.get("DOMUS_MAX_SITES", "100"))


def host_allowed(header):
    """Whether a Host header names this server: an IP address, localhost, or a name in DOMUS_ALLOWED_HOSTS.
    A hostile web page can point its own name at this machine, and its requests then carry that name."""
    if not header:
        return True
    if header.startswith("["):
        host = header[1:header.find("]")]
    else:
        host = header.rpartition(":")[0] if ":" in header else header
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return host in {h.strip().lower() for h in os.environ.get("DOMUS_ALLOWED_HOSTS", "").split(",")}


def _inside(base, rel):
    """`rel` under `base`, or a path that doesn't exist if it would leave it (by `..`, an absolute path or a link)."""
    base = os.path.realpath(base)
    if not rel:
        return base
    target = os.path.realpath(os.path.join(base, unquote(rel)))
    if not target.startswith(base + os.sep):
        return os.path.join(base, "__missing__")
    return target


def accepts_gzip(header):
    """True if an Accept-Encoding header takes gzip, and doesn't refuse it with q=0."""
    for part in (header or "").split(","):
        coding, *params = [p.strip() for p in part.split(";")]
        if coding.lower() == "gzip":
            weights = [p[2:] for p in (p.replace(" ", "").lower() for p in params) if p.startswith("q=")]
            try:
                return not weights or float(weights[0]) > 0
            except ValueError:
                return False
    return False


@functools.lru_cache(maxsize=32)
def gzipped(path, mtime_ns, size):
    """A file's bytes gzipped, kept until the file changes (its time and size are part of the key)."""
    return gzip.compress(Path(path).read_bytes(), compresslevel=6, mtime=0)


def job_env(root, own_output):
    """The environment of a job on this site, which is how the job's modules find it."""
    env = {**os.environ, "DOMUS_SITE": str(root), "PYTHONUNBUFFERED": "1"}
    if own_output:
        env.pop("DOMUS_OUTPUT", None)
    return env


class Job:
    """One Domus command at a time on a site, in a process of its own (the server imports no site's code), its output
    in the site's .domus/<name>.log. `status()` gives its state (idle, running, done or failed) and its log's last
    `tail` lines."""

    def __init__(self, root, name, own_output=False, tail=10):
        self.root = Path(root)
        self.own_output = own_output    # one of many sites: its build goes in its own folder, whatever the environment says
        self.proc = None
        self.log = self.root / ".domus" / f"{name}.log"
        self.tail = tail

    def run(self, *args):
        """Starts `domus ARGS` on the site."""
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log, "wb") as fh:
            self.proc = subprocess.Popen([sys.executable, "-m", "domus.app.cli", *args], stdout=fh, stderr=subprocess.STDOUT,
                                         env=job_env(self.root, self.own_output))

    def text(self):
        return self.log.read_text(encoding="utf-8", errors="replace") if self.log.exists() else ""

    def state(self):
        code = self.proc.poll() if self.proc else None
        return "idle" if self.proc is None else "running" if code is None else "done" if code == 0 else "failed"

    def status(self):
        return {"state": self.state(), "log": "\n".join(self.text().splitlines()[-self.tail:])}


class Creator(Job):
    """One `domus create` at a time, for the address box, and only into an empty site. `output` is where the site's
    model is built (DOMUS_OUTPUT, as the server read it), unless the site builds into its own viewer/data."""

    def __init__(self, root, own_output=False, output=None):
        super().__init__(root, "create", own_output, tail=12)
        self.output = output

    def allowed(self):
        """An empty site, or one whose build from an address stopped short (running it again carries on)."""
        if not (self.root / "data" / "site.toml").exists():
            return True
        return (self.root / "data" / "public-lookup.json").exists() and not self.built()

    def built(self):
        out = "viewer/data" if self.own_output else self.output if self.output is not None else "viewer/data"
        return (self.root / out / "model.json").exists()

    def start(self, address):
        self.run("create", address, str(self.root))

    def status(self):
        """As a Job's, or `choose` with the places to choose from when the address named more than one."""
        which = next((ln[len("WHICH: "):] for ln in reversed(self.text().splitlines()) if ln.startswith("WHICH: ")), None)
        if self.state() == "failed" and which:
            return {"state": "choose", **json.loads(which)}
        return super().status()


class ScanJob(Job):
    """One `domus video auto` at a time, for the scan page."""

    def __init__(self, root, own_output=False):
        super().__init__(root, "scan", own_output, tail=10)

    def start(self, rel):
        self.run("video", "auto", rel)


class BuildJob(Job):
    """One `domus build` at a time, after a sketch is saved as an option."""

    def __init__(self, root, own_output=False):
        super().__init__(root, "build", own_output, tail=6)
        self.tried = None       # the Domus a stale model was last rebuilt for: a failed rebuild waits for the next one

    def start(self):
        self.run("build")


class ChangeJob(Job):
    """One change to the site's data at a time from the viewer, run as its command runs it (`domus site accept`,
    `reject` or `undo`), with a build after one that changes the data."""

    def __init__(self, root, own_output=False):
        super().__init__(root, "change", own_output, tail=8)

    def start(self, *args):
        self.run("site", *args)


def _json_files(folder, pattern):
    out = []
    for path in sorted(Path(folder).glob("*.json")) if Path(folder).is_dir() else ():
        if re.fullmatch(pattern, path.name):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue        # one being written: the next look sees it whole
    return out


def changes(root):
    """The site's proposals, and its edits (the newest EDITS_SHOWN, newest first), as the viewer lists them: read from
    .domus/proposals/ and .domus/edits/, as domus.project.changes writes them."""
    root = Path(root)
    keep = ("id", "made", "source", "why", "summary", "state", "accepted", "rejected")
    proposals = [{k: p[k] for k in keep if k in p} for p in _json_files(root / ".domus" / "proposals", r"\d+\.json")]
    records = sorted(_json_files(root / ".domus" / "edits", EDIT_RECORD), key=lambda r: int(r.get("id", 0)))
    undone = {}
    for r in reversed(records):          # an undo comes after what it undoes, so its own state is known first
        r["in_effect"] = r.get("id") not in undone
        if r["in_effect"] and r.get("undoes"):
            undone[r["undoes"]] = r["id"]
        if r.get("id") in undone:
            r["undone_by"] = undone[r["id"]]
    keep = ("id", "kind", "who", "when", "why", "summary", "undoes", "in_effect", "undone_by", "basis", "rebuild")
    edits = [{k: r[k] for k in keep if k in r} for r in reversed(records[-EDITS_SHOWN:])]
    return {"proposals": proposals, "edits": edits, "count": len(records)}


class SketchWorker:
    """A `domus sketch-worker` for one site, so its ground is read once. It stops when idle, and starts again when the site's files change."""

    def __init__(self, root, own_output=False):
        self.root = Path(root)
        self.own_output = own_output
        self.proc = None
        self.seen = None
        self.lock = threading.Lock()
        self.idle = None

    def signature(self):
        files = [self.root / "options" / "_site.toml", self.root / "data" / "site.toml", *(self.root / "data" / "site").glob("*.geojson"), *(self.root / "data" / "lidar").glob("*.tif")]
        return tuple(sorted((f.name, f.stat().st_mtime_ns) for f in files if f.exists()))

    def stop(self):
        if self.idle:
            self.idle.cancel()
        with self.lock:
            self._stop()

    def _stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None

    def call(self, items):
        if not (self.root / "options" / "_site.toml").exists():
            return {"error": "This site has no options/_site.toml, which sets where options can regrade the ground."}
        with self.lock:
            sig = self.signature()
            if self.proc is None or self.proc.poll() is not None or sig != self.seen:
                self._stop()
                self.proc = subprocess.Popen([sys.executable, "-m", "domus.app.cli", "sketch-worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             text=True, encoding="utf-8", env=job_env(self.root, self.own_output))   # its errors: the server's log
                self.seen = sig
            timer = threading.Timer(WORKER_TIMEOUT_S, self.proc.kill)   # a stuck worker ends the read below
            timer.start()
            try:
                self.proc.stdin.write(json.dumps({"items": items}) + "\n")
                self.proc.stdin.flush()
                line = self.proc.stdout.readline()
            except OSError:
                line = ""
            finally:
                timer.cancel()
            if not line:
                self._stop()
                return {"error": "The sketch couldn't be worked out. The server's log says why."}
            if self.idle:
                self.idle.cancel()
            self.idle = threading.Timer(WORKER_IDLE_S, self.stop)
            self.idle.daemon = True
            self.idle.start()
            return json.loads(line)


def save_option(root, name, items, own_output, who=None):
    """Save a sketch as a wall option through `domus sketch-option`, in a process of its own on the site its environment
    names, which reads its settings and writes options/sketch-<name>.toml, never over an earlier one, by an edit on the
    change route recorded with `who`. Gives the file."""
    out = subprocess.run([sys.executable, "-m", "domus.app.cli", "sketch-option"],
                         input=json.dumps({"name": name, "items": items, "who": who}),
                         capture_output=True, text=True, encoding="utf-8", env=job_env(root, own_output), check=False,
                         timeout=WORKER_TIMEOUT_S)
    if out.returncode:
        sys.stderr.write(out.stderr)            # all of it, in the server's log
        raise DomusError("That sketch couldn't be saved as an option. The server's log says why.")
    answer = json.loads(out.stdout)
    if "error" in answer:                       # the site's settings or data need putting right: the message says what
        raise DomusError(answer["error"])
    return Path(root) / answer["file"]


class Handler(http.server.SimpleHTTPRequestHandler):
    store = None
    sketch_worker = None
    build_job = None
    creator = None
    scan_job = None
    change_job = None
    data_dirs = ()
    trust_proxy = False
    varies = False          # the response depends on Accept-Encoding

    def log_message(self, format, *args):
        if os.environ.get("DOMUS_ACCESS_LOG"):
            super().log_message(format, *args)

    def parse_request(self):
        if not super().parse_request():
            return False
        if not host_allowed(self.headers.get("Host")):
            self.send_json({"error": "This host name isn't allowed. Add it to DOMUS_ALLOWED_HOSTS."}, 403)
            return False
        return True

    def end_headers(self):
        for name, value in HEADERS.items():
            self.send_header(name, value)
        if self.varies:
            self.send_header("Vary", "Accept-Encoding")
        super().end_headers()

    def send_head(self):
        """A file from the viewer or the model, gzipped when it's a big text file and the browser takes gzip."""
        path = Path(self.translate_path(self.path))
        self.varies = path.suffix.lower() in GZIP_TYPES and path.is_file()
        if not self.varies or not accepts_gzip(self.headers.get("Accept-Encoding")):
            return super().send_head()
        try:
            st = path.stat()
            if st.st_size < GZIP_MIN:
                return super().send_head()
            if self.unchanged_since(st.st_mtime):
                self.send_response(304)
                self.end_headers()
                return None
            body = gzipped(str(path), st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            return super().send_head()      # gone since we looked: the standard library's 404
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Last-Modified", self.date_time_string(st.st_mtime))
        self.end_headers()
        return io.BytesIO(body)

    def unchanged_since(self, mtime):
        """True if the request's If-Modified-Since is no older than the file, to the second, as the standard library judges it."""
        if "If-Modified-Since" not in self.headers or "If-None-Match" in self.headers:
            return False
        try:
            since = email.utils.parsedate_to_datetime(self.headers["If-Modified-Since"])
        except (TypeError, IndexError, OverflowError, ValueError):
            return False
        if since.tzinfo is None:
            since = since.replace(tzinfo=datetime.UTC)
        return int(mtime) <= since.timestamp()

    def send_json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def drain(self, cap=65536):
        """Reads the request's body (up to `cap` bytes) before an answer that doesn't need it: the server closes the
        connection after answering, and a body left unread then resets it, which can lose the answer on its way."""
        try:
            length = int(self.headers.get("Content-Length", "") or 0)
        except ValueError:
            return
        if length > 0:
            self.rfile.read(min(length, cap))

    def data_dir(self):
        return next((d for d in self.data_dirs if (d / "model.json").exists()), None)

    def translate_path(self, path):
        # Only the viewer's folder and the model's data are served.
        clean = urlsplit(path).path
        data = self.data_dir()
        if data is not None and clean.startswith("/data/"):
            return _inside(data, clean[len("/data/"):])
        return _inside(VIEWER, clean.lstrip("/"))

    def db_route(self):
        parts = [unquote(p) for p in urlsplit(self.path).path.split("/")[3:]]
        return parts if 1 <= len(parts) <= 2 and all(NAME.match(p) for p in parts) else None

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/healthz":
            return self.send_json({"ok": True})
        if path == "/api/version":
            data = self.data_dir()
            built = data / "build.json" if data else None
            model = json.loads(built.read_text(encoding="utf-8")) if built and built.exists() else None
            return self.send_json({"version": __version__, "model": model})
        if path == "/api/user":
            return self.send_json({"id": self.user()})
        if path == "/api/create":
            return self.send_json(self.creator.status())
        if path == "/api/scan-job":
            return self.send_json(self.scan_job.status())
        if path == "/api/sketch/option":
            return self.send_json(self.build_job.status())
        if path == "/api/changes" and self.change_job is not None:
            return self.send_json({**changes(self.change_job.root), "job": self.change_job.status()})
        if path.startswith("/api/db/"):
            route = self.db_route()
            if route is None:
                return self.send_json({"error": "not found"}, 404)
            if len(route) == 1:
                return self.send_json(self.store.list(route[0]))
            doc = self.store.get(*route)
            if doc is None:
                return self.send_json({"error": "not found"}, 404)
            return self.send_json({"id": route[1], "data": doc})
        if path.startswith("/api/"):
            return self.send_json({"error": "not found"}, 404)
        if path == "/scan":
            return self.send_page((VIEWER / "scan.html").read_text(encoding="utf-8"))
        if path in ("/", "/index.html", "/demo"):
            # A site of its own with no model yet asks for an address, or shows how the build is going.
            asking = path != "/demo" and not (self.data_dirs[0] / "model.json").exists() and (
                self.creator.allowed() or self.creator.status()["state"] in ("running", "failed", "choose"))
            if asking:
                page = NEW_SITE
            else:
                page = (VIEWER / "index.html").read_text(encoding="utf-8") if self.data_dir() else EMPTY
                if path != "/demo":
                    self.rebuild_if_stale()
            return self.send_page(page)
        return super().do_GET()

    def building(self):
        """True while a model is being made or built here, or its data changed. The server runs one build at a time."""
        return any(job is not None and job.status()["state"] == "running"
                   for job in (self.creator, self.build_job, self.change_job))

    def user(self):
        """Who the login proxy says this is (only with DOMUS_TRUST_PROXY=1), else None."""
        if not self.trust_proxy:
            return None
        return (self.headers.get("X-Forwarded-Preferred-Username") or self.headers.get("X-Forwarded-User")
                or self.headers.get("X-Forwarded-Email"))

    def rebuild_if_stale(self):
        """A site's model that another Domus built is built again when the site is opened, one build at a time, and
        shown as it was meanwhile. A rebuild that fails isn't tried again until the server restarts."""
        out, job = (self.data_dirs[0] if self.data_dirs else None), self.build_job
        if out is None or job is None or not (out / "model.json").exists() or not (job.root / "data" / "site.toml").exists():
            return
        try:
            built = json.loads((out / "build.json").read_text(encoding="utf-8")).get("domus")
        except (OSError, ValueError, AttributeError):
            built = None
        if built == __version__:
            return
        with CREATE_LOCK:
            if job.tried == __version__ or self.building():
                return
            job.tried = __version__
            job.start()
        print(f"{job.root}: {'Domus ' + built if built else 'an older Domus'} built its model, so Domus {__version__} is "
              f"building it again (log: {job.log}).", file=sys.stderr, flush=True)

    def send_page(self, page):
        body = (HEAD + page + "\n</body></html>\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def start_scan_job(self, raw):
        try:
            rel = json.loads(raw)["file"]
        except (ValueError, KeyError, TypeError):
            return self.send_json({"error": "send a file"}, 400)
        m = re.fullmatch(r"sources/raw/scan-([A-Za-z0-9-]{1,40}\.(?:webm|mp4))", rel) if isinstance(rel, str) else None
        if not m or not (self.creator.root / rel).is_file():
            return self.send_json({"error": "no such scan"}, 404)
        with CREATE_LOCK:
            if self.scan_job.status()["state"] == "running":
                return self.send_json({"error": "A scan is already being processed."}, 409)
            self.scan_job.start(rel)
        return self.send_json({"state": "running"}, 202)

    def save_scan(self, name):
        """Stream an upload to sources/raw/, under a name of our own and never over an earlier one."""
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.send_json({"error": "length required"}, 411)
        if not 0 < length <= MAX_SCAN:
            return self.send_json({"error": "too large"}, 413)
        ok_type = self.headers.get_content_type() == "application/octet-stream"
        if not ok_type or not SCAN_NAME.match(name):
            self.rfile.read(min(length, 65536))
            return self.send_json({"error": "bad name"} if ok_type else {"error": "send application/octet-stream"}, 400 if ok_type else 415)
        rel = f"sources/raw/scan-{name}"
        path = self.creator.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(path.parent).free < length + KEEP_FREE:
            self.rfile.read(min(length, 65536))
            return self.send_json({"error": "There isn't enough room on the disk for this."}, 507)
        try:
            fh = open(path, "xb")  # noqa: SIM115 - closed by the with below, after the check for an earlier file
        except FileExistsError:
            self.rfile.read(min(length, 65536))
            return self.send_json({"error": "already saved"}, 409)
        left = length
        with fh:
            while left:
                chunk = self.rfile.read(min(left, 1024 * 1024))
                if not chunk:
                    break
                fh.write(chunk)
                left -= len(chunk)
        if left:     # the phone went away part way: don't leave half a video looking like a whole one
            path.unlink(missing_ok=True)
            return None
        return self.send_json({"file": rel}, 201)

    def sketch_post(self, path):
        """Sketches: bigger than other posts, so they come ahead of the size check."""
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.send_json({"error": "length required"}, 411)
        if not 0 <= length <= MAX_SKETCH:
            return self.send_json({"error": "too large"}, 413)
        raw = self.rfile.read(length)
        if self.headers.get_content_type() != "application/json":
            return self.send_json({"error": "send application/json"}, 415)
        try:
            body = json.loads(raw)
            items = body["items"]
            if not isinstance(items, list):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return self.send_json({"error": "send a sketch's items"}, 400)
        if path == "/api/sketch":
            return self.send_json(self.sketch_worker.call(items))
        name = body.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            return self.send_json({"error": "Give the option a name."}, 400)
        with CREATE_LOCK:
            if self.building():
                return self.send_json({"error": "A build is already running."}, 409)
            try:
                saved = save_option(self.build_job.root, name.strip(), items, self.build_job.own_output,
                                    (self.user() or "the viewer")[:80])
            except DomusError as e:
                return self.send_json({"error": str(e)}, 400)
            self.build_job.start()
        return self.send_json({"file": saved.relative_to(self.build_job.root).as_posix()}, 202)

    def change(self, m, raw):
        """Starts accepting or rejecting a proposal, or undoing an edit (`m`, the path's match), as the viewer asks: one
        change at a time, and not while the model's being built."""
        number, action = (m[1], m[2]) if m[1] else (m[3], m[4])
        root = self.change_job.root
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self.send_json({"error": "not JSON"}, 400)
        why = body.get("why") if isinstance(body, dict) else None
        if action == "reject" and (not isinstance(why, str) or not why.strip() or len(why) > MAX_WHY):
            return self.send_json({"error": "Say why it's rejected."}, 400)
        if action != "undo":
            try:
                state = json.loads((root / ".domus" / "proposals" / f"{int(number):04d}.json").read_text(encoding="utf-8"))["state"]
            except (OSError, ValueError, KeyError, TypeError):
                return self.send_json({"error": "There's no such proposal."}, 404)
            if state != "open":
                return self.send_json({"error": f"That proposal was {state} already."}, 409)
        elif not any(str(r.get("id")).isdigit() and int(r["id"]) == int(number)
                     for r in _json_files(root / ".domus" / "edits", EDIT_RECORD)):
            return self.send_json({"error": "There's no such edit."}, 404)
        who = ["--who", (self.user() or "the viewer")[:80]]
        args = {"accept": ["accept", number, *who, "--build"], "undo": ["undo", number, *who, "--build"],
                "reject": ["reject", number, "--why", why.strip() if isinstance(why, str) else "", *who]}[action]
        with CREATE_LOCK:
            if self.building():
                return self.send_json({"error": "The model is being built or changed: try again when that's done."}, 409)
            self.change_job.start(*args)
        return self.send_json({"state": "running"}, 202)

    def do_POST(self):
        path = urlsplit(self.path).path
        if path in ("/api/sketch", "/api/sketch/option"):
            return self.sketch_post(path)
        if path.startswith("/api/scan/"):
            return self.save_scan(unquote(path[len("/api/scan/"):]))
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.send_json({"error": "length required"}, 411)
        if not 0 <= length <= 4096:
            return self.send_json({"error": "too large"}, 413)
        raw = self.rfile.read(length)
        changing = CHANGE_PATH.match(path) if self.change_job is not None else None
        if path not in ("/api/create", "/api/scan-job") and not changing:
            return self.send_json({"error": "not found"}, 404)
        if self.headers.get_content_type() != "application/json":
            return self.send_json({"error": "send application/json"}, 415)
        if changing:
            return self.change(changing, raw)
        if path == "/api/scan-job":
            return self.start_scan_job(raw)
        address, problem = read_address(raw)
        if problem:
            return self.send_json({"error": problem}, 400)
        with CREATE_LOCK:
            if self.creator.status()["state"] == "running":
                return self.send_json({"error": "A model is already being built."}, 409)
            if not self.creator.allowed():
                return self.send_json({"error": "This site already has a project."}, 409)
            self.creator.start(address)
        return self.send_json({"state": "running"}, 202)

    def do_PUT(self):
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.send_json({"error": "length required"}, 411)
        if not 0 <= length <= MAX_DOC:
            return self.send_json({"error": "too large"}, 413)
        raw = self.rfile.read(length)
        route = self.db_route() if urlsplit(self.path).path.startswith("/api/db/") else None
        if route is None or len(route) != 2:
            return self.send_json({"error": "not found"}, 404)
        # A JSON content type can't be sent cross-site without a preflight, which this server never answers.
        if self.headers.get_content_type() != "application/json":
            return self.send_json({"error": "send application/json"}, 415)
        try:
            body = json.loads(raw)
        except ValueError:
            return self.send_json({"error": "not JSON"}, 400)
        self.store.put(*route, body)
        return self.send_json({"id": route[1]})


def site_name(address, taken):
    """A folder name from an address, and not one already used: 12 Example Road, Town -> 12-example-road-town."""
    plain = unicodedata.normalize("NFKD", address).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", plain).strip("-")[:56].strip("-") or "site"
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}-{n}"
    return name


class Sites:
    """A folder of site projects, each with its own store and jobs, made as they're first asked for. One build runs at a time."""

    def __init__(self, root, demo=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.demo = Path(demo) if demo else None
        self.open = {}
        self.lock = threading.Lock()
        self.top_store = Store(self.root / ".domus" / "viewer.db")

    def names(self):
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and SITE_PATH.match(f"/s/{p.name}"))

    def get(self, name):
        with self.lock:
            if name not in self.open:
                root = self.root / name
                if name not in self.names():
                    return None
                self.open[name] = {"store": Store(root / ".domus" / "viewer.db"), "creator": Creator(root, own_output=True),
                                   "scan_job": ScanJob(root, own_output=True), "sketch_worker": SketchWorker(root, own_output=True), "build_job": BuildJob(root, own_output=True),
                                   "change_job": ChangeJob(root, own_output=True),
                                   "data_dirs": (root / "viewer" / "data", *((self.demo,) if self.demo else ()))}
            return self.open[name]

    def building(self):
        with self.lock:
            return any(c[job].status()["state"] == "running" for c in self.open.values()
                       for job in ("creator", "build_job", "change_job"))

    def listing(self):
        """(name, title, state) of each site."""
        out = []
        for name in self.names():
            c = self.get(name)["creator"]
            title = name
            try:
                title = tomllib.loads((c.root / "data" / "site.toml").read_text(encoding="utf-8"))["site"]["title"]
            except (OSError, ValueError, KeyError):
                pass
            state = c.status()["state"]
            out.append((name, title, "building" if state == "running" else "" if c.built() else "not finished"))
        return out

    def page(self):
        rows = "".join(f'<li><a href="s/{html.escape(n)}/"><span>{html.escape(t)}</span><span class="state">{s}</span></a></li>' for n, t, s in self.listing())
        return SITES_PAGE.replace("@SITES@", f'<ul class="sites">{rows}</ul>' if rows else "<p>No sites yet.</p>")

    def create(self, address):
        """Start a new site's build: its folder's name, or None if another build is running."""
        if self.building():
            return None
        name = site_name(address, set(self.names()))
        (self.root / name).mkdir()
        self.get(name)["creator"].start(address)
        return name


class SitesHandler(Handler):
    """Handler, for the site a /s/<name>/ path names. Outside one, only the list and the made-up demo."""
    sites = None
    in_site = False
    add_slash = None

    def enter(self):
        """True in a site, with this request bound to it and its path made the site's own; False if there's no such site; None outside any."""
        parts = urlsplit(self.path)
        m = SITE_PATH.match(parts.path)
        if not m:
            self.store, self.data_dirs = self.sites.top_store, (self.sites.demo,) if self.sites.demo else ()
            return None
        ctx = self.sites.get(m[1])
        if ctx is None:
            return False
        for k, v in ctx.items():
            setattr(self, k, v)
        self.in_site = True
        if m[2] is None:
            # The site's own name, as the server lists it, not the request's text, goes into the Location header.
            self.add_slash = next((f"/s/{n}/" for n in self.sites.names() if n == m[1]), "/")
        self.path = (m[2] or "/") + (f"?{parts.query}" if parts.query else "")
        return True

    def building(self):
        return self.sites.building()

    def send_page(self, page):
        if self.in_site:
            page = page.replace('href="./demo"', 'href="../../demo"').replace("<!--back-->", BACK_LINK)
        super().send_page(page)

    def do_GET(self):
        entered = self.enter()
        if entered is False:
            return self.send_json({"error": "no such site"}, 404)
        if entered:
            if self.add_slash:
                self.send_response(301)
                self.send_header("Location", self.add_slash)
                self.send_header("Content-Length", "0")
                return self.end_headers()
            return super().do_GET()
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self.send_page(self.sites.page())
        if path in ("/scan", "/api/create", "/api/scan-job", "/api/sketch/option", "/api/changes") or (path == "/demo" and not self.data_dirs):
            return self.send_json({"error": "not found"}, 404)
        return super().do_GET()

    def do_POST(self):
        entered = self.enter()
        if entered is False:
            self.drain()
            return self.send_json({"error": "no such site"}, 404)
        if entered:
            if urlsplit(self.path).path == "/api/create" and self.sites.building():
                self.drain()
                return self.send_json({"error": "Another model is being built. Try again when it's done."}, 409)
            return super().do_POST()
        if urlsplit(self.path).path != "/api/sites":
            self.drain()
            return self.send_json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.send_json({"error": "length required"}, 411)
        if not 0 <= length <= 4096:
            return self.send_json({"error": "too large"}, 413)
        raw = self.rfile.read(length)
        if self.headers.get_content_type() != "application/json":
            return self.send_json({"error": "send application/json"}, 415)
        address, problem = read_address(raw)
        if problem:
            return self.send_json({"error": problem}, 400)
        if len(self.sites.names()) >= max_sites():
            return self.send_json({"error": "This server has as many sites as it's set to hold."}, 409)
        with CREATE_LOCK:
            name = self.sites.create(address)
        if name is None:
            return self.send_json({"error": "Another model is being built. Try again when it's done."}, 409)
        return self.send_json({"url": f"s/{name}/"}, 202)

    def do_PUT(self):
        if self.enter() is False:
            self.drain()
            return self.send_json({"error": "no such site"}, 404)
        return super().do_PUT()


def make_sites_server(host, port, root, demo=None, trust_proxy=False):
    cls = type("BoundSitesHandler", (SitesHandler,), {"sites": Sites(root, demo), "trust_proxy": trust_proxy})
    return http.server.ThreadingHTTPServer((host, port), functools.partial(cls, directory=str(VIEWER)))


def make_server(host, port, data_dirs, db_path, trust_proxy=False, root=None, output=None):
    """The server of one site, in `root`, its model built into `output` (DOMUS_OUTPUT as main read it, if set)."""
    cls = type("BoundHandler", (Handler,), {"store": Store(db_path), "data_dirs": tuple(Path(d) for d in data_dirs),
                                            "trust_proxy": trust_proxy, "creator": Creator(root or Path(db_path).parent / "site",
                                                                                           output=output),
                                            "scan_job": ScanJob(root or Path(db_path).parent / "site"),
                                            "sketch_worker": SketchWorker(root or Path(db_path).parent / "site"),
                                            "build_job": BuildJob(root or Path(db_path).parent / "site"),
                                            "change_job": ChangeJob(root or Path(db_path).parent / "site")})
    httpd = http.server.ThreadingHTTPServer((host, port), functools.partial(cls, directory=str(VIEWER)))
    httpd.handler_class_worker = lambda: cls.sketch_worker
    return httpd


def main(argv=None):
    ap = argparse.ArgumentParser(prog="domus serve", description="Serve the viewer and the built model.")
    ap.add_argument("--host", default=os.environ.get("DOMUS_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("DOMUS_PORT", "8765")))
    args = ap.parse_args(argv)
    trust = os.environ.get("DOMUS_TRUST_PROXY") == "1"
    if os.environ.get("DOMUS_SITES"):
        with make_sites_server(args.host, args.port, os.environ["DOMUS_SITES"], os.environ.get("DOMUS_DEMO_DATA"), trust) as httpd:
            print(f"Sites on http://{args.host}:{args.port}/  (Ctrl+C to stop)", flush=True)
            httpd.serve_forever()
        return
    # The site, read from the environment once, here, and handed down; its jobs get its folder.
    root = Path(os.environ.get("DOMUS_SITE", ".")).resolve()
    output = os.environ.get("DOMUS_OUTPUT")
    dirs = [Path(output if output is not None else root / "viewer" / "data").resolve()]
    if os.environ.get("DOMUS_DEMO_DATA"):
        dirs.append(Path(os.environ["DOMUS_DEMO_DATA"]))
    db = os.environ.get("DOMUS_DB", str(root / ".domus" / "viewer.db"))
    with make_server(args.host, args.port, dirs, db, trust, root, output) as httpd:
        print(f"Viewer on http://{args.host}:{args.port}/  (Ctrl+C to stop)", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
