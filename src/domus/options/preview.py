"""The viewer's sketch preview, worked out in Python: a sketch as solids, from what a built model gives the viewer
(model.json's `regrade` and its `ground` layer), by the steps app/viewer/sketch-core.js takes in the browser. The two
are held to each other by the sketches in tests/sketch_corpus.json, and these solids to the build's own rule
(domus.options.scheme) by the tests.

The preview works a sketch out as the build does (sketch.sketched, scheme.Design, solids.option_solids), with three
differences it shares with the viewer's copy, each worth far less than the 0.05% of the soil moved the tests allow:

- a batter's free edge is worked out exactly (solids.free_pieces), where Design takes shapely's buffers round the
  walls, and the batters are worked out from it (solids.batter_levels);
- the ground is the model's ground layer, its corners to the millimetre as the build writes them, and each held about
  the solids' origin in single precision, as the viewer's mesh holds it;
- a batter's cone has PREVIEW_SEGMENTS sides; while a sketch is dragged, DRAG_SEGMENTS, and where batters disagree
  the grid is DRAG_CONFLICT_STEP_M.

A pure part of the model: it reads no site, only a model's JSON.
"""

import functools
from types import SimpleNamespace

import numpy as np
from shapely.geometry import LineString, Polygon

from ..geo.mesh import Surface
from . import scheme, sketch, solids

PREVIEW_SEGMENTS = 24       # the sides of a batter's cone in the preview: between the 16 and 32 the owner chose
DRAG_SEGMENTS = 16          # and while a sketch is dragged
DRAG_CONFLICT_STEP_M = 0.05     # the grid where batters disagree, while a sketch is dragged (solids.CONFLICT_STEP_M else)


def _on_grid(model, pts):
    """Points in viewer metres (x, z) on the site's grid (E, N)."""
    e0, n0 = float(model["origin"][0]), float(model["origin"][1])
    p = np.asarray(pts, float).reshape(-1, 2)
    return np.column_stack([p[:, 0] + e0, n0 - p[:, 1]])


def _even_odd(model, rings):
    """Rings in viewer metres as one shape on the site's grid, even-odd: a ring inside another is a hole in it."""
    shape = Polygon()
    for ring in rings:
        shape = shape.symmetric_difference(Polygon(_on_grid(model, ring)))
    return shape


def regrade_area(model):
    """The regrade area a model gives the viewer, on the site's grid."""
    return _even_odd(model, model["regrade"]["area"])


def old_walls(model):
    """The existing walls a model gives the viewer, as the solids take them (option_solids' `old`): each one's id, and
    its plan, its line, the distances along it and the soil's level under it (its toe), on the site's grid."""
    h0 = float(model["origin"][2])
    return [(w["id"], (_even_odd(model, w["plan"]), LineString(_on_grid(model, w["line"])), np.asarray(w["chain"], float),
                       np.asarray(w["toe"], float) + h0))
            for w in model["regrade"]["walls"]]


class ModelGround:
    """A model's ground layer, read like a Ground: sample(e, n) on the site's grid, NaN off it, and its triangles as
    `surface`. Each corner is about `origin` (the solids' origin) in single precision, as the viewer's mesh holds it."""

    def __init__(self, model, origin):
        layer = next(layer for layer in model["layers"] if layer["id"] == "ground")
        e0, n0, h0 = (float(v) for v in model["origin"])
        ox, oy = float(origin[0]), float(origin[1])
        p = np.asarray(layer["positions"], float).reshape(-1, 3)
        x = np.float32((p[:, 0] + e0) - ox).astype(float) + ox
        y = np.float32((n0 - p[:, 2]) - oy).astype(float) + oy
        z = np.float32(p[:, 1] + h0).astype(float)
        self.surface = SimpleNamespace(vertices=np.column_stack([x, y, z]),
                                       faces=np.asarray(layer["indices"], np.int64).reshape(-1, 3))
        self._triangles = Surface(self.surface.vertices, self.surface.faces)

    def sample(self, e, n):
        return self._triangles.z(e, n)


class SketchDesign(scheme.Design):
    """A sketch's option worked out as the preview works it out: as Design does, but with its terraces' free edges
    worked out exactly (solids.free_pieces) and its batters from them."""

    @functools.cached_property
    def free_pieces(self):
        return solids.free_pieces(self)

    def batters(self, e, n):
        return solids.batter_levels(self, self.free_pieces, e, n)


def sketch_solids(model, items, segments=PREVIEW_SEGMENTS, conflict_step=solids.CONFLICT_STEP_M):
    """A sketch (the viewer's items, their corners `en` on the site's grid) as the preview works it out, on what
    `model` (a model.json) gives the viewer: (its solids.Solids, its SketchDesign, its walls levelled), or None for a
    sketch with no wall or area in it."""
    rg = model["regrade"]
    old = old_walls(model)
    opt = sketch.sketched(items, rg["defaults"]["upstand_m"], [i for i, _ in old])
    if not opt["walls"] and not opt["terraces"]:
        return None
    garden = regrade_area(model)
    origin = np.floor(np.asarray(garden.bounds[:2], float))
    d = SketchDesign(opt, rg["defaults"], ModelGround(model, origin), garden, None)
    walls = d.wall_levels()
    s = solids.option_solids(d, walls, segments=segments, old=old, within=solids.option_window(d, walls),
                             conflict_step=conflict_step)
    return s, d, walls
