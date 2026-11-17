"""Edits to a site's curated layers (data/site/*.geojson) that say what they did and keep what was there.

    domus site shift SPEC.json BACKUP_DIR [--apply]
    domus site edit SPEC.json BACKUP_DIR [--apply]
    domus site regrade CLOUD[,CLOUD] SPEC.json BACKUP_DIR [--apply]

Each prints what it would change and, without --apply, writes nothing. With --apply each file is first copied to
BACKUP_DIR (a folder of its own: a copy already there stops it) and the copy read back, then written to a temporary
file in the file's own layout (one-space indent and a final newline, or compact), read back and checked, and only then
put in place.

`shift` moves named features by one vector in plan, and adds a sentence to each one's basis:
    {"shift": [dE, dN], "dz": 0.06 (optional: their heights too), "basis": "how they're placed now",
     "files": {"walls-existing.geojson": ["W2", "W3"], "boundary.geojson": "*", ...}}
The check: the same features, the named ones moved by exactly the shift and the others untouched.

`edit` changes named features' coordinates and properties, adds features and deletes them:
    {"house.geojson": [{"id": "window-3", "so": [[10.0, 4.3], [11.1, 4.3]], "props": {"basis": "..."},
                        "drop_props": ["confidence"]},
                       {"id": "drain-lid", "so": [9.0, 10.3]},
                       {"id": "W1", "soz": [[14.3, 0.6, 40.7], ...]},
                       {"id": "new-thing", "new": {"type": "LineString"}, "soz": [...], "props": {...}},
                       {"id": "old-thing", "delete": true}], ...}
"so"/"soz" are in the wall options' frame, "en"/"enz" on the site's grid; "so" and "en" keep each vertex's own height
when the feature has heights and the number of its vertices doesn't change. A polygon's ring is given open or closed
and written closed. The check: the named features as asked, and every other one untouched.

`regrade` gives ground-fix points and lines (data/site/ground-fixes.geojson) the levels a placed cloud shows today:
    {"features": ["lawn-ground", ...], "radius": 0.3, "min_points": 12, "percentile": 20,
     "max_rise": 0.6, "max_drop": 0.25, "fallback_radius": 3.0, "keep_unseen": ["side-path-ground"],
     "basis": "how their levels were read"}
Each vertex takes the given percentile of the heights of the cloud's points within the radius (the soil and the grass
under taller things). A vertex the cloud doesn't see well enough (fewer points, or a reading further than max_rise or
max_drop from its level: plants, holes) keeps its level plus the median change of the read vertices within
fallback_radius (all of them, if none is that near), so the surface it belongs to moves as one; one of a feature in
keep_unseen keeps its level. The check: only the named features' heights changed.
"""

import copy
import json
import os
import shutil
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from .. import crs
from ..errors import DomusError


def layers_dir():
    """The site's curated layers, data/site/."""
    return crs.ROOT / "data" / "site"


def _read(name):
    path = layers_dir() / name
    if not path.exists():
        raise DomusError(f"There's no {path}")
    raw = path.read_text(encoding="utf-8")
    return path, raw, json.loads(raw), "\n" not in raw.strip()


def _write(path, data, raw, compact, backup, name, check):
    """Backs `path` up into `backup` and replaces it with `data`, in its layout, once `check(written)` passes."""
    backup = Path(backup)
    backup.mkdir(parents=True, exist_ok=True)
    copy_path = backup / name
    if copy_path.exists():
        raise DomusError(f"{copy_path} is already there: use a fresh backup folder")
    shutil.copy2(path, copy_path)
    if copy_path.read_text(encoding="utf-8") != raw:
        raise DomusError(f"backup of {name} does not read back the same")
    text = json.dumps(data, separators=(",", ":")) if compact else json.dumps(data, indent=1) + "\n"
    tmp = Path(f"{path}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    check(json.loads(tmp.read_text(encoding="utf-8")))
    os.replace(tmp, path)
    return copy_path


def _with_basis(props, basis):
    if basis and basis not in props.get("basis", ""):
        old = props.get("basis", "")
        props["basis"] = (old + " " if old else "") + basis


# --------------------------------------------------------------------------- shift

def _first(c):
    return c if isinstance(c[0], (int, float)) else _first(c[0])


def _flat(c, out):
    if isinstance(c[0], (int, float)):
        out.append(c)
    else:
        for x in c:
            _flat(x, out)
    return out


def shift(spec, backup, apply=False, say=print):
    de, dn = spec["shift"]
    dz = float(spec.get("dz", 0.0))

    def moved(c):
        if isinstance(c[0], (int, float)):
            return [round(c[0] + de, 3), round(c[1] + dn, 3), *([round(c[2] + dz, 3)] if len(c) > 2 else []), *c[3:]]
        return [moved(x) for x in c]

    for name, ids in spec["files"].items():
        path, raw, data, compact = _read(name)
        before = json.loads(raw)
        found = set()
        for f in data["features"]:
            pid = f["properties"].get("id")
            if ids != "*" and pid not in ids:
                continue
            found.add(pid)
            a = _first(f["geometry"]["coordinates"])
            f["geometry"]["coordinates"] = moved(f["geometry"]["coordinates"])
            b = _first(f["geometry"]["coordinates"])
            _with_basis(f["properties"], spec.get("basis"))
            say(f"   {name}: {pid}: ({a[0]:.3f}, {a[1]:.3f}) -> ({b[0]:.3f}, {b[1]:.3f})")
        if ids != "*":
            missing = set(ids) - found
            if missing:
                raise DomusError(f"{name}: no feature {sorted(missing)}")
        if not apply:
            continue

        def check(after, name=name, ids=ids, before=before):
            if len(after["features"]) != len(before["features"]):
                raise DomusError(f"{name}: feature count changed")
            for fa, fb in zip(after["features"], before["features"], strict=True):
                pid = fb["properties"].get("id")
                ca, cb = _flat(fa["geometry"]["coordinates"], []), _flat(fb["geometry"]["coordinates"], [])
                named = ids == "*" or pid in ids
                want, dz_want = ((de, dn), dz) if named else ((0.0, 0.0), 0.0)
                for pa, pb in zip(ca, cb, strict=False):
                    if abs(pa[0] - pb[0] - want[0]) > 0.0011 or abs(pa[1] - pb[1] - want[1]) > 0.0011 or \
                            (len(pa) > 2 and abs(pa[2] - pb[2] - dz_want) > 0.0011) or pa[3:] != pb[3:]:
                        raise DomusError(f"{name}: {pid} did not move as meant")
                if len(ca) != len(cb):
                    raise DomusError(f"{name}: {pid} changed its number of points")

        copy_path = _write(path, data, raw, compact, backup, name, check)
        say(f"{name}: written ({len(found) if ids != '*' else len(data['features'])} features moved); backup {copy_path}")
    if not apply:
        say("dry run: nothing written")


# --------------------------------------------------------------------------- edit

def _shape(coords, gtype):
    if gtype == "Point":
        return coords
    if gtype == "Polygon":
        ring = [list(c) for c in coords]
        if ring[0] != ring[-1]:
            ring.append(list(ring[0]))
        return [ring]
    return coords


def _vertices(geom):
    c = geom["coordinates"]
    if geom["type"] == "Point":
        return [c]
    if geom["type"] == "Polygon":
        return c[0]
    return c


def edit(spec, backup, apply=False, say=print):
    from .. import design

    fr = design.frame()

    def to_en(so):
        a = np.asarray(so, float)
        one = a.ndim == 1
        en = fr.en(np.atleast_2d(a)[:, :2])
        out = [[round(float(e), 3), round(float(n), 3)] + [round(float(z), 3) for z in row[2:]]
               for (e, n), row in zip(en, np.atleast_2d(a), strict=True)]
        return out[0] if one else out

    for name, edits in spec.items():
        path, raw, data, compact = _read(name)
        before = json.loads(raw)
        by_id = {f["properties"].get("id"): f for f in data["features"]}
        touched, expect = set(), {}
        for e in edits:
            fid = e["id"]
            touched.add(fid)
            if e.get("delete"):
                if fid not in by_id:
                    raise DomusError(f"{name}: no feature {fid} to delete")
                data["features"] = [f for f in data["features"] if f["properties"].get("id") != fid]
                say(f"   {name}: {fid}: deleted")
                continue
            if "new" in e:
                if fid in by_id:
                    raise DomusError(f"{name}: {fid} is already there")
                f = {"type": "Feature", "properties": {"id": fid}, "geometry": {"type": e["new"]["type"], "coordinates": []}}
                data["features"].append(f)
                by_id[fid] = f
            if fid not in by_id:
                raise DomusError(f"{name}: no feature {fid}")
            f = by_id[fid]
            g = f["geometry"]
            new = None
            for key in ("so", "soz", "en", "enz"):
                if key in e:
                    new = to_en(e[key]) if key.startswith("so") else json.loads(json.dumps(e[key]))
                    if key in ("so", "en") and g["coordinates"]:
                        old = _vertices(g)
                        cand = [new] if g["type"] == "Point" else new
                        olds = old[:-1] if g["type"] == "Polygon" and len(old) == len(cand) + 1 else old
                        if len(olds) == len(cand) and all(len(o) > 2 for o in olds):
                            cand = [c[:2] + list(o[2:]) for c, o in zip(cand, olds, strict=True)]
                            new = cand[0] if g["type"] == "Point" else cand
            if new is not None:
                was = _vertices(g)[0] if g["coordinates"] else None
                g["coordinates"] = _shape(new, g["type"])
                say(f"   {name}: {fid}: {len(_vertices(g))} vertices, first {was} -> {_vertices(g)[0]}")
            for k, v in e.get("props", {}).items():
                if f["properties"].get(k) != v:
                    say(f"   {name}: {fid}.{k}: {str(f['properties'].get(k))[:70]!r} -> {str(v)[:110]!r}")
                f["properties"][k] = v
            for k in e.get("drop_props", []):
                if k in f["properties"]:
                    say(f"   {name}: {fid}.{k}: dropped")
                    del f["properties"][k]
            expect[fid] = copy.deepcopy(f)
        if not apply:
            continue

        def check(after, name=name, before=before, touched=touched, expect=expect, data=data):
            a_by = {f["properties"].get("id"): f for f in after["features"]}
            for f in before["features"]:
                fid = f["properties"].get("id")
                if fid not in touched and a_by.get(fid) != f:
                    raise DomusError(f"{name}: {fid} changed though it was not named: not written")
            for fid, f in expect.items():
                if a_by.get(fid) != f:
                    raise DomusError(f"{name}: {fid} does not read back as written")
            if len(after["features"]) != len(data["features"]):
                raise DomusError(f"{name}: feature count is off")

        copy_path = _write(path, data, raw, compact, backup, name, check)
        say(f"{name}: written ({len(touched)} features changed, {len(data['features'])} in all); backup {copy_path}")
    if not apply:
        say("dry run: nothing written")


# --------------------------------------------------------------------------- regrade

def regrade(clouds, spec, backup, apply=False, say=print):
    from ..prep.cloud import read_clouds

    radius, nmin, pct = spec.get("radius", 0.3), spec.get("min_points", 12), spec.get("percentile", 20)
    rise, drop, reach = spec.get("max_rise", 0.6), spec.get("max_drop", 0.25), spec.get("fallback_radius", 3.0)
    parts = []
    for name in clouds:                 # several clouds of one placement count as one; a sparse cloud's well seen points
        stem = name.removesuffix(".npz")
        parts.append(read_clouds([name], all_points=not stem.endswith("site"))[0])
    xyz = np.vstack(parts)
    tree = cKDTree(xyz[:, :2])
    name = "ground-fixes.geojson"
    path, raw, data, _ = _read(name)
    before = json.loads(raw)
    by_id = {f["properties"].get("id"): f for f in data["features"]}

    def verts(g):
        return [g["coordinates"]] if g["type"] == "Point" else g["coordinates"]

    allp, owner = [], []
    for fid in spec["features"]:
        if fid not in by_id:
            raise DomusError(f"no feature {fid}")
        for k, v in enumerate(verts(by_id[fid]["geometry"])):
            if len(v) < 3:
                raise DomusError(f"{fid}: vertex {k} has no height")
            allp.append(v)
            owner.append((fid, k))
    p = np.array(allp, float)
    new = np.full(len(p), np.nan)
    for i, idx in enumerate(tree.query_ball_point(p[:, :2], radius)):
        if len(idx) >= nmin:
            zc = float(np.percentile(xyz[idx, 2], pct))
            if -drop <= zc - p[i, 2] <= rise:
                new[i] = zc
    read = np.isfinite(new)
    if not read.any():
        raise DomusError("the cloud sees none of these points")
    change = new - p[:, 2]
    rt = cKDTree(p[read, :2])
    overall = float(np.median(change[read]))
    keep_unseen = set(spec.get("keep_unseen", []))
    for i in np.flatnonzero(~read):
        if owner[i][0] in keep_unseen:
            new[i] = p[i, 2]
            continue
        near = rt.query_ball_point(p[i, :2], reach)
        new[i] = p[i, 2] + (float(np.median(change[read][near])) if near else overall)
    change = new - p[:, 2]
    for fid in spec["features"]:
        m = np.array([o[0] == fid for o in owner])
        ch = change[m]
        say(f"   {fid}: {int(m.sum())} vertices, {int((read & m).sum())} read from the cloud; change median {np.median(ch):+.3f}, "
            f"10th to 90th percentile {np.percentile(ch, 10):+.3f} to {np.percentile(ch, 90):+.3f}, extremes {ch.min():+.3f} / {ch.max():+.3f}")
        f = by_id[fid]
        vs = verts(f["geometry"])
        for (ofid, k), z in zip(owner, new, strict=True):
            if ofid == fid:
                vs[k][2] = round(float(z), 3)
        _with_basis(f["properties"], spec.get("basis"))
    if not apply:
        say("dry run: nothing written")
        return

    def check(after):
        a_by = {f["properties"].get("id"): f for f in after["features"]}
        for f in before["features"]:
            fid = f["properties"].get("id")
            if fid not in spec["features"]:
                if a_by.get(fid) != f:
                    raise DomusError(f"{fid} changed though it was not named: not written")
            else:
                va, vb = verts(a_by[fid]["geometry"]), verts(f["geometry"])
                if len(va) != len(vb) or any(x[:2] != y[:2] for x, y in zip(va, vb, strict=True)):
                    raise DomusError(f"{fid}: its points moved in plan: not written")

    copy_path = _write(path, data, raw, False, backup, name, check)
    say(f"ground-fixes.geojson written; backup {copy_path}")
