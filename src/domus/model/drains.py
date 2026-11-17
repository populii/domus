"""Drains: the levels along each one, worked out once for the pipes the viewer draws and the options' checks.

A drain is a LineString in data/site/services.geojson. Its levels come from Points there naming it in `line`, each
with an `invert_m`. The points from one document (their `source`) give that document's levels over their own
stretch of the drain; where documents overlap, the higher level is used, the shallower pipe with less cover over it.
Between documents the levels run straight. Past the first and last level the pipe keeps that level, or that level's
depth below the ground where the ground falls away, so a drain running on downhill doesn't come out of it. A drain
with no levels takes its `[services] assumed_depth_m` below the ground, or has no levels at all.
"""

import numpy as np
from shapely.geometry import LineString, Point

from ..geo.mesh import resample_line


def level_points(features):
    """The level points of services.geojson's `features`, by the drain they name: {drain id: [(xy, invert, source)]}."""
    out = {}
    for f in features:
        p = f["properties"]
        if f["geometry"]["type"] == "Point" and "invert_m" in p and p.get("line"):
            out.setdefault(p["line"], []).append((f["geometry"]["coordinates"][:2], float(p["invert_m"]), p.get("source", "")))
    return out


def levels(line, points, ground, assumed_depth=None, spacing=0.5):
    """A drain's invert along `line` (its coordinates), from its level `points` (level_points) and ground(e, n):
    (points along it, distances along it, invert, the known levels as (distance, invert)), with no known levels when
    the invert comes from `assumed_depth`. None if it has neither."""
    pts, chain = resample_line(np.asarray(line, float)[:, :2], spacing)
    g = ground(pts[:, 0], pts[:, 1])
    if not points:
        return None if assumed_depth is None else (pts, chain, g - assumed_depth, [])
    drawn = LineString(np.asarray(line, float)[:, :2])
    sources = {}
    for xy, z, source in points:
        sources.setdefault(source, []).append((drawn.project(Point(xy)), z))
    known = [sorted(k) for k in sources.values()]
    if len(known) == 1:
        invert = np.interp(chain, *zip(*known[0]))
    else:
        # On the samples and the level points themselves: the higher document where they overlap, straight between.
        grid = np.union1d(chain, [c for k in known for c, _ in k])
        stack = np.array([np.where((grid >= k[0][0]) & (grid <= k[-1][0]), np.interp(grid, *zip(*k)), np.nan) for k in known])
        covered = ~np.isnan(stack).all(axis=0)
        invert = np.interp(chain, grid[covered], np.nanmax(stack[:, covered], axis=0))
    first, last = min(k[0] for k in known), max(k[-1] for k in known)
    for end, side in ((first, chain < first[0]), (last, chain > last[0])):
        invert[side] = np.minimum(end[1], g[side] - (np.interp(end[0], chain, g) - end[1]))
    return pts, chain, invert, sorted(c for k in known for c in k)
