"""Edits to a site's curated layers (data/site/*.geojson) that say what they did and keep what was there.

    domus site shift SPEC.json [BACKUP_DIR] [--apply]
    domus site edit SPEC.json [BACKUP_DIR] [--apply]
    domus site regrade CLOUD[,CLOUD] SPEC.json [BACKUP_DIR] [--apply]

Each prints what it would change and, without --apply, writes nothing. With --apply each is an edit on the route every
change to a site takes (domus.project.changes): each file is first copied to BACKUP_DIR if it's given (a folder of its
own: a copy already there stops it), else beside the edit's record in .domus/edits/, and the copy read back; then the
new file is written to a temporary file in the file's own layout (its indent, separators and final newline), read back
and checked, and only then put in place, all the files or none. The edit is recorded with who, when and why (--why,
else the spec's basis), and `domus site undo` undoes it.

`shift` moves named features by one vector in plan, and adds a sentence to each one's basis:
    {"shift": [dE, dN], "dz": 0.06 (optional: their heights too), "basis": "how they're placed now",
     "files": {"walls-existing.geojson": ["W2", "W3"], "boundary.geojson": "*", ...}}
The check: the same features, the named ones moved by exactly the shift and the others untouched. Each file's is a
`move` intent.

`edit` changes named features' coordinates and properties, adds features and deletes them:
    {"house.geojson": [{"id": "window-3", "so": [[10.0, 4.3], [11.1, 4.3]], "props": {"basis": "..."},
                        "drop_props": ["confidence"]},
                       {"id": "drain-lid", "so": [9.0, 10.3]},
                       {"id": "W1", "soz": [[14.3, 0.6, 40.7], ...]},
                       {"id": "new-thing", "new": {"type": "LineString"}, "soz": [...], "props": {...}},
                       {"id": "old-thing", "delete": true}], ...}
"so"/"soz" are in the wall options' frame, "en"/"enz" on the site's grid; "so" and "en" keep each vertex's own height
when the feature has heights and the number of its vertices doesn't change. A polygon's ring is given open or closed
and written closed. The check: the named features as asked, and every other one untouched. Each change is a `coords`,
`props`, `add` or `delete` intent; SPEC.json can also be intents themselves (docs/changes.md).

`regrade` gives ground-fix points and lines (data/site/ground-fixes.geojson) the levels a placed cloud shows today:
    {"features": ["lawn-ground", ...], "radius": 0.3, "min_points": 12, "percentile": 20,
     "max_rise": 0.6, "max_drop": 0.25, "fallback_radius": 3.0, "keep_unseen": ["side-path-ground"],
     "basis": "how their levels were read"}
Each vertex takes the given percentile of the heights of the cloud's points within the radius (the soil and the grass
under taller things). A vertex the cloud doesn't see well enough (fewer points, or a reading further than max_rise or
max_drop from its level: plants, holes) keeps its level plus the median change of the read vertices within
fallback_radius (all of them, if none is that near), so the surface it belongs to moves as one; one of a feature in
keep_unseen keeps its level. The check: only the named features' heights changed. Each feature's is a `level` intent.
"""

import json

import numpy as np
from scipy.spatial import cKDTree

from ..errors import DomusError
from . import changes
from . import intents as kinds


def layers_dir(site):
    """The site's curated layers, data/site/."""
    return site.path / "data" / "site"


def _layer(name):
    return name.removesuffix(".geojson")


def _why(why, spec, command):
    """Why an edit from an older spec is made: as given, else the spec's basis, else that nobody said."""
    for reason in (why, spec.get("basis") if isinstance(spec, dict) else None):
        if isinstance(reason, str) and reason.strip():
            return reason
    return f"domus site {command}, with no reason given"


# --------------------------------------------------------------------------- shift

def shift(site, spec, backup=None, apply=False, say=print, why=None, who=None):
    """`domus site shift`: the named features of each file moved by one vector, as `move` intents. Gives the edit's
    record (with apply False, what it would hold)."""
    de, dn = spec["shift"]
    by = [de, dn, spec["dz"]] if "dz" in spec else [de, dn]
    basis = {"basis": spec["basis"]} if spec.get("basis") else {}
    intents = kinds.read([{"kind": "move", "layer": _layer(name), "ids": ids, "by": by, **basis}
                          for name, ids in spec["files"].items()])

    def written(name, copy_path, data):
        ids = spec["files"][name]
        return f"{name}: written ({len(data['features']) if ids == '*' else len(set(ids))} features moved); backup {copy_path}"

    return changes.apply(site, intents, _why(why, spec, "shift"), who, backup=backup, dry_run=not apply, say=say,
                         written=written)


# --------------------------------------------------------------------------- edit

def edit_intents(site, spec):
    """The intents an older `domus site edit` spec ({layer file: [changes]}) comes to, in its order."""
    frame = changes.frame_of(site)
    out = []
    for name, entries in spec.items():
        layer = _layer(name)
        for e in entries:
            fid = e["id"]
            if e.get("delete"):
                out.append({"kind": "delete", "layer": layer, "id": fid})
                continue
            keys = [k for k in ("so", "soz", "en", "enz") if k in e]
            if "new" in e:
                coords = []
                if keys:
                    coords = kinds.to_en(frame(), e[keys[-1]]) if keys[-1].startswith("so") else json.loads(json.dumps(e[keys[-1]]))
                out.append({"kind": "add", "layer": layer, "feature": {
                    "type": "Feature", "properties": {"id": fid},
                    "geometry": {"type": e["new"]["type"], "coordinates": kinds.shape(coords, e["new"]["type"])}}})
            elif keys:
                out.append({"kind": "coords", "layer": layer, "id": fid, keys[-1]: e[keys[-1]]})
            if e.get("props") or e.get("drop_props"):
                out.append({"kind": "props", "layer": layer, "id": fid, **({"set": e["props"]} if e.get("props") else {}),
                            **({"drop": e["drop_props"]} if e.get("drop_props") else {})})
    return kinds.read(out) if out else []


def edit(site, spec, backup=None, apply=False, say=print, why=None, who=None):
    """`domus site edit` with a spec in the form above: named features' coordinates and properties changed, features
    added and deleted. Gives the edit's record (with apply False, what it would hold). Intents go to
    domus.project.changes.apply themselves."""
    intents = edit_intents(site, spec)
    if not intents:
        say("Nothing to change.")
        return None

    def written(name, copy_path, data):
        return (f"{name}: written ({len({e['id'] for e in spec[name]})} features changed, {len(data['features'])} in all); "
                f"backup {copy_path}")

    return changes.apply(site, intents, _why(why, spec, "edit"), who, backup=backup, dry_run=not apply, say=say,
                         written=written)


# --------------------------------------------------------------------------- regrade

def regrade(site, clouds, spec, backup=None, apply=False, say=print, why=None, who=None):
    """`domus site regrade`: ground-fix points and lines given the levels a placed cloud shows, as `level` intents.
    Gives the edit's record (with apply False, what it would hold)."""
    from ..prep.cloud import read_clouds

    radius, nmin, pct = spec.get("radius", 0.3), spec.get("min_points", 12), spec.get("percentile", 20)
    rise, drop, reach = spec.get("max_rise", 0.6), spec.get("max_drop", 0.25), spec.get("fallback_radius", 3.0)
    parts = []
    for name in clouds:                 # several clouds of one placement count as one; a sparse cloud's well seen points
        stem = name.removesuffix(".npz")
        parts.append(read_clouds(site, [name], all_points=not stem.endswith("site"))[0])
    xyz = np.vstack(parts)
    tree = cKDTree(xyz[:, :2])
    name = "ground-fixes.geojson"
    path = layers_dir(site) / name
    if not path.exists():
        raise DomusError(f"There's no {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
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
    intents = []
    for fid in spec["features"]:
        m = np.array([o[0] == fid for o in owner])
        ch = change[m]
        say(f"   {fid}: {int(m.sum())} vertices, {int((read & m).sum())} read from the cloud; change median {np.median(ch):+.3f}, "
            f"10th to 90th percentile {np.percentile(ch, 10):+.3f} to {np.percentile(ch, 90):+.3f}, extremes {ch.min():+.3f} / {ch.max():+.3f}")
        intents.append({"kind": "level", "layer": "ground-fixes", "id": fid,
                        "z": [round(float(z), 3) for (ofid, _), z in zip(owner, new, strict=True) if ofid == fid],
                        **({"basis": spec["basis"]} if spec.get("basis") else {})})
    return changes.apply(site, kinds.read(intents), _why(why, spec, "regrade"), who, backup=backup, dry_run=not apply,
                         say=say, lines=False, written=lambda name, copy_path, data: f"{name} written; backup {copy_path}")
