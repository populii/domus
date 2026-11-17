"""What would make a site's model better: a checklist of further sources, and a route to film.

The build adds both to the model, and the viewer's "Improve this model" panel shows them. A source counts as found from the
site's own files, so the checklist keeps up as you add things; a person can also tick one off. `[enrich] enabled = false` in
data/site.toml leaves all this out.

The route walks the lot's edge, then round the house, then visits what the LiDAR sees worst: ground under tree crowns, and steep
banks. It's a suggestion to adapt on the ground, staying off neighbours' land.
"""

import re

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon

from . import crs, geojson, raster

SPOT_SPACING_M = 6.0

# id, title, why it helps, where to get it, what to do with it, and file names under sources/raw that count as having it.
SOURCES = [
    ("boundary", "Title and survey plan", "Replaces the public boundary, which is a guide only, with the surveyed one. Pegs on the ground settle it.",
     "Your record of title and its deposited plan (DP): from your solicitor, or LINZ's title search services.",
     "Put the corners in data/site/boundary.geojson.", r"title|survey|\bdp\b|dp\d"),
    ("lim", "LIM report", "Often the single best document: consents, drainage plans, hazards, and sometimes old survey drawings.",
     "Your council, for a fee. It may take several working days.", "Keep it in sources/raw/, and read off what the model needs.", r"\blim\b|lim[-_ ]"),
    ("consents", "Consent history and property file", "Shows what was consented, retaining walls included, and holds the plans and engineering behind it.",
     "Your council's property file request. Often free or low cost.", "Keep it in sources/raw/. Add any plans as below.", r"consent|property.?file|\bcoc\b|\bccc\b"),
    ("plans", "Building and site plans", "Gives true house dimensions, floor levels, openings, and existing levels on the site.",
     "Your property file, or your designer or architect.", "domus prep plan-georef for scans, domus prep plan-trace for vector PDFs: docs/from-documents.md.",
     r"plan|drawing|elevation|section|sketch"),
    ("drains", "Drainage and underground services", "A wall crossing a drain is expensive to find out about late. Comparing options checks the cover over each pipe.",
     "Your property file, your council's GIS map, and a free beforeUdig request for services in the street.",
     "Add the lines and depths to data/site/services.geojson.", r"drain|sewer|stormwater|as.?built|beforeudig"),
    ("measure", "Tape and level measurements", "The most accurate source you can have for walls and ground. A few measured heights tie everything else down.",
     "Measure wall heights and lengths, and level across the ground with a laser or water level.",
     "Add walls to data/site/walls-existing.geojson, and ground to data/site/ground-fixes.geojson.", r"measure|level"),
    ("video", "A video walk of the site", "Shows what LiDAR can't: under trees, wall faces, and anything changed since the survey. See the route below.",
     "Your phone: open this page on it and tap Scan, or film the recommended route and copy the video over.", "domus video: docs/video.md.", r"\.(mp4|mov|m4v|webm)$"),
    ("scan", "Phone LiDAR scan", "On an iPhone Pro or iPad Pro, measures walls and banks directly, in true metres, for a few metres around you.",
     "A scanning app such as Polycam, Scaniverse or 3d Scanner App. Export as PLY or LAS.", "domus scan: docs/phone-lidar.md.", r"\.(ply|las)$"),
    ("photos", "Photos of the house's walls", "Replaces the drawn walls with real ones, and fences and paving with real textures.",
     "Your phone: stand square-on to each wall, on an overcast day if you can.", "domus prep house-textures and feature-textures: docs/from-documents.md.", r"\.(jpe?g|heic)$"),
    ("geotech", "Geotechnical or engineering reports", "Ground conditions decide what a wall can be, and what it costs. Any earlier report on your site or next door helps.",
     "Your property file, or a geotechnical engineer.", "Keep it in sources/raw/ and note ground conditions in your options.", r"geotech|engineer|\bpsi?\d|ps1|soil"),
    ("ifc", "A designer's 3D model", "Shows a proposed or existing building in place, with its levels.",
     "Your designer: ask for an IFC export.", "domus prep ifc: docs/from-documents.md.", r"\.ifc$"),
]


# The same, said for anywhere: where New Zealand's documents and services have names of their own.
ANYWHERE = {
    "boundary": ("Title and survey plan", None, "Your title and its survey plan: from your solicitor, or the land registry.", None),
    "lim": ("Council property report",
            ("Often the single best document: consents, drainage plans, hazards, and sometimes old survey drawings."),
            "Your council or local authority, where it offers one. It may take several working days.", None),
    "drains": (None, None, ("Your property file, your council's GIS map, and the local service for locating underground "
                            "pipes and cables before digging."), None),
}


def sources(in_nz):
    """SOURCES, worded for New Zealand or for anywhere."""
    if in_nz:
        return SOURCES
    out = []
    for i, t, why, where, how, pat in SOURCES:
        title, why2, where2, how2 = ANYWHERE.get(i, (None, None, None, None))
        out.append((i, title or t, why2 or why, where2 or where, how2 or how, pat))
    return out


def has_any(pattern):
    return any(p.is_file() and re.search(pattern, p.name, re.IGNORECASE) for p in (crs.ROOT / "sources" / "raw").rglob("*")) \
        if (crs.ROOT / "sources" / "raw").is_dir() else False


def checklist(cfg):
    """The sources in order of value, each with whether the site's files already show it."""
    found = {
        "boundary": not _boundary_is_public(),
        "measure": (crs.ROOT / "data" / "site" / "ground-fixes.geojson").exists() or (crs.ROOT / "data" / "site" / "walls-existing.geojson").exists(),
        "video": any((crs.ROOT / "data" / "video").glob("*-site.npz")),
        "scan": any((crs.ROOT / "data" / "video").glob("*-scan.npz")),
        "plans": bool(cfg.get("drapes")) or (crs.ROOT / "data" / "georef").is_dir(),
        "drains": (crs.ROOT / "data" / "site" / "services.geojson").exists(),
        "photos": (crs.ROOT / "data" / "house" / "faces.json").exists() or (crs.ROOT / "data" / "features").is_dir(),
        "ifc": "ifc" in cfg,
    }
    from .crs import site_to_lonlat
    from .newsite import in_new_zealand

    e0, n0, e1, n1 = cfg["model"]["extent"]
    here = sources(in_new_zealand(*site_to_lonlat((e0 + e1) / 2, (n0 + n1) / 2)))
    return [{"id": i, "title": t, "why": why, "where": where, "how": how, "found": bool(found.get(i) or has_any(pat))}
            for i, t, why, where, how, pat in here]


def _boundary_is_public():
    feats = geojson.read(crs.ROOT / "data" / "site" / "boundary.geojson")["features"]
    src = str(feats[0]["properties"].get("source", ""))
    return "A guide, not a survey" in src or "stand-in" in src or "square" in src


def ring_points(poly, spacing):
    """Points every `spacing` metres round a polygon's outside (its largest part, if it comes apart)."""
    parts = list(getattr(poly, "geoms", [poly]))
    if not parts or poly.is_empty:
        return []
    ring = max(parts, key=lambda g: g.area).exterior
    return [ring.interpolate(d) for d in np.arange(0, ring.length, spacing)]


def weak_spots(lot, ground, avoid=None, step=1.0):
    """Places in the lot the LiDAR sees worst, away from the house: (x, y, what), the worst few, spread out."""
    x0, y0, x1, y1 = lot.bounds
    E, N = np.meshgrid(np.arange(x0, x1, step), np.arange(y0, y1, step))
    inside = shapely.contains_xy(lot, E, N)
    if avoid is not None:
        inside &= ~shapely.contains_xy(avoid, E, N)
    H = ground(E.ravel(), N.ravel()).reshape(E.shape)
    gy, gx = np.gradient(np.nan_to_num(H, nan=float(np.nanmean(H))), step)
    slope = np.hypot(gx, gy)
    canopy = np.zeros(E.shape)
    if raster.DSM.exists():
        canopy = np.nan_to_num(raster.dsm().sample(E, N) - H, nan=0.0)
    scores = []
    for i, j in zip(*np.nonzero(inside)):
        if canopy[i, j] > 2.5:
            scores.append((canopy[i, j] + 5, E[i, j], N[i, j], "the ground under the trees, which the LiDAR only guesses"))
        elif slope[i, j] > 0.5:
            scores.append((slope[i, j] * 5, E[i, j], N[i, j], "the steep bank, from the bottom looking up, then along its top"))
    out = []
    for _, x, y, what in sorted(scores, key=lambda s: -s[0]):
        if all(np.hypot(x - a, y - b) > SPOT_SPACING_M for a, b, _ in out):
            out.append((float(x), float(y), what))
        if len(out) == 4:
            break
    return out


def walk_route(lot_ring, house_ring, ground):
    """{path: [[x, y]...], stops: [{n, x, y, text}...]}: round the lot's inside edge with a short detour to each weak spot,
    then round the house. Detours leave from the edge, so no leg crosses the house."""
    lot = Polygon(lot_ring).buffer(0)
    edge = lot.buffer(-1.0)
    if edge.is_empty:
        edge = lot
    edge_pts = ring_points(edge, 1.0)
    if not edge_pts:
        return None
    house = Polygon(house_ring).buffer(0) if house_ring else None
    keep_clear = house.buffer(1.0) if house else None
    loop = LineString([*[(p.x, p.y) for p in edge_pts], (edge_pts[0].x, edge_pts[0].y)])
    # Each spot leaves the edge at its nearest point, in order along the walk.
    spurs = []
    for x, y, what in weak_spots(lot, ground, keep_clear):
        d = loop.project(Point(x, y))
        q = loop.interpolate(d)
        if keep_clear is None or not LineString([(q.x, q.y), (x, y)]).intersects(keep_clear):
            spurs.append((d, x, y, what))
    spurs.sort()
    path = [[edge_pts[0].x, edge_pts[0].y]]
    stops = [{"x": path[0][0], "y": path[0][1], "text": "Start here and walk the edge of the lot, the camera turned in towards the middle, "
              "taking in fences, walls and level changes."}]
    at = iter(spurs)
    spur = next(at, None)
    for p, dist in ((Point(c), loop.project(Point(c))) for c in list(loop.coords)[1:]):
        while spur and spur[0] <= dist and dist > 0:
            q = loop.interpolate(spur[0])
            path += [[q.x, q.y], [spur[1], spur[2]], [q.x, q.y]]
            stops.append({"x": spur[1], "y": spur[2], "text": f"Step in to {spur[3]}. Circle it slowly, then come back to the edge."})
            spur = next(at, None)
        path.append([p.x, p.y])
    if house is not None:
        pts = ring_points(house.buffer(2.5, join_style=2).intersection(lot.buffer(-0.3)), 1.0)
        if pts:
            k = int(np.argmin([Point(path[-1]).distance(q) for q in pts]))
            around = [[q.x, q.y] for q in pts[k:] + pts[:k]]
            stops.append({"x": around[0][0], "y": around[0][1], "text": "Walk round the house about 2.5 m out, the camera on the walls. Include the "
                          "corners, door sills and the foot of the walls, where the model ties to the ground."})
            path += [*around, around[0]]
    for k, st in enumerate(stops):
        st["n"] = k + 1
    return {"path": path, "stops": stops, "length_m": round(LineString(path).length)}
