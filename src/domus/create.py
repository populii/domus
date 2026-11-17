"""From an address to a 3D model, using public data alone.

    domus create "12 Example Street, Christchurch" [path]

Looks up the address, parcel and buildings (domus.publicdata), starts a site project, fetches the LiDAR and aerial photo
(domus.linz), then drafts what it can from them: the house on the lot, as an outline from the public layer with its height
and roof fitted to the LiDAR; neighbouring buildings as plain blocks; trees from the LiDAR canopy. Everything drafted says
so in its layer, and the files can be edited like any others.

It's a first model, not a survey. Roofs are fitted as a single pitch and walls are plain.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon

from . import newsite, publicdata

STAND_IN_M = 24.0        # side of the stand-in lot when the source has no parcels
OVERHANG_M = 0.4
PLANE_MIN_POINTS = 12
SHAPE_MAX_RMS_M = 0.35           # a skillion or gable has to fit this well, against a 1 m surface model
SHAPE_BETTER_BY = 0.7            # and this much better than a hip
SHAPE_MIN_SHARE = 0.75           # over this much of the roof
SKILLION_OVER_GABLE = 1.1        # one plane wins if two aren't clearly better
SKILLION_MAX_PITCH = 25.0
GABLE_MIN_PITCH = 5.0
GABLE_MIN_SIDE = 0.1             # each slope's share of the roof
GABLE_MAX_PITCH = 50.0
MIN_HOUSE_M2 = 35.0      # smaller than this on the lot is a shed or garage, not the house


def slug(address):
    return re.sub(r"[^a-z0-9]+", "-", address.split(",")[0].lower()).strip("-") or "site"


def centroid(ring):
    p = np.asarray(ring[:-1], float)
    return p.mean(axis=0)


def pick_house(found, lot):
    """The largest building whose middle is on the lot, or the one the address falls in, and the rest."""
    area = lambda r: publicdata._area(r)
    x, y = found["point"]
    on_lot = [b for b in found["buildings"] if publicdata._contains(lot, *centroid(b))]
    big = [b for b in on_lot if area(b) >= MIN_HOUSE_M2]
    at_point = [b for b in found["buildings"] if publicdata._contains(b, x, y)]
    house = max(big, key=area) if big else (at_point[0] if at_point else None)
    return house, [b for b in found["buildings"] if b is not house]


def scaffold(root, found):
    """The site project: settings and boundary, with a stand-in square when there is no parcel."""
    x, y = found["point"]
    if found["parcel"]:
        lot, note = [[round(a, 3), round(b, 3)] for a, b in found["parcel"]], f"{found['source']}. A guide, not a survey."
    else:
        h = STAND_IN_M / 2
        lot = [[x - h, y + h], [x + h, y + h], [x + h, y - h], [x - h, y - h], [x - h, y + h]]
        note = f"{found['source']}. A square round the address: replace it with the real boundary."
    about = ("Built automatically from public data: " + found["source"] + ". LiDAR and aerial photo from LINZ. "
             "A first model, not a survey: the house's walls and roof are estimates.")
    root = newsite.write_project(root, found["address"].split(",")[0], lot, note, about)
    (root / "data" / "public-lookup.json").write_text(json.dumps(found, indent=1) + "\n", encoding="utf-8", newline="\n")
    return lot


def fc(name, features):
    return {"type": "FeatureCollection", "name": name, "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2193"}},
            "features": features}


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8", newline="\n")


def fit_plane(e, n, z):
    """(a, b, c, rms, cells used) of z = a e + b n + c through the points, leaving out what stands off it such as trees and aerials, or None if too few."""
    e0, n0 = float(np.mean(e)), float(np.mean(n))
    keep = np.isfinite(z)
    for _ in range(3):
        if keep.sum() < PLANE_MIN_POINTS:
            return None
        A = np.column_stack([e[keep] - e0, n[keep] - n0, np.ones(keep.sum())])
        a, b, c = np.linalg.lstsq(A, z[keep], rcond=None)[0]
        res = z - (a * (e - e0) + b * (n - n0) + c)
        keep = np.isfinite(res) & (np.abs(res) <= max(0.25, 2.5 * np.nanmedian(np.abs(res))))
    return a, b, c - a * e0 - b * n0, float(np.sqrt(np.mean(res[keep] ** 2))), keep


def fit_gable(ring, fr, e, n, z):
    """(bearing, ridge offset, pitch, low height over the outline, rms, cells used) of the best two-sided roof with its ridge along a wall direction, or None."""
    from shapely.geometry import Polygon

    centre = np.asarray(Polygon(ring[:-1]).centroid.coords[0])
    pts = np.asarray(ring[:-1], float)
    best = None
    for bearing in (np.degrees(np.arctan2(fr.c, fr.s)) % 180, (np.degrees(np.arctan2(fr.c, fr.s)) + 90) % 180):
        a = np.radians(bearing)
        normal = np.array([np.cos(a), -np.sin(a)])
        d = (np.column_stack([e, n]) - centre) @ normal
        wall = (pts - centre) @ normal
        for off in np.arange(-0.3, 0.301, 0.05) * np.ptp(wall):
            keep = np.isfinite(z)
            for _ in range(3):
                if keep.sum() < PLANE_MIN_POINTS:
                    break
                A = np.column_stack([np.abs(d[keep] - off), np.ones(keep.sum())])
                slope, top = np.linalg.lstsq(A, z[keep], rcond=None)[0]
                res = z - (slope * np.abs(d - off) + top)
                keep = np.isfinite(res) & (np.abs(res) <= max(0.25, 2.5 * np.nanmedian(np.abs(res))))
            if keep.sum() < PLANE_MIN_POINTS or slope >= 0:
                continue
            rms = float(np.sqrt(np.mean(res[keep] ** 2)))
            if best is None or rms < best[4]:
                if min((d[keep] > off).sum(), (d[keep] < off).sum()) < GABLE_MIN_SIDE * keep.sum():
                    continue                # one slope and some leftovers: not two sides
                best = (float(bearing), float(off), float(np.degrees(np.arctan(-slope))), float(top + slope * np.abs(wall - off).max()), rms, keep)
    return best


def fit_hip_rms(ring, fr, e, n, z, keep):
    """How well the best hip of any pitch fits the same cells as another shape, for comparing them (m)."""
    from . import house

    local = fr.local(np.column_stack([e, n]))
    inset = house.roof_surface(fr, fr.local(np.asarray(ring[:-1], float)), {"roof_pitch_deg": 45.0, "eaves_overhang_m": OVERHANG_M, "roof_plane_at_wall": 0.0})
    depth = inset(local[:, 0], local[:, 1])
    keep = keep & np.isfinite(depth)
    if keep.sum() < PLANE_MIN_POINTS:
        return np.inf
    slope, top = np.linalg.lstsq(np.column_stack([depth[keep], np.ones(keep.sum())]), z[keep], rcond=None)[0]
    return float(np.sqrt(np.mean((z[keep] - (slope * depth[keep] + top)) ** 2)))


def fit_shape(ring, e, n, s, g):
    """Settings for a skillion or gable, if one fits the surface model clearly better than a hip of any pitch. Otherwise None."""
    from . import house

    fr = house.HouseFrame(ring[:-1])

    def good(rms, pitch, low, most, keep):
        # Most of the roof has to lie on the shape: leaving half of it out would make anything fit.
        return (keep.sum() >= SHAPE_MIN_SHARE * np.isfinite(s).sum() and rms <= SHAPE_MAX_RMS_M and pitch <= most and low - g >= 2.2
                and rms <= SHAPE_BETTER_BY * fit_hip_rms(ring, fr, e, n, s, keep))

    plane, gable = fit_plane(e, n, s), fit_gable(ring, fr, e, n, s)
    settings = {"fl_upper": round(float(g) + 0.3, 2), "fitted": True}
    if plane:
        a, b, c, rms, keep = plane
        pitch = float(np.degrees(np.arctan(np.hypot(a, b))))
        low = min(a * p[0] + b * p[1] + c for p in ring[:-1])
        if good(rms, pitch, low, SKILLION_MAX_PITCH, keep) and (gable is None or rms <= SKILLION_OVER_GABLE * gable[4]):
            return {**settings, "soffit": round(low - 0.2, 2), "roof_plane_at_wall": round(low, 2), "roof_pitch_deg": round(pitch, 1),
                    "roof_form": "skillion", "roof_azimuth_deg": round(float(np.degrees(np.arctan2(a, b))) % 360, 1)}
    if gable:
        bearing, off, pitch, low, rms, keep = gable
        if good(rms, pitch, low, GABLE_MAX_PITCH, keep) and pitch >= GABLE_MIN_PITCH:
            return {**settings, "soffit": round(low - 0.2, 2), "roof_plane_at_wall": round(low, 2), "roof_pitch_deg": round(pitch, 1),
                    "roof_form": "gable", "roof_azimuth_deg": round(bearing, 1), "roof_ridge_offset_m": round(off, 2)}
    return None


def fit_house(ring, ground, surface, roof_points=None):
    """Floor, eaves and roof for an outline, from the ground and surface models: settings for [house]. With `roof_points`
    (site E, N, height of the point cloud's building points) and enough of them over the outline, the roof is fitted to
    those instead of the 1 m surface model."""
    pts = np.asarray(ring[:-1], float)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    E, N = np.meshgrid(np.arange(lo[0], hi[0] + 0.5, 0.5), np.arange(lo[1], hi[1] + 0.5, 0.5))
    inside = np.array([[publicdata._contains(ring, e, n) for e, n in zip(re_, rn)] for re_, rn in zip(E, N)])
    g = np.nanmedian(ground(E[inside], N[inside]))
    E, N, cells = E[inside], N[inside], surface(E[inside], N[inside])
    roof_from = "surface model"
    if roof_points is not None:
        pe, pn, pz = (np.asarray(v, float) for v in roof_points)
        on = shapely.contains_xy(Polygon(ring), pe, pn)
        if on.sum() >= max(4 * PLANE_MIN_POINTS, len(E)):     # at least one a cell of the grid it replaces
            E, N, cells, roof_from = pe[on], pn[on], pz[on], "building points"
    s = cells[np.isfinite(cells)]
    if not len(s) or np.nanpercentile(s, 90) - g < 2.0:     # no surface model, or it doesn't see a roof here
        return {"fl_upper": round(float(g) + 0.3, 2), "soffit": round(float(g) + 2.7, 2), "roof_plane_at_wall": round(float(g) + 2.9, 2),
                "roof_pitch_deg": 25.0, "fitted": False}
    ridge, eave = float(np.percentile(s, 97)), float(np.percentile(s, 15))
    eave = max(eave, g + 2.2)
    half = min(*(hi - lo)) / 2
    pitch = float(np.clip(np.degrees(np.arctan2(max(ridge - eave, 0.3), max(half, 1.0))), 5.0, 45.0))
    plane = eave + 0.1
    shape = fit_shape(ring, E, N, cells, g)
    if shape:
        return {**shape, "roof_from": roof_from}
    edge = plane - np.tan(np.radians(pitch)) * OVERHANG_M     # the roof's edge at the eaves: the soffit sits just under it
    return {"fl_upper": round(float(g) + 0.3, 2), "soffit": round(edge - 0.05, 2), "roof_plane_at_wall": round(plane, 2),
            "roof_pitch_deg": round(pitch, 1), "fitted": True, "roof_from": roof_from}


def walls_from_roof(ring, overhang=OVERHANG_M):
    """The wall line under a roof outline. LINZ's building outlines trace the roof's edge, eaves and all, so the walls stand
    `overhang` inside it. A shape too small or thin to inset keeps its outline."""
    roof = Polygon(ring)
    walls = roof.buffer(-overhang, join_style="mitre", mitre_limit=4.0).simplify(0.01)
    if walls.is_empty or walls.geom_type != "Polygon" or walls.area < 0.5 * roof.area:
        return ring
    return [[round(a, 3), round(b, 3)] for a, b in walls.exterior.coords]


def clockwise(ring):
    p = ring[:-1]
    area2 = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(p, [*p[1:], p[0]]))
    return ring if area2 < 0 else ring[::-1]


def draft_layers(root, found, lot, source):
    """Run in a process with DOMUS_SITE set to the project, once its rasters are there."""
    from . import points, raster, terrain, vegetation
    from .crs import ROOT

    site = ROOT / "data" / "site"
    dem = terrain.ground()
    ground = lambda e, n: dem.sample(np.atleast_1d(e), np.atleast_1d(n))
    surface = (lambda e, n: raster.dsm().sample(np.atleast_1d(e), np.atleast_1d(n))) if raster.DSM.exists() else (
        lambda e, n: np.full(np.shape(e), np.nan))
    house_ring, others = pick_house(found, lot)
    settings = []
    cloud = points.read()
    roof_points = points.on_site(cloud, cloud["cls"] == points.BUILDING) if cloud is not None else None
    if house_ring:
        ring = clockwise(walls_from_roof([[round(a, 3), round(b, 3)] for a, b in house_ring]))
        h = fit_house(ring, ground, surface, roof_points)
        faces = [f"wall-{i}" for i in range(len(ring) - 1)]
        write_json(site / "house.geojson", fc("house", [{
            "type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"id": "house-walls", "name": "House (outline from public data)", "source": source, "soffit": h["soffit"], "faces": faces,
                           "basis": f"the public outline is the roof's edge: walls set {OVERHANG_M} m inside it for the eaves",
                           "confidence": "estimated from LiDAR" if h["fitted"] else "assumed heights"}}]))
        settings.append(f"\n[house]\nfl_upper = {h['fl_upper']}\nroof_pitch_deg = {h['roof_pitch_deg']}\n"
                        f"roof_plane_at_wall = {h['roof_plane_at_wall']}\neaves_overhang_m = {OVERHANG_M}\n"
                        + (f'roof_form = "{h["roof_form"]}"\nroof_azimuth_deg = {h["roof_azimuth_deg"]}\n' if h.get("roof_form") else "")
                        + (f'roof_ridge_offset_m = {h["roof_ridge_offset_m"]}\n' if h.get("roof_ridge_offset_m") else "") +
                        f'roof_source = "LiDAR {h.get("roof_from", "surface model")} over the outline, an estimate"\n')
    feats = []
    extent = _extent()
    for k, ring in enumerate(others):
        c = centroid(ring)
        if not (extent[0] < c[0] < extent[2] and extent[1] < c[1] < extent[3]):
            continue
        inner = [p for p in ring if extent[0] < p[0] < extent[2] and extent[1] < p[1] < extent[3]]
        if len(inner) != len(ring):
            continue
        s = surface(np.array([c[0]]), np.array([c[1]]))[0]
        g = ground(np.array([c[0]]), np.array([c[1]]))[0]
        height = float(np.clip(s - g, 2.4, 12.0)) if np.isfinite(s) and np.isfinite(g) else 3.0
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[round(a, 3), round(b, 3)] for a, b in ring]]},
                      "properties": {"id": f"building-{k + 1}", "kind": "building", "name": f"Building {k + 1}", "material": "grey",
                                     "height_m": round(height, 1), "source": source, "confidence": "height from LiDAR"}})
    if feats:
        write_json(site / "features-buildings.geojson", fc("features-buildings", feats))
    if settings and "[house]" not in (ROOT / "data" / "site.toml").read_text(encoding="utf-8"):
        with open(ROOT / "data" / "site.toml", "a", encoding="utf-8", newline="\n") as fh:
            fh.write("".join(settings))
    if raster.DSM.exists() and raster.AERIAL.exists():
        draft_trees(site, vegetation)


def _extent():
    from . import crs

    return crs.settings()["model"]["extent"]


def draft_trees(site, vegetation):
    """Crowns the LiDAR and aerial agree are plants, drawn as trees and shrubs."""
    cands, _ = vegetation.map_canopy(_extent())
    feats = []
    for k, c in enumerate(cands):
        kind = vegetation.guess_kind(c)
        ring = [[round(x, 3), round(y, 3)] for x, y in c["outline"].exterior.coords]
        props = {"id": f"plant-{k + 1}", "kind": kind, "name": f"{kind.capitalize()} {k + 1}", "height_m": round(float(c["height_max"]), 2),
                 "colour": c["colour"], "source": "LiDAR canopy and aerial photo", "confidence": "drafted from LiDAR"}
        if kind == "tree":
            props.update(top="lidar", crown_base_m=round(max(0.8, 0.3 * float(c["height_max"])), 1), trunk_diameter_m=0.3, look="broadleaf")
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": props})
    if feats:
        write_json(site / "trees.geojson", fc("trees", feats))


def run_step(root, *args):
    """A step in its own process: modules read the site's path when imported."""
    env = {**os.environ, "DOMUS_SITE": str(root)}
    try:
        subprocess.run([sys.executable, "-m", "domus.create", *args], check=True, env=env)
    except subprocess.CalledProcessError as e:
        raise SystemExit("The build stopped: the lines above say why. Run the same address again to carry on.") from e


def main(argv=None):
    ap = argparse.ArgumentParser(prog="domus create", description="Build a 3D model from an address and public data.")
    ap.add_argument("address")
    ap.add_argument("path", nargs="?", help="folder for the site project (default: from the address)")
    args = ap.parse_args(argv)
    root = Path(args.path or os.environ.get("DOMUS_SITE") or slug(args.address))
    earlier = root / "data" / "public-lookup.json"
    if earlier.exists():
        if json.loads(earlier.read_text(encoding="utf-8")).get("query") != args.address:
            raise SystemExit(f"{root} already holds another address. Use an empty folder for a new one.")
        print("Carrying on with the build of this address")
    else:
        try:
            found = publicdata.lookup(args.address)
        except publicdata.NotFound as e:
            raise SystemExit(str(e)) from e
        except publicdata.Ambiguous as e:
            raise SystemExit("WHICH: " + json.dumps({"address": e.address, "places": e.places})) from e
        except OSError as e:
            raise SystemExit(f"The address lookup didn't get an answer ({e}). Try again shortly.") from e
        print(f"Found {found['address']}: {found['source']}; {len(found['buildings'])} buildings nearby")
        scaffold(root, {**found, "query": args.address})
    run_step(root, "--step", "rest")
    print(f"Done. See it with: domus --site {root} serve")
    return 0


def rest():
    """Runs with DOMUS_SITE set: fetch, draft, build."""
    from . import build, crs, linz
    from .crs import ROOT

    found = json.loads((ROOT / "data" / "public-lookup.json").read_text(encoding="utf-8"))
    lot = json.loads((ROOT / "data" / "site" / "boundary.geojson").read_text(encoding="utf-8"))["features"][0]["geometry"]["coordinates"][0]
    linz.main()
    if publicdata.linz_key():
        from . import title

        try:
            title.main([])
        except OSError as e:
            print(f"LINZ's title and easements didn't load ({e}); carrying on without them")
    from . import points

    try:
        points.main([])
    except (OSError, SystemExit) as e:
        print(f"No point cloud ({e}); the roof is fitted to the 1 m surface model")
    draft_layers(ROOT, found, lot, found["source"])
    crs.settings.cache_clear()    # the draft added [house]
    build.main()


if __name__ == "__main__":
    if sys.argv[1:] == ["--step", "rest"]:
        rest()
    else:
        sys.exit(main())
