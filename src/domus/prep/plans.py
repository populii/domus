"""Plans into site coordinates: scanned sheets pinned to the grid, vector sheets traced onto it.

    domus prep plan-pages       the embedded page scans out of a PDF, without re-encoding
    domus prep plan-georef      a scanned sheet's affine pixel -> grid fit, from the grid crosses printed on it
    domus prep plan-trace       a vector sheet's linework and text, as GeoJSON on the grid

"plans" in data/prep.json lists the work; docs/from-documents.md gives the format. A georeferenced sheet can be
laid on the ground through [[drapes]] in data/site.toml. Needs `pip install domus[plans]`; the image has it.
"""

import argparse
import json

import numpy as np

from ..errors import DomusError
from ..geo import geojson
from ..geo.georef import apply_affine, find_cross, fit_affine, fit_rigid, fit_similarity, intersect, refine_line
from . import config

MM_PER_PT = 25.4 / 72


def usage(step, what):
    """A plan step's arguments, of which it has none: what it does comes from data/prep.json."""
    argparse.ArgumentParser(prog=f"domus prep {step}", description=f"{what}, as \"plans\" in data/prep.json says "
                            "(docs/from-documents.md).").parse_args()


def pages(site):
    usage("plan-pages", "The embedded page scans out of a PDF, without re-encoding")
    import pdfplumber

    for job in config(site, "plans")["pages"]:
        out = site.path / job["out"]
        out.mkdir(parents=True, exist_ok=True)
        with pdfplumber.open(site.path / job["pdf"]) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                (image,) = page.images
                ext, data = page_image(image["stream"])
                path = out / f"{job['stem']}{i}.{ext}"
                path.write_bytes(data)
                print(f"page {i}: {image['srcsize'][0]}x{image['srcsize'][1]} -> {path}")


def page_image(stream):
    """An embedded image as a file: a JPEG or JPEG 2000 as it is stored, anything else as a PNG."""
    import io

    from PIL import Image

    filters = stream.get_filters()
    last = filters[-1][0].name if filters else None
    if last in ("DCTDecode", "JPXDecode") and len(filters) == 1:
        return ("jpeg" if last == "DCTDecode" else "jpx"), stream.get_rawdata()
    w, h = stream["Width"], stream["Height"]
    bits = stream.get("BitsPerComponent", 8)
    space = getattr(stream.get("ColorSpace"), "name", None)
    mode = {("DeviceGray", 8): "L", ("DeviceGray", 1): "1", ("DeviceRGB", 8): "RGB", ("DeviceCMYK", 8): "CMYK"}.get((space, bits))
    if mode is None:
        raise DomusError(f"Can't save an embedded image with {filters} in {space} at {bits} bits; convert the PDF's pages first.")
    buf = io.BytesIO()
    Image.frombytes(mode, (w, h), stream.get_data()).save(buf, "PNG")
    return "png", buf.getvalue()


class Rect:
    """A box in PDF points, y down from the top of the page."""

    def __init__(self, x0, y0, x1, y1):
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1


class Quad:
    """A turned rectangle's corners, upper left round to lower left."""

    def __init__(self, ul, ur, lr, ll):
        self.ul, self.ur, self.lr, self.ll = ul, ur, lr, ll


def rgb(colour):
    """A PDF colour (grey, RGB or CMYK) as RGB in 0..1."""
    if colour is None or isinstance(colour, str):
        return None
    c = tuple(colour) if isinstance(colour, (tuple, list)) else (colour,)
    if len(c) == 1:
        return (c[0],) * 3
    if len(c) == 4:
        k = c[3]
        return tuple((1 - x) * (1 - k) for x in c[:3])
    return c[:3]


def drawings(page):
    """A page's vector paths as dicts: type ("s" stroke, "f" fill, "fs" both), rect, color, fill, width and items.

    Each item is ("l", p, q), ("c", p, c1, c2, q), ("re", Rect) for an upright rectangle or ("qu", Quad) for a turned
    one. A path closed back to its start gets the closing line as an item.
    """
    out = []
    for kind in ("line", "curve", "rect"):
        for o in page.objects.get(kind, []):
            items, start, cur = [], None, None
            for op in o["path"]:
                if op[0] == "m":
                    start = cur = op[1]
                elif op[0] == "l":
                    items.append(("l", cur, op[1]))
                    cur = op[1]
                elif op[0] == "c":
                    items.append(("c", cur, *op[1:]))
                    cur = op[3]
                elif op[0] == "h" and cur != start:
                    items.append(("l", cur, start))
                    cur = start
            if kind == "rect":
                items = [("re", Rect(o["x0"], o["top"], o["x1"], o["bottom"]))]
            elif _is_quad(o["path"]):
                a, b, c, d = (op[1] for op in o["path"][:4])
                items = [("qu", Quad(a, b, c, d))]
            stroke, fill = bool(o["stroke"]), bool(o["fill"])
            out.append({"type": ("f" if fill else "") + ("s" if stroke else ""),
                        "rect": Rect(o["x0"], o["top"], o["x1"], o["bottom"]),
                        "color": rgb(o["stroking_color"]) if stroke else None,
                        "fill": rgb(o["non_stroking_color"]) if fill else None,
                        "width": o["linewidth"] if stroke else None,
                        "items": items})
    return out


def _is_quad(path):
    """A closed four-sided path whose sides meet square to within about half a degree, turned off upright."""
    ops = [op[0] for op in path]
    closed = ops == ["m", "l", "l", "l", "h"] or (ops == ["m", "l", "l", "l", "l"] and np.allclose(path[4][1], path[0][1]))
    if not closed:
        return False
    p = np.array([op[1] for op in path[:4]], float)
    sides = np.roll(p, -1, axis=0) - p
    lengths = np.hypot(*sides.T)
    if lengths.min() == 0:
        return False
    cos = [abs(np.dot(sides[i], sides[(i + 1) % 4])) / (lengths[i] * lengths[(i + 1) % 4]) for i in range(4)]
    upright = np.all(np.isclose(sides[:, 0], 0) | np.isclose(sides[:, 1], 0))
    return max(cos) < 0.01 and not upright


def words(page):
    """Words as (x0, top, x1, bottom, text): characters that follow on from each other along their own line, split
    at spaces. Text at any angle stays whole."""
    return _runs(page, split_at_space=True)


def spans(page):
    """Runs of text in one font and size along one line, spaces included, as (x0, top, x1, bottom, text)."""
    return _runs(page, split_at_space=False)


def _runs(page, split_at_space):
    height = float(page.height)
    runs, run = [], []
    nxt = last_step = None         # where the previous character's advance ends, and that advance

    def close():
        if run:
            runs.append((min(c["x0"] for c in run), min(c["top"] for c in run), max(c["x1"] for c in run),
                         max(c["bottom"] for c in run), "".join(c["text"] for c in run)))
            run.clear()

    for c in page.chars:
        a, b, _, _, e, f = c["matrix"]
        origin = np.array([e, height - f])
        step = c["adv"] * np.array([a, -b])        # the advance along the baseline, y down
        if run:
            prev = run[-1]
            same_font = split_at_space or (prev["fontname"], round(prev["size"], 1)) == (c["fontname"], round(c["size"], 1))
            off = np.hypot(*(origin - nxt))
            if not same_font or off > 0.5 * max(np.hypot(*step), np.hypot(*last_step)):
                close()
        if split_at_space and c["text"].isspace():
            close()
        else:
            run.append(c)
        nxt, last_step = origin + step, step
    close()
    return [r for r in runs if r[4].strip()]


def georef(site):
    usage("plan-georef", "A scanned sheet's affine fit from pixels to the grid, from the grid crosses printed on it")
    from PIL import Image

    out_dir = site.path / "data" / "georef"
    out_dir.mkdir(parents=True, exist_ok=True)
    for sheet in config(site, "plans")["georef"]:
        r = sheet["rough"]
        gray = np.asarray(Image.open(site.path / sheet["image"]).convert("L"))
        ties = []
        for n in sheet["grid_n"]:
            for e in sheet["grid_e"]:
                # Where a grid intersection roughly falls in the scan, for the search to start from.
                near = (r["col0"] + (n - r["n0"]) * r["col_per_m"], r["row0"] + (e - r["e0"]) * r["row_per_m"])
                col, row, score = find_cross(gray, near)
                ties.append({"mE": e, "mN": n, "col": round(col, 2), "row": round(row, 2),
                             "score": round(score, 3), "used": score >= sheet.get("min_score", 0.85)})
        used = [t for t in ties if t["used"]]
        a, resid = fit_affine([(t["col"], t["row"]) for t in used], [(t["mE"], t["mN"]) for t in used])
        for t, res in zip(used, resid):
            t["resid_m"] = [round(float(res[0]), 4), round(float(res[1]), 4)]
        rms = float(np.sqrt((resid ** 2).sum(1).mean()))
        record = {"sheet": sheet["name"], "source": sheet["source"], "page": sheet["page"], "image": sheet["image"],
                  "dpi": sheet["dpi"], "crs": sheet["crs"], "transform": "mE, mN = A @ [col, row, 1]",
                  "A": [[float(v) for v in row] for row in a], "rms_m": round(rms, 4), "ties": ties}
        path = out_dir / f"{sheet['out']}.json"
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"{sheet['name']}: {len(used)} crosses, rms {rms * 1000:.0f} mm -> {path}")


def scan_corners(site, spec):
    """Corners read off a georeferenced scan: lines refined between rough points, intersected, put on the grid."""
    from PIL import Image

    rec = json.loads((site.path / spec["sheet"]).read_text(encoding="utf-8"))
    a = np.array(rec["A"])
    gray = np.asarray(Image.open(site.path / rec["image"]).convert("L"))
    approx = {k: np.array(v) * spec.get("scale", 1.0) for k, v in spec["approx"].items()}
    lines = {n: refine_line(gray, approx[n[0]], approx[n[1]], band=12, trim=0.15) for n in spec["lines"]}
    return [apply_affine(a, [intersect(lines[p], lines[q])])[0] for _, p, q in spec["corners"]]


def corner_marks(drawings, spec):
    """A boundary's corner marks: the small circles in a given pen, in order from the bottom left of the sheet."""
    marks = []
    for d in drawings:
        if round(d.get("width") or 0, 2) == spec["pen_width"] and all(it[0] == "c" for it in d["items"]):
            r = d["rect"]
            c = ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
            if all(np.hypot(c[0] - m[0], c[1] - m[1]) > 1 for m in marks):
                marks.append(c)
    cx, cy = spec["centre"]
    marks = sorted(marks, key=lambda m: np.arctan2(m[1] - cy, m[0] - cx))
    i = marks.index(min(marks, key=lambda m: m[0] - m[1]))
    return marks[i:] + marks[:i]


def trace_one(site, job):
    import pdfplumber

    with pdfplumber.open(site.path / job["pdf"]) as pdf:
        _trace_page(site, job, pdf.pages[job.get("page", 0)])


def _trace_page(site, job, page):
    paths = drawings(page)
    fit = job["fit"]
    features = []
    fields = {}
    marks = corner_marks(paths, job["marks"]) if "marks" in job else None
    if fit["kind"] == "rigid":
        # Rotation and shift only: a sheet at its printed scale keeps its true size.
        m_per_pt = MM_PER_PT / 1000 * job["paper_scale"]
        paper = lambda pt: np.array([pt[0] * m_per_pt, -pt[1] * m_per_pt])
        src = np.array([paper(m) for m in marks])
        rot, tx, ty, resid = fit_rigid(src[:fit["use_marks"]], scan_corners(site, fit["scan_corners"]))
        k = np.exp(1j * rot)
        fields = {"paper": job["paper_scale"], "scale": 1.0}
    else:
        ring = geojson.read(site.path / "data" / "site" / "boundary.geojson")["features"][0]["geometry"]["coordinates"][0]
        title = dict(zip(fit["boundary_corners"], ring))
        paper = lambda pt: np.array([pt[0], -pt[1]])
        src = np.array([paper(p) for p in fit["paper"].values()])
        scale, rot, tx, ty, resid = fit_similarity(src, np.array([title[n][:2] for n in fit["paper"]]))
        k = scale * np.exp(1j * rot)
        fields = {"mm_per_pt": scale * 1000, "paper": scale / (MM_PER_PT / 1000)}
    nd = job.get("round")

    def to_grid(pt):
        p = paper(pt)
        z = k * (p[0] + 1j * p[1]) + (tx + 1j * ty)
        return [round(z.real, nd), round(z.imag, nd)] if nd else [z.real, z.imag]

    if marks:
        x, y = src.T
        area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
        corners = [to_grid(m) for m in marks]
        features.append(geojson.feature("Polygon", [[*corners, corners[0]]], layer="boundary", name=job["marks"]["name"],
                                        corners=job["marks"]["names"], area_m2=round(float(area), 2), note=job["marks"]["note"]))
    x0, y0, x1, y1 = job["window"]
    colours, styles = job.get("colours", {}), job.get("styles", {})
    for d in paths:
        r = d["rect"]
        if not (x0 < r.x0 and r.x1 < x1 and y0 < r.y0 and r.y1 < y1):
            continue
        extra = {}
        width = round(d.get("width") or 0, 2)
        if job.get("fill_colour"):
            colour = tuple(round(c, 2) for c in (d.get("color") or d.get("fill") or (0, 0, 0)))
            layer = colours.get(",".join(str(float(c)) for c in colour), f"other-{colour}")
            extra["width"] = width
        else:
            color = d.get("color")
            if d["type"] != "s" or not color:
                continue
            grey = round(color[0], 2) if color[0] == color[1] == color[2] else None
            key = ",".join(str(float(round(c, 2))) for c in color)
            layer = colours.get(key) or styles.get(f"{float(grey) if grey is not None else None},{width}", f"other-{grey}-{width}")
        for it in d["items"]:
            if it[0] == "l":
                coords = [to_grid(it[1]), to_grid(it[2])]
            elif it[0] == "c":
                coords = [to_grid(it[1]), to_grid(it[2]), to_grid(it[3]), to_grid(it[4])] if job.get("curve_points", 4) == 4 \
                    else [to_grid(it[1]), to_grid(it[4])]
            elif it[0] == "re":
                q = it[1]
                coords = [to_grid((q.x0, q.y0)), to_grid((q.x1, q.y0)), to_grid((q.x1, q.y1)),
                          to_grid((q.x0, q.y1)), to_grid((q.x0, q.y0))]
            elif it[0] == "qu" and job.get("fill_colour"):
                q = it[1]
                coords = [to_grid(q.ul), to_grid(q.ur), to_grid(q.lr), to_grid(q.ll), to_grid(q.ul)]
            else:
                continue
            props = {"layer": layer, "kind": it[0]} if not extra else {"layer": layer, **extra}
            features.append(geojson.feature("LineString", coords, **props))
    if job.get("text") == "words":
        for wx0, wy0, wx1, wy1, word in words(page):
            if x0 < wx0 < x1 and y0 < wy0 < y1:
                features.append(geojson.feature("Point", to_grid(((wx0 + wx1) / 2, (wy0 + wy1) / 2)), layer="text", text=word))
    elif job.get("text") == "spans":
        tx0, ty0, tx1, ty1 = job.get("text_window", job["window"])
        for sx0, sy0, sx1, sy1, text in spans(page):
            if tx0 < sx0 < tx1 and ty0 < sy0 < ty1:
                features.append(geojson.feature("Point", to_grid(((sx0 + sx1) / 2, (sy0 + sy1) / 2)), layer="text",
                                                text=text.strip()))
    desc = job.get("description", "{name}: rotation {rot:.3f} deg, residuals (m) {resid}.").format(
        name=job["name"], rot=np.degrees(rot), resid=np.round(resid, 3).tolist(), **fields)
    out = site.path / job["out"]
    geojson.write(out, geojson.collection(features, job["name"], desc, epsg=site.grid.epsg))
    print(desc)
    print(f"{len(features)} features -> {out}")


def trace(site):
    usage("plan-trace", "A vector sheet's linework and text, as GeoJSON on the grid")
    for job in config(site, "plans")["trace"]:
        trace_one(site, job)
