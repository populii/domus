"""What each of a site's observations reads against what its model says where it looks (docs/observations.md).

    domus prep check observations              # each one, and how far the model is from it
    domus site propose OBSERVATION             # the target set to what was read, proposed for a person to accept

At every build, and whenever it's asked, each observation is read afresh: what it measured from its source (a clip's
latest solved picks, data/video/picks/<picks>-site.csv, by the mark's id) and what the model says of its target as the
model is now (by the element's id), the difference, and how that compares with how sure the reading is. A placement, a
reconstruction placed another way, is measured as the clouds check measures a placed cloud: its clouds moved as it
says, their ground (each 0.25 m cell's lowest flat points) against the drawn ground on open ground, its reading the
median difference, with their spread and how many cells. The states:

- ok: within its uncertainty; near: within twice it; off: further; compared: it gives no uncertainty;
- reading: it has no target, so its reading is shown by itself;
- lost target: its target isn't in the model any more (renamed or removed), or gives no value there;
- no reading: its picks, its mark or its clouds aren't there, the mark wasn't solved, or its clouds see too little
  open ground to measure.

An observation with a target can be proposed (domus.project.changes): intents that set the target to what was read
(a level setting, a vertex's height, the plinth on a face of the house, a ground-fix point under the reading, or a
vertex's place), for a person to accept or reject.
"""

import copy
import csv
import math

import numpy as np

from ..errors import DomusError
from ..geo import geojson
from ..project import observations as data

TONES = {"ok": "ok", "near": "near", "off": "off", "lost target": "lost"}
# A level setting a site can leave out, and the one the model takes for it then (domus.project.schema says so).
WORKED_OUT = {"house.fl_lower": "house.fl_upper"}


def picks_file(site, picks):
    """A clip's solved picks: data/video/picks/<picks>-site.csv, or the CSV `picks` names there."""
    return site.path / "data" / "video" / "picks" / (picks if picks.endswith(".csv") else f"{picks}-site.csv")


class _Model:
    """What the observations read of the site and its model, each read once: the layers, the settings, the ground as
    the build draws it and the house's plinths."""

    def __init__(self, site, ground=None, plinths=None):
        self.site, self._ground, self._plinths = site, ground, plinths
        self.layers, self.picks, self.toes = {}, {}, {}

    def layer(self, name):
        if name not in self.layers:
            path = self.site.path / "data" / "site" / f"{name}.geojson"
            self.layers[name] = geojson.read_optional(path) if path.exists() else None
        return self.layers[name]

    def ground(self, e, n):
        if self._ground is None:
            from ..model import terrain

            drawn = terrain.existing(self.site)
            self._ground = lambda e, n: drawn.sample(np.atleast_1d(e), np.atleast_1d(n))
        return float(np.asarray(self._ground(e, n), float).ravel()[0])

    def plinths(self):
        """The plinth's top on each face of the house, as the model draws it."""
        if self._plinths is None:
            self._plinths = house_plinths(self.site)
        return self._plinths

    def checks(self):
        """The model as the checks see it (domus.checks.model.Model), and the plan area under its objects and low
        plants: made once, when a placement is measured."""
        if not hasattr(self, "_checks"):
            from . import model

            self._checks = model.Model(self.site)
            self._raised = self._checks.raised_footprint()
        return self._checks, self._raised

    def toe(self, f):
        """An existing wall's toe at each of its vertices, as the build takes it: its `toe_m`, else the ground in
        front of its face (terrain.wall_levels)."""
        fid = f["properties"]["id"]
        if fid not in self.toes:
            from ..model import terrain, walls

            wall = walls.from_feature(f)
            _, _, chain, toe, _ = terrain.wall_levels(wall, site=self.site)
            line = np.asarray(f["geometry"]["coordinates"], float)[:, :2]
            at = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(line, axis=0).T))])
            self.toes[fid] = [float(toe[int(np.argmin(np.abs(chain - c)))]) for c in at]
        return self.toes[fid]

    def rows(self, path):
        if path not in self.picks:
            if not path.exists():
                self.picks[path] = None
            else:
                with open(path, newline="", encoding="utf-8") as fh:
                    self.picks[path] = {row.get("id"): row for row in csv.DictReader(fh)}
        return self.picks[path]


def house_plinths(site):
    """The plinth's top on each face of the house (house-walls in house.geojson), as the build draws the house: a
    face's `plinth_top`, else the lowest ground along it."""
    from ..model import house, terrain

    hfc = geojson.read_optional(site.path / "data" / "site" / "house.geojson")
    if not any(f["properties"].get("id") == "house-walls" for f in hfc):
        return {}
    hcfg = site.settings().get("house", {})
    dem = terrain.ground(site)
    hm = house.build(hfc, hcfg["fl_upper"], hcfg["roof_pitch_deg"], hcfg["roof_plane_at_wall"], hcfg["eaves_overhang_m"],
                     lambda e, n: dem.sample(np.atleast_1d(e), np.atleast_1d(n)), **house.roof_options(hcfg))
    out = {}
    for f in hm.faces:
        if "plinth_top" in f.spec:
            out.setdefault(f.id, float(f.spec["plinth_top"]))
    return out


def _reading(m, source):
    """(E, N, H) of a mark as its clip's picks were last solved, or (None, why not)."""
    path = picks_file(m.site, source["picks"])
    rows = m.rows(path)
    rel = path.relative_to(m.site.path).as_posix()
    if rows is None:
        return None, f"its picks, {rel}, aren't there: domus video pick solves them"
    row = rows.get(source["mark"])
    if row is None:
        return None, f"{rel} has no mark {source['mark']}"
    try:
        at = tuple(float(row[k]) for k in ("E", "N", "H"))
    except (KeyError, TypeError, ValueError):
        return None, f"the mark {source['mark']} in {rel} wasn't solved"
    if not all(map(math.isfinite, at)):
        return None, f"the mark {source['mark']} in {rel} wasn't solved"
    return at, None


def _feature(m, t):
    feats = m.layer(t["layer"])
    if feats is None:
        return None, f"data/site/{t['layer']}.geojson isn't there"
    found = [f for f in feats if (f.get("properties") or {}).get("id") == t["id"]]
    if not found:
        return None, f"{t['layer']} has no {t['id']}"
    if len(found) > 1:
        return None, f"{t['layer']} has {len(found)} features with the id {t['id']}"
    return found[0], None


def _vertex(f, t):
    g = f.get("geometry") or {}
    c = g.get("coordinates")
    kind = g.get("type")
    vs = ([c] if c else []) if kind == "Point" else (c[0] if c else []) if kind == "Polygon" else c if kind == "LineString" else None
    if not vs:
        return None, f"{t['id']} is a {g.get('type')}: a vertex is a point's, a line's or a polygon's outer ring's"
    k = t.get("vertex", 0)
    n = len(vs) - (g["type"] == "Polygon" and len(vs) > 1 and vs[0][:2] == vs[-1][:2])
    if k >= n:
        return None, f"{t['id']} has vertices 0 to {n - 1}, not {k}"
    return vs[k], None


def _placement(m, source):
    """A placement's reading, as the clouds check reads a placed cloud: its clouds moved as it says, their ground cells
    against the drawn ground on open ground. ({measured, spread_m, cells, at}, None), or (None, why not)."""
    from ..capture.export_cloud import moved
    from . import model

    checked, raised = m.checks()
    parts = []
    for name in source["clouds"]:
        cams = f"{name.rsplit('-', 1)[0]}-site.npz"
        if not (model.video_dir(m.site) / f"{name}.npz").exists():
            return None, f"its cloud, data/video/{name}.npz, isn't there"
        if not name.endswith("-scan") and not (model.video_dir(m.site) / cams).exists():
            return None, f"its clip's cameras, data/video/{cams}, aren't there"
        parts.append(model.placed_cloud(checked, name))
    cells = model.ground_cells(moved(np.vstack(parts), source))
    if not len(cells):
        return None, "its clouds show no flat ground"
    r, open_ = model.open_ground(checked, cells, raised)
    if open_.sum() < model.MIN_OPEN_CELLS:
        return None, f"its clouds see too little open ground: {int(open_.sum())} cells"
    d = r[open_]
    e, n = cells[open_, 0].mean(), cells[open_, 1].mean()
    h = float(np.median(cells[open_, 2]))
    return {"measured": round(float(np.median(d)), 3),
            "spread_m": round(float(np.percentile(d, 75) - np.percentile(d, 25)), 3),
            "cells": int(open_.sum()), "at": [round(float(e), 3), round(float(n), 3), round(h, 3)]}, None


def expected(m, o, at):
    """What the model says of an observation's target, (value, None) or (None, why it says nothing): a level for a
    level, [E, N] or [E, N, H] for a point."""
    t = o["target"]
    if "setting" in t:
        for key in (t["setting"], WORKED_OUT.get(t["setting"])):      # one left out is worked out as the model does
            here = m.site.settings()
            for part in (key or "").split("."):
                here = dict.get(here, part) if isinstance(here, dict) else None
            if isinstance(here, (int, float)) and not isinstance(here, bool):
                return float(here), None
        return None, f"data/site.toml gives no {t['setting']}"
    if t["what"] == "ground":
        h = m.ground(at[0], at[1])
        return (h, None) if math.isfinite(h) else (None, "the model has no ground under it")
    f, why = _feature(m, t)
    if f is None:
        return None, why
    if t["what"] == "plinth":
        given = (f["properties"].get("plinth_top") or {}).get(t["face"])
        if isinstance(given, (int, float)) and not isinstance(given, bool):
            return float(given), None
        drawn = m.plinths().get(t["face"]) if t["layer"] == "house" and t["id"] == "house-walls" else None
        return (drawn, None) if drawn is not None else (None, f"{t['id']} has no face {t['face']}")
    v, why = _vertex(f, t)
    if v is None:
        return None, why
    if t["what"] == "toe":
        if f["geometry"]["type"] != "LineString":
            return None, f"{t['id']} isn't a wall's line: a toe is an existing wall's"
        return m.toe(f)[t.get("vertex", 0)], None
    if t["what"] == "position":
        return [float(x) for x in v[:3]], None
    if len(v) < 3:
        return None, f"{t['id']}'s vertex {t.get('vertex', 0)} has no height"
    return float(v[2]), None


def evaluate(site, ground=None, plinths=None):
    """The site's observations, each as read now: its `id`, `kind`, `name`, `source`, `target`, `uncertainty_m` and
    `note`; `at`, where the reading is (E, N, H); `measured`, what it read (a level, or [E, N, H] for a point);
    `expected`, what the model says of its target; `difference` (measured less expected for a level, the distance in
    plan for a point, with `offset` [dE, dN]); its `state` (the module's docstring); and `why`, for a lost target or
    no reading. A placement's `measured` and `difference` are both its ground's median height above the model's, with
    `spread_m` (the middle half of the cells' differences, from the first quartile to the third), `cells` (how many open
    cells it's measured over) and `at` (their middle). `ground` and `plinths` are the build's, when it asks."""
    found = data.read(site)
    if not found:
        return []
    m = _Model(site, ground, plinths)
    out = []
    for o in found:
        r = {"id": o["id"], "kind": o["kind"], "name": o.get("name") or o["id"], "source": o["source"],
             "target": o.get("target"), **{k: o[k] for k in ("uncertainty_m", "note", "lift_m") if o.get(k) is not None}}
        out.append(r)
        u = o.get("uncertainty_m")
        if o["kind"] == "placement":
            reading, why = _placement(m, o["source"])
            if reading is None:
                r.update(state="no reading", why=why)
                continue
            r.update(reading)
            d = r["difference"] = reading["measured"]          # its ground's height above the model's
            r["state"] = "compared" if u is None else "ok" if abs(d) <= u else "near" if abs(d) <= 2 * u else "off"
            continue
        at, why = _reading(m, o["source"])
        if at is None:
            r.update(state="no reading", why=why)
            continue
        r["at"] = list(at)
        r["measured"] = at[2] if o["kind"] == "level" else list(at)
        if not o.get("target"):
            r["state"] = "reading"
            continue
        value, why = expected(m, o, at)
        if value is None:
            r.update(state="lost target", why=why)
            continue
        r["expected"] = value
        if o["kind"] == "level":
            d = r["difference"] = round(at[2] - value, 3)           # to the millimetre, as it's shown and judged
        else:
            r["offset"] = [round(at[0] - value[0], 3), round(at[1] - value[1], 3)]
            d = r["difference"] = round(math.hypot(*r["offset"]), 3)
        r["state"] = "compared" if u is None else "ok" if abs(d) <= u else "near" if abs(d) <= 2 * u else "off"
    return out


def label(r):
    """An observation's label, as the viewer shows it."""
    name = r["name"]
    if r["kind"] == "placement":
        return f"{name}: ground {r['measured']:+.2f} m ({r['cells']} cells, spread {r['spread_m']:.2f} m" + \
            (f", ±{r['uncertainty_m']:.2f}" if r.get("uncertainty_m") is not None else "") + ")"
    if r["kind"] == "point":
        if "difference" in r:
            return f"{name}: {r['difference']:.2f} m off in plan ({r['offset'][0]:+.2f} E, {r['offset'][1]:+.2f} N)"
        return f"{name}: E {r['at'][0]:.2f} N {r['at'][1]:.2f}" + (" (its target is lost)" if r["state"] == "lost target" else "")
    text = f"{name}: {r['measured']:.2f}"
    if "difference" in r:
        text += f" (model {r['expected']:.2f}, {r['difference']:+.2f} m" + \
            (f", ±{r['uncertainty_m']:.2f}" if r.get("uncertainty_m") is not None else "") + ")"
    elif r["state"] == "lost target":
        text += " (its target is lost)"
    elif r.get("note"):
        text += f" ({r['note']})"
    return text


def report(r):
    """An observation as summary.json and the check report it: the reading, the model's value and how they compare."""
    keep = ("id", "kind", "name", "state", "measured", "expected", "difference", "offset", "spread_m", "cells",
            "uncertainty_m", "target", "source", "why")
    out = {k: r[k] for k in keep if k in r}
    for k in ("measured", "expected"):
        if isinstance(out.get(k), float):
            out[k] = round(out[k], 3)
        elif isinstance(out.get(k), list):
            out[k] = [round(x, 3) for x in out[k]]
    return out


def check(site, findings):
    """`domus prep check observations`: a finding for each observation; one further from the model than twice its
    uncertainty fails, one between once and twice it, or one that lost its target or its reading, warns."""
    fails = 0
    for r in evaluate(site):
        level = {"off": "fail", "near": "warn", "lost target": "warn", "no reading": "warn"}.get(r["state"], "info")
        fails += level == "fail"
        if r["state"] in ("lost target", "no reading"):
            detail = f"{r['state']}: {r['why']}"
        else:
            detail = f"{r['state']}: {label(r)}"
        findings.append(("observations", level, r["id"], detail))
    return fails


# --------------------------------------------------------------------------- proposing what was read

def intents_for(site, r):
    """The intents that set an observation's target (`r`, as evaluate gives it) to what it read."""
    t, o_id = r.get("target"), r["id"]
    if r["kind"] == "placement" or not t:
        raise DomusError(f"Observation {o_id} has no target to set: give it one (docs/observations.md)")
    if r["state"] in ("no reading", "lost target"):
        raise DomusError(f"Observation {o_id} can't be proposed: {r['why']}")
    basis = f"Read off {r['source']['picks']} ({r['source']['mark']}), observation {o_id}."
    e, n, h = (round(x, 3) for x in r["at"])
    if r["kind"] == "point":
        return [{"kind": "coords", "layer": t["layer"], "id": t["id"], "vertex": t.get("vertex", 0), "en": [e, n],
                 "basis": basis}]
    if "setting" in t:
        return [{"kind": "setting", "key": t["setting"], "value": h}]
    if t["what"] == "plinth":
        f = next(f for f in geojson.read_optional(site.path / "data" / "site" / f"{t['layer']}.geojson")
                 if f["properties"].get("id") == t["id"])
        plinths = copy.deepcopy(f["properties"].get("plinth_top") or {})
        plinths[t["face"]] = h
        return [{"kind": "props", "layer": t["layer"], "id": t["id"], "set": {"plinth_top": plinths}}]
    if t["what"] == "toe":
        f = next(f for f in geojson.read_optional(site.path / "data" / "site" / f"{t['layer']}.geojson")
                 if f["properties"].get("id") == t["id"])
        toes = list(f["properties"].get("toe_m") or [round(x, 3) for x in _Model(site).toe(f)])
        toes[t.get("vertex", 0)] = h
        return [{"kind": "props", "layer": t["layer"], "id": t["id"], "set": {"toe_m": toes}}]
    if t["what"] == "ground":
        fid = f"observation-{o_id}"
        fixes = site.path / "data" / "site" / "ground-fixes.geojson"
        if any(f["properties"].get("id") == fid for f in geojson.read_optional(fixes)):
            return [{"kind": "coords", "layer": "ground-fixes", "id": fid, "enz": [e, n, h], "basis": basis}]
        props = {"id": fid, "source": f"observation {o_id}", "basis": basis}
        if r.get("uncertainty_m") is not None:
            props["confidence"] = f"within {r['uncertainty_m']:.2f} m"
        return [{"kind": "add", "layer": "ground-fixes",
                 "feature": {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [e, n, h]}}}]
    return [{"kind": "level", "layer": t["layer"], "id": t["id"], "vertex": t.get("vertex", 0), "z": h, "basis": basis}]


def propose(site, observation, say=print):
    """Proposes setting an observation's target to what it read, for a person to accept or reject. Gives the
    proposal."""
    from ..project import changes
    from ..project import intents as kinds

    found = [r for r in evaluate(site) if r["id"] == observation]
    if not found:
        raise DomusError(f"This site has no observation {observation} (domus prep check observations lists them)")
    r = found[0]
    intents = kinds.read(intents_for(site, r))
    if "difference" in r and r["kind"] == "level":
        why = f"{r['name']}: read {r['measured']:.3f}, the model {r['expected']:.3f} ({r['difference']:+.3f} m)"
    elif "difference" in r:
        why = f"{r['name']}: read {r['difference']:.3f} m in plan from the model's"
    else:
        why = f"{r['name']}: read {r['measured'] if r['kind'] == 'level' else r['at']}"
    if r.get("uncertainty_m") is not None:
        why += f", within {r['uncertainty_m']:.3f} m"
    return changes.propose(site, intents, why + ".", f"observation {observation}", say)
