"""The made-up sites CI builds with a pull request's base and with its head, so a change to the build can be proved
without anyone's real property.

    uv run python tools/made_up_sites.py write OUT     # writes OUT/demo, OUT/richer and OUT/abroad
    python tools/made_up_sites.py report DIR           # what moved between DIR/base-<site> and DIR/head-<site>

The sites, all in the sea, so none is a real place:

  demo     What `domus demo` writes: a sloping lot, a small house, a block wall, a drain, trees, a fence and two
           wall options.
  richer   The demo with more of what real sites have: house parts (a porch and its steps, a deck and its
           balustrade, a chimney and a tank), levels along the drain, a sewer at an assumed depth, and a right of
           way listed before the lot.
  abroad   The demo moved onto the British National Grid (EPSG:27700), rasters and all. Domus reads rasters on New
           Zealand's grid for now, so this one won't build until phase 3 in ARCHITECTURE.md, and the report says so
           rather than failing.

`report` reads what CI leaves in DIR for each side (base and head) and site: the copy it built (`<side>-<site>/`,
with the model in `viewer/data/`), the build's output (`<side>-<site>.log`) and whether it built
(`<side>-<site>.status`, `ok` or `failed`). It prints Markdown, and exits 1 only when the head can't build a site
the base could.
"""

import argparse
import contextlib
import io
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITES = ("demo", "richer", "abroad")
EXPECTED = {"abroad": "Expected until Domus reads rasters on their own grid (phase 3 in ARCHITECTURE.md)."}
# The abroad site's grid: the British National Grid, in the Celtic Sea.
ABROAD_EPSG, ABROAD_X0, ABROAD_Y0 = 27700, 100000.0, 100000.0
ABROAD_CRS = {"label": "OSGB36 / British National Grid (EPSG:27700)", "vertical": "ODN"}


def feat(gtype, coords, **props):
    return {"type": "Feature", "properties": props, "geometry": {"type": gtype, "coordinates": coords}}


def box(x0, y0, x1, y1):
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8", newline="\n")


def append(path, text):
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def enrich(root):
    """The demo, with house parts, drain levels, a drain at an assumed depth, and a lot that isn't listed first."""
    from domus import demo

    x0, y0 = demo.X0, demo.Y0
    site = root / "data" / "site"
    fl = tomllib.loads((root / "data" / "site.toml").read_text(encoding="utf-8"))["house"]["fl_upper"]
    # The house is 12 by 9 m, x0 + 5 to x0 + 17 east and y0 + 6 to y0 + 15 north, with its door on the north face.
    treads = [[[x0 + 9.5, y0 + 17 + 0.3 * k, round(fl - 0.05 - 0.17 * k, 2)],
               [x0 + 11.5, y0 + 17 + 0.3 * k, round(fl - 0.05 - 0.17 * k, 2)]] for k in (1, 2, 3)]
    parts = [
        feat("Polygon", box(x0 + 9, y0 + 15, x0 + 12, y0 + 17), id="porch", part="porch", floor=round(fl - 0.05, 2),
             roof_at_wall=round(fl + 2.4, 2), roof_at_edge=round(fl + 2.1, 2), walls=["west"], source="made up"),
        feat("MultiLineString", treads, id="porch-steps", part="steps", back_line=[[x0 + 9.5, y0 + 17], [x0 + 11.5, y0 + 17]],
             open_sides=["east", "west"], source="made up"),
        feat("Polygon", box(x0 + 2, y0 + 7, x0 + 5, y0 + 13), id="deck", part="block", top_below_fl_m=0.1, thickness_m=0.2,
             colour="deck", layer="deck", source="made up"),
        feat("MultiLineString", [[[x0 + 2, y0 + 7], [x0 + 2, y0 + 13], [x0 + 5, y0 + 13]]], id="deck-balustrade",
             part="balustrade", top_below_fl_m=0.1, height_m=0.9, source="made up"),
        feat("Polygon", box(x0 + 14, y0 + 8, x0 + 14.6, y0 + 8.6), id="chimney", part="chimney", stack_top=round(fl + 4.6, 2),
             source="made up"),
        feat("Point", [x0 + 18, y0 + 13.5], id="tank", part="tank", radius_m=0.5, height_m=2.0, source="made up"),
    ]
    house = read_json(site / "house.geojson")
    house["features"] += parts
    write_json(site / "house.geojson", house)

    # Levels along the stormwater drain, falling to the north, and a sewer with no levels at all.
    services = read_json(site / "services.geojson")
    for pid, xy, depth in [("drain-top", [x0 + 17, y0 + 11], 0.45), ("drain-bottom", [x0 + 21, y0 + 32], 0.9)]:
        invert = round(float(demo.ground(*xy)) - depth, 2)
        services["features"].append(feat("Point", xy, id=pid, line="drain", invert_m=invert, label=f"IL {invert:.2f}",
                                         source="made up"))
    services["features"].append(feat("LineString", [[x0 + 6, y0 + 15], [x0 + 6, y0 + 33]], id="sewer", name="Sewer",
                                     source="made up"))
    write_json(site / "services.geojson", services)

    # A right of way beside the lot, listed first, so the main lot has to be named.
    boundary = read_json(site / "boundary.geojson")
    way = feat("Polygon", box(x0 + 22, y0, x0 + 25, y0 + 30), id="right-of-way", name="Right of way (made up)",
               source="made up")
    boundary["features"].insert(0, way)
    write_json(site / "boundary.geojson", boundary)
    append(root / "data" / "site.toml", '\n[boundary]\nmain = "lot"\n\n[services]\nassumed_depth_m = { sewer = 0.7 }\n'
                                        '\n[services.notes.drain]\ntext = "Invert {lo} to {hi}, made up ({span})."\n'
                                        'confidence = "made up"\n')


def move_abroad(root):
    """The demo moved onto the British National Grid: its layers, rasters and settings."""
    import rasterio

    from domus import demo

    dx, dy = ABROAD_X0 - demo.X0, ABROAD_Y0 - demo.Y0

    def shift(c):
        if c and isinstance(c[0], (int, float)):
            return [round(c[0] + dx, 3), round(c[1] + dy, 3), *c[2:]]
        return [shift(x) for x in c]

    for path in (root / "data" / "site").glob("*.geojson"):
        fc = read_json(path)
        fc["crs"] = {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{ABROAD_EPSG}"}}
        for f in fc["features"]:
            f["geometry"]["coordinates"] = shift(f["geometry"]["coordinates"])
        write_json(path, fc)
    for path in [*(root / "data" / "lidar").glob("*.tif"), *(root / "data" / "aerial").glob("*.tif")]:
        with rasterio.open(path) as src:
            data, profile = src.read(), src.profile
        t = profile["transform"]
        profile.update(crs=f"EPSG:{ABROAD_EPSG}", transform=rasterio.Affine(t.a, t.b, t.c + dx, t.d, t.e, t.f + dy))
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data)

    def shift_settings(path, keys):
        """Moves the [E, N] arrays that `keys` set, in a settings file as Domus writes them (one array to a line)."""
        def moved(m):
            v = json.loads(m[2])
            v = [v[0] + dx, v[1] + dy, v[2] + dx, v[3] + dy] if m[1] == "extent" else [v[0] + dx, v[1] + dy, *v[2:]]
            return f"{m[1]} = {json.dumps(v)}"
        text = re.sub(rf"^({'|'.join(keys)}) = (\[[^\]]*\])", moved, path.read_text(encoding="utf-8"), flags=re.MULTILINE)
        path.write_text(text, encoding="utf-8", newline="\n")

    shift_settings(root / "data" / "site.toml", ("extent", "origin"))
    shift_settings(root / "options" / "_site.toml", ("origin", "toward"))
    settings = root / "data" / "site.toml"
    text = settings.read_text(encoding="utf-8")
    text = re.sub(r"^epsg = \d+", f"epsg = {ABROAD_EPSG}", text, flags=re.MULTILINE)
    for key, value in ABROAD_CRS.items():
        text = re.sub(rf"^{key} = .*$", f"{key} = {json.dumps(value)}", text, count=1, flags=re.MULTILINE)
    text = text.replace('"Demo site (made up)"', '"Demo site abroad (made up)"')
    settings.write_text(text, encoding="utf-8", newline="\n")


def write(out):
    from domus import demo

    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} isn't empty: give a new folder")
    for name in SITES:
        with contextlib.redirect_stdout(io.StringIO()):
            demo.main([str(out / name)])
    enrich(out / "richer")
    move_abroad(out / "abroad")
    print(f"Wrote {', '.join(SITES)} to {out}")


def status(d, side, name):
    path = d / f"{side}-{name}.status"
    return path.read_text(encoding="utf-8").strip() if path.exists() else "not built"


def tail(path, n=15):
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]) if path.exists() else "(no log)"


def fenced(text):
    return ["", "```text", text.strip(), "```", ""]


def report(d):
    rows, details, broke = [], [], []
    for name in SITES:
        base, head = status(d, "base", name), status(d, "head", name)
        if "not built" in (base, head):
            moved = "not built"
        elif base == head == "ok":
            built = [str(d / f"{side}-{name}" / "viewer" / "data") for side in ("base", "head")]
            r = subprocess.run([sys.executable, str(HERE / "compare_builds.py"), *built], check=False, capture_output=True,
                               text=True, encoding="utf-8")
            first = (r.stdout.strip().splitlines() or ["(no output)"])[0]
            moved = "nothing" if r.returncode == 0 else first
            if r.returncode:
                details += [f"### {name}: what moved", *fenced(r.stdout + r.stderr)]
        elif base == "ok":
            broke.append(name)
            moved = "**this pull request can't build it**"
            details += [f"### {name}: the build's last lines", *fenced(tail(d / f"head-{name}.log"))]
        elif head == "ok":
            moved = "builds now"
        else:
            moved = EXPECTED.get(name, "fails with both")
            details += [f"### {name}: the build's last lines", *fenced(tail(d / f"head-{name}.log"))]
        rows.append(f"| {name} | {base} | {head} | {moved} |")
    print("\n".join(["## Made-up sites: the base against this pull request", "",
                     "| Site | Base | This pull request | What moved |", "|---|---|---|---|", *rows, "", *details]))
    return 1 if broke else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("write", help="write the made-up sites").add_argument("out", type=Path)
    sub.add_parser("report", help="say what moved between the base's builds and the head's").add_argument("dir", type=Path)
    args = ap.parse_args(argv)
    if args.command == "write":
        write(args.out)
        return 0
    return report(args.dir)


if __name__ == "__main__":
    sys.exit(main())
