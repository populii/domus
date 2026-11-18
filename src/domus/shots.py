"""Pictures of the viewer, from a spec: `domus shots SPEC.json`.

    domus shots SPEC.json [--timeout 600]

It serves the viewer and the site's built model on a free port of this machine's own address (127.0.0.1), opens the
viewer in Playwright's Chromium with no window on screen, and for each shot shows a wall option, sets the camera and
saves a PNG of the model. The server and the browser stop when it ends, however it ends, and the whole run has a time
limit: every wait in it is cut short by what's left of `--timeout`.

SPEC.json, all but `shots` optional (docs/shots.md has more, and examples on the made-up demo site):

    {"window": [1280, 720],        the window, and so each picture, in CSS pixels
     "scale": 1,                   device pixels to a CSS pixel: 2 makes pictures twice as wide and high
     "hide": ["Vegetation"],       layer groups to switch off, as the viewer's Layers list names them
     "out": "pictures",            the folder for the pictures, from the spec's own folder (the default)
     "shots": [{"name": "now", "view": "Overview"},
               {"name": "a", "option": "a-one-wall", "pos_so": [-6, 11, 46], "target_so": [12, 11, 41]}]}

A shot names its picture, `<name>.png`, and shows a wall option by its id (`option`; "" or none, the site as it is). Its
camera is a view button's name (`view`: Overview, Plan or one of `[views]` in data/site.toml), or where it stands and
what it looks at: in viewer metres (`pos`, `target`: x east, y up and z south of the model's origin), in the wall
options' frame (`pos_so`, `target_so`: s, o and a height) or on the site's grid (`pos_en`, `target_en`: E, N and a
height). With none of these it's the Overview.

Each picture is the whole window showing the model alone: the panel, the title block, the navigation pad and the
option buttons are hidden, and the view's compass stays in the top right corner. Its PNG holds the image and nothing
else: no text, time or other metadata.

Playwright and its Chromium are an optional extra, imported only here: `uv sync --extra shots`, then
`uv run playwright install chromium`.
"""

import argparse
import functools
import http.server
import json
import math
import os
import re
import struct
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .errors import DomusError

VIEWER = Path(__file__).resolve().parent / "app" / "viewer"      # the app's viewer: its files, not its code
# The document skeleton domus serve wraps the viewer's page body in (domus.app.serve.HEAD, which this module may not
# import): the tests hold the two the same.
HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<link rel="icon" href="icon.png">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>:root{color-scheme:light;padding:env(safe-area-inset-top,0px) 0 env(safe-area-inset-bottom,0px)}
body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>
</head><body>
"""
WINDOW = [1280, 720]
MAX_SIDE = 8192
MAX_SCALE = 4
TIMEOUT_S = 600.0
VIEWS = ("Overview", "Plan")        # the viewer's own view buttons; a site's [views] add more
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")
SPEC_KEYS = {"window", "scale", "hide", "out", "shots"}
# Where a shot's camera stands and what it looks at, by the key that gives each: viewer metres, the wall options'
# frame (s, o, height) or the site's grid (E, N, height).
CAMERA = {"pos": ("pos", "viewer"), "pos_so": ("pos", "so"), "pos_en": ("pos", "en"),
          "target": ("target", "viewer"), "target_so": ("target", "so"), "target_en": ("target", "en")}
SHOT_KEYS = {"name", "option", "view", *CAMERA}
INSTALL = ("Pictures need Playwright and its Chromium, an optional extra. In a copy of Domus: uv sync --extra shots, "
           "then uv run playwright install chromium (or pip install playwright, then playwright install chromium; on "
           "Linux, playwright install --with-deps chromium also installs the libraries it needs).")
PNG = b"\x89PNG\r\n\x1a\n"
PNG_IMAGE = {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND"}      # the chunks that make up the image itself
CONTENT_TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json",
                 ".geojson": "application/geo+json", ".html": "text/html", ".css": "text/css", ".png": "image/png",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml"}

# Run once the viewer has the model: the model takes the whole window.
SETUP = """async () => {
  for (const sel of ["#panel", "footer.sheet", "#navpad", "#optionBar"]) {
    const el = document.querySelector(sel);
    if (!el) throw new Error(`This viewer has no ${sel}.`);
    el.hidden = true;
  }
  return true;
}"""
# Run for each shot, through the viewer's own controls: the option's button, the groups' switches in the Layers list
# (after the option, which shows and hides some layers of its own), then the view's button or the camera. It waits
# until the model has been drawn so (siteViewer.drawn), and gives what the viewer says it's showing.
SHOT = """async ({ option, hide, view, pos, target }) => {
  const button = [...document.querySelectorAll("#optionBar button[data-opt]")].find((b) => b.dataset.opt === option);
  if (button) button.click();
  else if (option) throw new Error(`This model has no wall option ${option}.`);
  for (const name of hide) {
    const group = [...document.querySelectorAll("#layers details.group")]
      .find((g) => g.querySelector(".group-name")?.textContent === name);
    if (!group) throw new Error(`This model's layers have no group ${name}.`);
    const all = group.querySelector("summary input[type=checkbox]");
    all.checked = false;
    all.dispatchEvent(new Event("change"));
  }
  if (view) {
    const b = [...document.querySelectorAll("#views button")].find((x) => x.textContent === view);
    if (!b) throw new Error(`This model has no view ${view}.`);
    b.click();
  } else {
    window.siteViewer.setCamera(pos, target);
  }
  await window.siteViewer.drawn();
  if (window.__domusShotsError) throw new Error(window.__domusShotsError);
  return document.getElementById("siteState")?.textContent || "";
}"""
# Before the page's own scripts: the first error they throw. Before the viewer has the model it means it couldn't open
# it, and after, that the viewer broke and its pictures can't be trusted.
CATCH = "addEventListener('error', (e) => { window.__domusShotsError ??= String(e.message || e.error); });"
OPENED = "() => (window.siteViewer?.drawn && 'opened') || window.__domusShotsError"


def read_spec(path):
    """SPEC.json, checked, with its defaults filled in and `out` a Path (from the spec's own folder)."""
    path = Path(path)
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DomusError(f"There's no {path}") from None
    except ValueError as e:
        raise DomusError(f"{path} isn't JSON: {e}") from None
    return check_spec(spec, path.resolve().parent)


def _point(shot, key):
    value = shot[key]
    if not (isinstance(value, list) and len(value) == 3 and all(_number(v) for v in value)):
        raise DomusError(f"Shot {shot['name']}: {key} is three numbers, such as [1.0, 2.0, 3.0]")
    return [float(v) for v in value]


def _number(v):
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def check_spec(spec, base):
    """A spec as read from JSON, checked and with its defaults filled in; `base` is the folder `out` is taken from."""
    if not isinstance(spec, dict):
        raise DomusError("A shots spec is a JSON object: see docs/shots.md")
    if unknown := sorted(set(spec) - SPEC_KEYS):
        raise DomusError(f"A shots spec has no {', '.join(unknown)}: it takes {', '.join(sorted(SPEC_KEYS))}")
    window = spec.get("window", WINDOW)
    if not (isinstance(window, list) and len(window) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) and 100 <= v <= MAX_SIDE for v in window)):
        raise DomusError(f"window is the window's width and height in CSS pixels, two whole numbers from 100 to {MAX_SIDE}")
    scale = spec.get("scale", 1)
    if not (_number(scale) and 0 < scale <= MAX_SCALE):
        raise DomusError(f"scale is how many device pixels make a CSS pixel: more than 0, up to {MAX_SCALE}")
    hide = spec.get("hide", [])
    if not (isinstance(hide, list) and all(isinstance(h, str) and h for h in hide)):
        raise DomusError('hide is a list of layer groups\' names, such as ["Vegetation"]')
    out = spec.get("out", ".")
    if not isinstance(out, str) or not out:
        raise DomusError("out is a folder, from the spec's own folder")
    shots = spec.get("shots")
    if not (isinstance(shots, list) and shots and all(isinstance(s, dict) for s in shots)):
        raise DomusError("shots is a list of shots, each a JSON object with a name: see docs/shots.md")
    seen = set()
    checked = []
    for shot in shots:
        name = shot.get("name")
        if not (isinstance(name, str) and NAME.fullmatch(name)):
            raise DomusError(f"A shot's name is its picture's file name without .png: letters, digits, - and _, not {name!r}")
        if name.casefold() in seen:
            raise DomusError(f"Two shots are called {name}")
        seen.add(name.casefold())
        if unknown := sorted(set(shot) - SHOT_KEYS):
            raise DomusError(f"Shot {name} has {', '.join(unknown)}, which a shot doesn't take: see docs/shots.md")
        option = shot.get("option", "")
        if not isinstance(option, str):
            raise DomusError(f"Shot {name}: option is a wall option's id, or \"\" for the site as it is")
        given = {k: CAMERA[k] for k in shot if k in CAMERA}
        out_shot = {"name": name, "option": option, "view": None, "pos": None, "target": None}
        if "view" in shot:
            if not isinstance(shot["view"], str) or not shot["view"]:
                raise DomusError(f"Shot {name}: view is a view button's name, such as \"Overview\"")
            if given:
                raise DomusError(f"Shot {name} gives a view and {', '.join(sorted(given))}: a view, or where the camera is")
            out_shot["view"] = shot["view"]
        elif not given:
            out_shot["view"] = "Overview"
        else:
            for end in ("pos", "target"):
                keys = sorted(k for k, (e, _) in given.items() if e == end)
                if len(keys) != 1:
                    raise DomusError(f"Shot {name} needs one of {end}, {end}_so and {end}_en"
                                     + (f", not {' and '.join(keys)}" if keys else ""))
                out_shot[end] = (CAMERA[keys[0]][1], _point(shot, keys[0]))
        checked.append(out_shot)
    return {"window": list(window), "scale": float(scale), "hide": list(dict.fromkeys(hide)), "out": base / out,
            "shots": checked}


def read_model(folder):
    """The built model's model.json, as a dict."""
    path = Path(folder) / "model.json"
    if not path.exists():
        raise DomusError(f"{folder} has no model.json yet: build the site first, with domus build")
    return json.loads(path.read_text(encoding="utf-8"))


def groups(model):
    """The layer groups a shot can hide: those the viewer's Layers list shows a switch for that hold a layer of the
    site's own, not a wall option's (an option's layers come and go with it)."""
    return sorted({layer.get("group") or "Other" for layer in model["layers"]
                   if layer["id"] != "ground" and not layer.get("option")})


def to_viewer(kind, point, origin, frame=None):
    """A point in viewer metres (x east, y up, z south of the model's origin `origin`, [E0, N0, H0]) from one given in
    viewer metres (kind "viewer"), in the wall options' frame as (s, o, height) ("so", with the frame, a SiteFrame) or
    on the site's grid as (E, N, height) ("en")."""
    if kind == "viewer":
        return list(point)
    if kind == "so":
        (e, n), = frame.en([point[:2]]).tolist()
        point = [e, n, point[2]]
    e, n, h = point
    return [e - origin[0], h - origin[2], -(n - origin[1])]


def plan(spec, model):
    """The spec's shots as the page takes them, checked against the built model: each with its option and either its
    view's name or its camera's `pos` and `target` in viewer metres."""
    options = [o["id"] for o in model["summary"].get("options", [])]
    views = list(dict.fromkeys([*VIEWS, *model.get("views", {})]))
    hideable = groups(model)
    if missing := [g for g in spec["hide"] if g not in hideable]:
        raise DomusError(f"This model's layers have no group {', '.join(missing)} to hide: the groups are {', '.join(hideable)}")
    frame = None
    shots = []
    for shot in spec["shots"]:
        name = shot["name"]
        if shot["option"] and shot["option"] not in options:
            raise DomusError(f"Shot {name}: this model has no wall option {shot['option']}: "
                             + (f"its options are {', '.join(options)}" if options else "it has none"))
        if shot["view"] is not None and shot["view"] not in views:
            raise DomusError(f"Shot {name}: this model has no view {shot['view']}: its views are {', '.join(views)}")
        cameras = {}
        for end in ("pos", "target"):
            if shot[end] is None:
                continue
            kind, point = shot[end]
            if kind == "so" and frame is None:
                frame = options_frame(model, name)
            cameras[end] = to_viewer(kind, point, model["origin"], frame)
        shots.append({"name": name, "option": shot["option"], "view": shot["view"], **cameras})
    return shots


def options_frame(model, name):
    """The frame the model's wall options were drawn in, as the build recorded it."""
    from .options.scheme import SiteFrame

    frame = model["summary"].get("options_site", {}).get("frame")
    if not frame:
        raise DomusError(f"Shot {name} is in the wall options' frame, and this model has no wall options")
    return SiteFrame(frame["origin"], frame["toward"], frame.get("side", "right"))


def bare_png(data):
    """A PNG's bytes with only the chunks that make up the image (IHDR, PLTE, tRNS, IDAT, IEND): no text, EXIF, time,
    colour profile or other metadata."""
    if not data.startswith(PNG):
        raise DomusError("That isn't a PNG")
    kept, at = [PNG], len(PNG)
    while at < len(data):
        if at + 12 > len(data):
            raise DomusError("That PNG ends part way through a chunk")
        length, kind = struct.unpack(">I4s", data[at:at + 8])
        end = at + 12 + length
        if end > len(data):
            raise DomusError("That PNG ends part way through a chunk")
        if kind in PNG_IMAGE:
            kept.append(data[at:end])
        at = end
        if kind == b"IEND":
            break
    return b"".join(kept)


def png_size(data):
    """A PNG's width and height, from its IHDR."""
    return struct.unpack(">II", data[16:24])


def _inside(base, rel):
    """`rel` under `base`, or a path that doesn't exist if it would leave it."""
    base = base.resolve()
    target = (base / unquote(rel)).resolve()
    return str(target) if target.is_relative_to(base) else str(base / "__missing__")


class Files(http.server.SimpleHTTPRequestHandler):
    """The viewer's page at /, its files, and the model's under /data/: nothing else, and only to read."""

    model = None        # the folder the model was built into

    def log_message(self, format, *args):
        pass

    def guess_type(self, path):
        # Named here: Python's own guess reads the Windows registry, which on some machines maps .js to text/plain,
        # and a browser won't run that as a module.
        return CONTENT_TYPES.get(Path(path).suffix.lower()) or super().guess_type(path)

    def translate_path(self, path):
        clean = urlsplit(path).path
        if clean.startswith("/data/"):
            return _inside(self.model, clean[len("/data/"):])
        return _inside(VIEWER, clean.lstrip("/"))

    def list_directory(self, path):
        self.send_error(404)

    def do_GET(self):
        if urlsplit(self.path).path not in ("/", "/index.html"):
            return super().do_GET()
        body = (HEAD + (VIEWER / "index.html").read_text(encoding="utf-8") + "\n</body></html>\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


@contextmanager
def serving(model_dir):
    """The viewer and the model built in `model_dir`, served on a free port of 127.0.0.1 while the block runs: gives the
    viewer's address, and stops the server when the block ends."""
    handler = type("ModelFiles", (Files,), {"model": Path(model_dir).resolve()})
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(handler, directory=str(VIEWER)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


class Deadline:
    """What's left of a run's time limit, in milliseconds for Playwright's waits; a DomusError once it has run out."""

    def __init__(self, seconds):
        self.seconds = seconds
        self.end = time.monotonic() + seconds

    def ms(self):
        left = self.end - time.monotonic()
        if left <= 0:
            raise DomusError(self.over())
        return max(1.0, left * 1000)

    def over(self, at=None):
        return (f"The pictures ran over their time limit, {self.seconds:g} s{f', at {at}' if at else ''}. "
                "--timeout gives them longer.")


def playwright():
    """Playwright's sync API (sync_playwright, its Error and its TimeoutError), or a DomusError saying how to install it."""
    try:
        from playwright.sync_api import Error, sync_playwright
        from playwright.sync_api import TimeoutError as Timeout
    except ImportError:
        raise DomusError(INSTALL) from None
    return sync_playwright, Error, Timeout


def first_line(error):
    """A Playwright error's first line, without the call it came from ("Page.wait_for_function: Error: ")."""
    line = (str(getattr(error, "message", error)).strip().splitlines() or [""])[0]
    return re.sub(r"^\w+\.\w+: ", "", line).removeprefix("Error: ")


def take(spec, model_dir, timeout_s=TIMEOUT_S, say=print):
    """Takes a spec's pictures (read_spec) of the model built in `model_dir`, saying each as it's written, and gives
    [(path, what the viewer showed)]."""
    shots = plan(spec, read_model(model_dir))
    sync_playwright, Error, Timeout = playwright()
    deadline = Deadline(timeout_s)
    spec["out"].mkdir(parents=True, exist_ok=True)
    taken = []
    with serving(model_dir) as url, sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(timeout=deadline.ms())
        except Timeout:
            raise DomusError(deadline.over("starting Chromium")) from None
        except Error as e:
            raise DomusError(f"Playwright's Chromium didn't start: {first_line(e)}\n{INSTALL}") from None
        with browser:
            width, height = spec["window"]
            context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=spec["scale"],
                                          color_scheme="light", reduced_motion="reduce")
            page = context.new_page()
            page.add_init_script(CATCH)
            step = "opening the viewer"
            try:
                page.goto(url, wait_until="load", timeout=deadline.ms())
                opened = page.wait_for_function(OPENED, timeout=deadline.ms()).json_value()
                if opened != "opened":
                    raise DomusError(f"The viewer couldn't open the model: {opened}")
                page.wait_for_function(SETUP, timeout=deadline.ms())
                for shot in shots:
                    step = f"shot {shot['name']}"
                    arg = {"option": shot["option"], "hide": spec["hide"], "view": shot["view"],
                           "pos": shot.get("pos"), "target": shot.get("target")}
                    showing = page.wait_for_function(SHOT, arg=arg, timeout=deadline.ms()).json_value()
                    png = bare_png(page.screenshot(type="png", timeout=deadline.ms()))
                    path = spec["out"] / f"{shot['name']}.png"
                    part = path.with_name(f"{path.name}.part")
                    part.write_bytes(png)
                    os.replace(part, path)
                    w, h = png_size(png)
                    say(f"wrote {path} ({w}x{h}, {showing})")
                    taken.append((path, showing))
            except Timeout:
                raise DomusError(deadline.over(step)) from None
            except Error as e:
                raise DomusError(f"The viewer stopped at {step}: {first_line(e)}") from None
    return taken


def main(site, argv=None):
    ap = argparse.ArgumentParser(prog="domus shots", description="Pictures of the viewer, from a spec (docs/shots.md).")
    ap.add_argument("spec", help="the spec, a JSON file")
    ap.add_argument("--timeout", type=float, default=TIMEOUT_S,
                    help=f"seconds the whole run may take, starting the browser included (default {TIMEOUT_S:g})")
    args = ap.parse_args(argv)
    if not (math.isfinite(args.timeout) and args.timeout > 0):
        raise DomusError("--timeout is a number of seconds, more than 0")
    spec = read_spec(args.spec)
    take(spec, site.output, args.timeout)
