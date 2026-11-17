"""A sketch from the viewer as a wall option: what it does to the ground, worked out by domus.design.

`domus sketch-worker` answers one JSON request per line on stdin, so the site's ground is read once and each answer takes a fraction of a second.

A wall in a sketch is a line with a top, at one level or per corner, or a height above the ground. Given the side it retains, it also holds
level ground behind it, `back` metres deep and `upstand` below its top. An area is a polygon with a level at its centre and a fall on a compass direction.
"""

import json
import sys

import numpy as np

from ..errors import DomusError
from . import design

DEFAULT_BACK_M = 3.0
GRID_STEP_M = 0.5
GRID_REACH_M = 6.0        # how far past the terraces a batter can reach (domus.design)
DIRECTIONS = {"north": (0, 1), "east": (1, 0), "south": (0, -1), "west": (-1, 0)}
FALLS = {"flat": 0.0, "1:100": 0.01, "1:50": 0.02, "1:40": 0.025, "1:25": 0.04}
KINDS = {"lawn": "lawn", "bed": "planting", "path": "paving"}


def behind(line, side, depth):
    """The points `depth` behind each point of a line: to the left or right looking along it, mitred at the corners."""
    p = np.asarray(line, float)
    d = np.diff(p, axis=0)
    d /= np.maximum(np.hypot(*d.T)[:, None], 1e-9)
    n = np.column_stack([-d[:, 1], d[:, 0]]) * (1.0 if side == "left" else -1.0)   # the side's normal per segment
    out = np.empty_like(p)
    out[0], out[-1] = p[0] + n[0] * depth, p[-1] + n[-1] * depth
    for i in range(1, len(p) - 1):
        m = n[i - 1] + n[i]
        m /= max(float(np.hypot(*m)), 1e-9)
        out[i] = p[i] + m * depth / max(float(m @ n[i]), 0.5)
    return out


def wall_option(it, upstand):
    """(the option's wall, the terrace it holds or None)."""
    en = np.asarray(it["en"], float)
    wall = {"id": f"S{it['id']}", "name": it.get("name") or f"Wall {it['id']}", "material": it.get("material", "timber")}
    tops = it.get("tops") if it.get("mode") == "level" else None
    if tops:
        wall["line"] = [[float(a), float(b), float(t)] for (a, b), t in zip(en, tops)]
    else:
        wall["line"] = en.tolist()
        wall["height_m"] = float(it.get("height") or it.get("height_above_ground") or 0.6)
    side = it.get("retains")
    if side not in ("left", "right"):
        return wall, None
    wall["retained_side"] = side
    if not tops:
        return wall, None
    back = behind(en, side, float(it.get("back") or DEFAULT_BACK_M))
    levels = np.asarray(tops, float) - upstand
    polygon = [*[[*p, float(z)] for p, z in zip(en.tolist(), levels)], *[[*p, float(z)] for p, z in zip(back.tolist()[::-1], levels[::-1])]]
    return wall, {"id": f"S{it['id']}-behind", "name": f"Behind {wall['name']}", "kind": "lawn", "polygon": polygon}


def area_terrace(it):
    en = np.asarray(it["en"], float)
    grad = FALLS.get(it.get("fall"), 0.0)
    d = np.asarray(DIRECTIONS.get(it.get("fall_to"), (0, 1)), float)
    z = float(it["level"]) - grad * ((en - en.mean(axis=0)) @ d)
    return {"id": f"S{it['id']}", "name": it.get("name") or f"Area {it['id']}", "kind": KINDS.get(it.get("area"), "lawn"),
            "polygon": [[float(a), float(b), float(c)] for (a, b), c in zip(en, z)]}


def to_option(items, name="Sketch"):
    """The option a sketch makes. Existing walls stay, since a sketch doesn't say otherwise."""
    upstand = design.settings()["defaults"]["upstand_m"]
    walls, terraces = [], []
    for it in items:
        if it.get("kind") == "wall" and len(it.get("en", [])) >= 2:
            wall, held = wall_option(it, upstand)
            walls.append(wall)
            if held:
                terraces.append(held)
        elif it.get("kind") == "area" and len(it.get("en", [])) >= 3:
            terraces.append(area_terrace(it))
    return {"id": "sketch", "name": name, "short": name, "description": "From a sketch in the viewer.", "steps": 0,
            "walls": walls, "terraces": terraces, "keep_existing": [w["id"] for w in design.existing_walls()]}


def _toml_value(v):
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return repr(round(v, 3)) if isinstance(v, float) else str(v)


def to_toml(opt):
    """An option as a file in options/, in the format of docs/options.md."""
    lines = [f"{k} = {_toml_value(opt[k])}" for k in ("name", "short", "description", "steps", "keep_existing")]
    for table, key in (("walls", "walls"), ("terraces", "terraces")):
        for row in opt[key]:
            lines += ["", f"[[{table}]]", *[f"{k} = {_toml_value(v)}" for k, v in row.items()]]
    return "\n".join(lines) + "\n"


def ground_grid(res):
    """Where the designed ground differs from the existing, on a grid for the viewer. None if nowhere does."""
    d = res["design"]
    shapes = [t.poly for t in d.terraces]
    if not shapes:
        return None
    bb = np.array([s.bounds for s in shapes])
    e = np.arange(np.floor(bb[:, 0].min() - GRID_REACH_M), bb[:, 2].max() + GRID_REACH_M + GRID_STEP_M, GRID_STEP_M)
    n = np.arange(np.floor(bb[:, 1].min() - GRID_REACH_M), bb[:, 3].max() + GRID_REACH_M + GRID_STEP_M, GRID_STEP_M)
    E, N = np.meshgrid(e, n)
    z = d.sample(E, N)
    dz = z - d.existing.sample(E.ravel(), N.ravel()).reshape(E.shape)
    if not np.nanmax(np.abs(dz)) > 0.02:
        return None
    keep = lambda a: [None if not np.isfinite(v) else round(float(v), 2) for v in a.ravel()]
    return {"e0": float(e[0]), "n0": float(n[0]), "step": GRID_STEP_M, "nx": len(e), "ny": len(n), "z": keep(z), "dz": keep(dz)}


def evaluate(items):
    """What the sketch comes to: JSON for the viewer."""
    opt = to_option(items)
    if not opt["walls"] and not opt["terraces"]:
        return {"walls": [], "empty": True}
    res = design.evaluate(opt, step=0.25)
    return {
        "walls": [{k: w[k] for k in ("id", "name", "length_m", "height_min_m", "height_max_m", "retained_max_m", "flags")} for w in res["walls"]],
        "volumes": res["volumes"],
        "flat": {k: res["flat"][k] for k in ("before_m2", "after_m2", "gain_m2")},
        "flags": [e["flag"] for e in res["edge"]] + [d["flag"] for d in res["drains"] if d.get("flag")],
        "grid": ground_grid(res),
    }


def main(argv=None):
    """The worker: a request per line, an answer per line."""
    for line in sys.stdin:
        try:
            answer = evaluate(json.loads(line)["items"])
        except DomusError as e:     # the site's settings or data need putting right: the message says what
            answer = {"error": str(e)}
        except Exception as e:      # noqa: BLE001 - a sketch that can't be worked out is an answer, not the end of the worker
            answer = {"error": f"{type(e).__name__}: {e}"}
        print(json.dumps(answer, default=float), flush=True)
