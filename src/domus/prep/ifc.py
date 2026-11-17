"""A designer's IFC model of the house on the site grid: meshes for the viewer, and its drains and retaining walls
as lines.

    domus prep ifc

An IFC usually carries no usable georeferencing, so it is fitted in plan to the house's outside wall line (the
house-walls feature in data/site/house.geojson): the union of the IFC's walls, its outer ring, matched to the outline
by a 2D rigid fit (ICP from the best of four quarter turns). Heights are the model's plus `datum_dz`.

"ifc" in data/prep.json says what to read: `source`, `out` (written as <out>.npz, one mesh per viewer layer, and
<out>.geojson, the lines and the fit), `datum_dz`, `walls_layer`, `drain_layer`, `drain_part`, `retaining_layer`, and
`groups`, the viewer layers: key = {name, colour, layers, and optionally status, type, not_type}.

Point the site's [ifc] settings at the output to show it. Needs `pip install domus[ifc]`; the image has it.
"""

import hashlib

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as ue
import ifcopenshell.util.placement as up
import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import unary_union

from .. import geojson
from ..crs import ROOT
from . import config


def layer_of(f, p):
    ls = ue.get_layers(f, p)
    return ls[0].Name if ls else ""


def products(f):
    s = ifcopenshell.geom.settings()
    s.set("use-world-coords", True)
    it = ifcopenshell.geom.iterator(s, f, 4)
    out = []
    if it.initialize():
        while True:
            sh = it.get()
            p = f.by_id(sh.id)
            st = ue.get_psets(p).get("AC_Pset_RenovationAndPhasing", {}).get("Renovation Status", "")
            out.append({"id": sh.id, "type": p.is_a(), "name": p.Name or "", "layer": layer_of(f, p), "status": st,
                        "lib": ue.get_psets(p).get("ArchiCADProperties", {}).get("Library Part Name", ""),
                        "verts": np.array(sh.geometry.verts, float).reshape(-1, 3),
                        "faces": np.array(sh.geometry.faces, int).reshape(-1, 3)})
            if not it.next():
                break
    # The geometry iterator's threads finish in any order.
    return sorted(out, key=lambda p: p["id"])


def fit_to_outline(prods, ring, walls_layer):
    """2D rigid transform (R, t) taking the IFC's outer wall line onto the house outline, and its misfit."""
    polys = []
    for p in prods:
        if p["layer"] == walls_layer and p["type"] in ("IfcWall", "IfcWallStandardCase"):
            for tri in p["verts"][p["faces"]][:, :, :2]:
                g = Polygon(tri)
                if g.area > 1e-8:
                    polys.append(g)
    u = unary_union(polys).buffer(0.02).buffer(-0.02)
    parts = list(u.geoms) if isinstance(u, MultiPolygon) else [u]
    outer = Polygon(max(parts, key=lambda g: Polygon(g.exterior).area).exterior)

    def dense(line, step=0.05):
        return np.array([line.interpolate(d).coords[0] for d in np.arange(0, line.length, step)])

    src = dense(LineString(outer.exterior.coords))
    tgt = dense(LineString(np.vstack([ring, ring[:1]])))
    tree = cKDTree(tgt)
    best = None
    for k in range(4):
        th = np.radians(90 * k)
        R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
        t = np.asarray(Polygon(ring).centroid.coords[0]) - R @ np.asarray(outer.centroid.coords[0])
        for _ in range(80):
            d, i = tree.query(src @ R.T + t)
            keep = d < max(0.5, np.percentile(d, 80))
            a, b = src[keep], tgt[i[keep]]
            ca, cb = a.mean(0), b.mean(0)
            U, _, Vt = np.linalg.svd((a - ca).T @ (b - cb))
            D = np.diag([1.0, np.sign(np.linalg.det(Vt.T @ U.T))])
            R = Vt.T @ D @ U.T
            t = cb - R @ ca
        d1, _ = tree.query(src @ R.T + t)
        d2, _ = cKDTree(src @ R.T + t).query(tgt)
        score = np.median(d1) + np.median(d2)
        if best is None or score < best[0]:
            best = (score, R, t, float(np.median(d2)), float(np.percentile(d2, 90)))
    _, R, t, med, p90 = best
    return R, t, med, p90


def to_site(v, R, t, dz):
    out = v.copy()
    out[:, :2] = v[:, :2] @ R.T + t
    out[:, 2] = v[:, 2] + dz
    return out


def pipe_axis(v):
    """Centreline end points and radius of a cylindrical pipe mesh (site coordinates)."""
    c = v.mean(0)
    _, _, vt = np.linalg.svd(v - c, full_matrices=False)
    a = vt[0]
    s = (v - c) @ a
    r = float(np.median(np.linalg.norm((v - c) - np.outer(s, a), axis=1)))
    return c + s.min() * a, c + s.max() * a, r


def selected(g, p):
    return (p["layer"] in g["layers"] and ("status" not in g or p["status"] == g["status"])
            and ("type" not in g or p["type"] == g["type"]) and p["type"] != g.get("not_type"))


def main():
    cfg = config("ifc")
    src, dz = ROOT / cfg["source"], cfg.get("datum_dz", 0.0)
    out = ROOT / cfg["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    f = ifcopenshell.open(str(src))
    prods = products(f)
    house = {g["properties"]["id"]: g for g in geojson.read(ROOT / "data" / "site" / "house.geojson")["features"]}
    ring = np.asarray(house["house-walls"]["geometry"]["coordinates"][0], float)[:, :2]
    R, t, med, p90 = fit_to_outline(prods, ring, cfg.get("walls_layer", "Walls"))
    rot = float(np.degrees(np.arctan2(R[1, 0], R[0, 0])))
    print(f"fit: rotation {rot:.4f} deg, shift {t.round(3)}; the outline's misfit median {med:.3f} m, 90% {p90:.3f} m")

    meshes, layers = {}, []
    for key, g in cfg["groups"].items():
        vs, fs, n = [], [], 0
        for p in prods:
            if selected(g, p) and len(p["faces"]):
                vs.append(to_site(p["verts"], R, t, dz))
                fs.append(p["faces"] + n)
                n += len(p["verts"])
        if vs:
            meshes[f"{key}_v"] = np.vstack(vs).astype(np.float64).round(3)
            meshes[f"{key}_f"] = np.vstack(fs).astype(np.int32)
            layers.append({"key": key, "name": g["name"], "colour": g["colour"], "triangles": len(meshes[f"{key}_f"])})
            print(f"  {key:<16} {len(meshes[f'{key}_f']):6d} triangles")
    np.savez_compressed(out.with_suffix(".npz"), **meshes)

    feats = []
    for p in prods:
        if p["layer"] == cfg.get("drain_layer") and p["lib"].startswith(cfg.get("drain_part", "")):
            v = to_site(p["verts"], R, t, dz)
            a, b, r = pipe_axis(v)
            feats.append(geojson.feature(
                "LineString", [a.round(3).tolist(), b.round(3).tolist()], id=f"ifc-pipe-{p['id']}", kind="pipe",
                status=p["status"].lower(), radius_m=round(r, 3),
                bottom_m=[round(float(a[2] - r), 3), round(float(b[2] - r), 3)]))
        if p["layer"] == cfg.get("retaining_layer") and p["type"] == "IfcWallStandardCase":
            el = f.by_id(p["id"])
            axis = None
            for rep in el.Representation.Representations:
                if rep.RepresentationIdentifier == "Axis":
                    pts = np.array([q.Coordinates for q in rep.Items[0].Points], float)
                    m = np.array(up.get_local_placement(el.ObjectPlacement))  # file units (mm)
                    pts = np.column_stack([pts, np.zeros(len(pts)), np.ones(len(pts))]) @ m.T
                    axis = to_site(pts[:, :3] / 1000.0, R, t, dz)
            usage = ue.get_material(el)
            thick = None
            if usage is not None and usage.is_a("IfcMaterialLayerSetUsage"):
                thick = sum(lay.LayerThickness for lay in usage.ForLayerSet.MaterialLayers) / 1000.0
            v = to_site(p["verts"], R, t, dz)
            base, top = float(v[:, 2].min()), float(v[:, 2].max())
            feats.append(geojson.feature(
                "LineString", [[round(float(x), 3), round(float(y), 3), round(top, 3)] for x, y, _ in axis],
                id=f"ifc-retaining-wall-{p['id']}", kind="retaining-wall", status=p["status"].lower(),
                base_m=round(base, 3), top_m=round(top, 3), thickness_m=None if thick is None else round(thick, 3)))
    fc = geojson.collection(feats, cfg.get("name", out.name), cfg.get("description", ""))
    fc["fit"] = {"source_sha256": sha, "rotation_deg": round(rot, 5), "shift": [round(float(x), 4) for x in t],
                 "datum_dz": dz, "outline_misfit_median_m": round(med, 3), "outline_misfit_p90_m": round(p90, 3)}
    fc["layers"] = layers
    geojson.write(out.with_suffix(".geojson"), fc)
    print(f"wrote {out.with_suffix('.npz')} and {out.with_suffix('.geojson')} ({len(feats)} lines)")
