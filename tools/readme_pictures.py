"""The README's pictures of the made-up sites, taken again with one command.

    uv run python tools/readme_pictures.py [--only NAME ...]

docs/images/readme-pictures.json lists each picture: its name (it's docs/images/<name>.png), the made-up site it shows
(`demo` or `richer`, as tools/made_up_sites.py writes them), the window it's taken in, and the shot as a `domus shots`
spec gives one (`option`, `cutfill`, and a `view` or a camera: docs/shots.md). This writes those sites into a folder
of its own that it deletes afterwards, builds them, and takes the pictures with `domus shots`, a spec for each site and
window. Each goes into docs/images as a PNG of 256 colours (Pillow's fast octree) with only the image's own chunks
(domus.shots.bare_png), about a quarter of the size Chromium writes.

It needs Playwright and its Chromium, the shots extra: `uv sync --extra shots`, then `uv run playwright install
chromium`.
"""

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGES = HERE.parent / "docs" / "images"
LIST = IMAGES / "readme-pictures.json"

sys.path.insert(0, str(HERE))
import made_up_sites

# What tools/made_up_sites.py does to the demo to make each site.
MAKE = {"demo": lambda root: None, "richer": made_up_sites.enrich}


def pictures(path=LIST):
    """The pictures the list names, in its order: each a dict with `name`, `site`, `window` and the shot's keys."""
    pics = json.loads(Path(path).read_text(encoding="utf-8"))["pictures"]
    names = [p["name"] for p in pics]
    if len(set(names)) != len(names):
        raise SystemExit(f"{path} names a picture twice")
    for p in pics:
        if p.get("site") not in MAKE:
            raise SystemExit(f"{p['name']}: site is one of {', '.join(MAKE)}, the made-up sites this takes pictures of")
    return pics


def specs(pics):
    """A `domus shots` spec for each made-up site and window the pictures need: {(site, (width, height)): spec}."""
    out = {}
    for p in pics:
        spec = out.setdefault((p["site"], tuple(p["window"])), {"window": list(p["window"]), "shots": []})
        spec["shots"].append({k: v for k, v in p.items() if k not in ("site", "window")})
    return out


def small_png(data):
    """A PNG's picture in 256 colours, with only the chunks that make up the image."""
    from PIL import Image

    from domus.shots import bare_png

    image = Image.open(io.BytesIO(data)).convert("RGB").quantize(colors=256, method=Image.Quantize.FASTOCTREE)
    raw = io.BytesIO()
    image.save(raw, "PNG", optimize=True)
    return bare_png(raw.getvalue())


def write_site(name, root):
    """Made-up site `name` in the folder `root`, as tools/made_up_sites.py writes it."""
    from domus.project import demo

    with contextlib.redirect_stdout(io.StringIO()):
        demo.write(root)
    MAKE[name](root)


def take(pics, images=IMAGES, say=print):
    """Takes `pics` again into the folder `images`, saying each as it's written. Gives the files written."""
    from domus import api

    written = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        for (site, (width, height)), spec in specs(pics).items():
            root = tmp / site
            if not root.exists():
                write_site(site, root)
                api.open_site(root).build(say=lambda line: None)
                say(f"wrote and built the made-up {site} site")
            spec["out"] = str(tmp / f"{site}-{width}x{height}")
            spec_path = tmp / f"{site}-{width}x{height}.json"
            spec_path.write_text(json.dumps(spec), encoding="utf-8")
            for path, showing in api.open_site(root).shots(spec_path, say=lambda line: None):
                data = small_png(path.read_bytes())
                target = Path(images) / path.name
                part = target.with_name(f"{target.name}.part")
                part.write_bytes(data)
                os.replace(part, target)
                say(f"wrote {target} ({width}x{height}, {len(data) // 1024} KB, {showing})")
                written.append(target)
    return written


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="+", metavar="NAME", help="take just these pictures, by name")
    args = ap.parse_args(argv)
    pics = pictures()
    if args.only:
        unknown = sorted(set(args.only) - {p["name"] for p in pics})
        if unknown:
            raise SystemExit(f"{LIST} has no picture {', '.join(unknown)}")
        pics = [p for p in pics if p["name"] in args.only]
    from domus.errors import DomusError

    try:
        take(pics)
    except DomusError as e:
        raise SystemExit(str(e)) from None
    return 0


if __name__ == "__main__":
    sys.exit(main())
