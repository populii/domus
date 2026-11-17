"""The ground along a line, and either side of it: for reading levels off the model without the viewer.

    domus prep profile "8.15,0.4 8.15,3.0" [--offsets -0.5 0 0.5] [--step 0.5] [--grid]

The line's points are in the wall options' frame (s along its reference boundary, o in from it: options/_site.toml),
or with --grid on the site's grid (east, north). Stations run along the line every --step metres and at each of its
points; at each, the ground is read at each offset across the line, to its left (negative) and right (positive)
looking along it. The ground is the model's existing ground: the ground model with the site's ground fixes. Prints a
table, and --csv FILE writes it too.
"""

import argparse
import csv
import itertools

import numpy as np

from ..errors import DomusError
from ..model import terrain


def stations(points, step):
    """(distance along, point, direction) at every `step` metres along the line through `points`, and at each point."""
    out = []
    run = 0.0
    for a, b in itertools.pairwise(points):
        d = b - a
        length = float(np.hypot(*d))
        if length < 1e-9:
            continue
        unit = d / length
        for t in [*np.arange(0.0, length, step), length] if step > 0 else [0.0, length]:
            out.append((run + t, a + unit * t, unit))
        run += length
    keep, last = [], None
    for s, p, u in out:                       # a point shared by two legs once
        if last is None or abs(s - last) > 1e-9:
            keep.append((s, p, u))
        last = s
    return keep


def profile(points, offsets=(0.0,), step=0.5, grid=False):
    """The ground along a line: [(distance along, station point, {offset: height})]. `points` are (s, o) in the
    options' frame, or (east, north) on the site's grid with `grid`."""
    pts = np.asarray(points, float).reshape(-1, 2)
    if len(pts) < 2:
        raise DomusError("A profile needs a line: two points or more")
    if grid:
        to_en, turn = (lambda p: np.asarray(p, float)), 1.0                   # east, north: right is clockwise
    else:
        from ..options import design

        fr = design.frame()
        to_en = lambda p: fr.en(np.asarray(p, float))
        # o grows into the lot, which lies on the reference boundary's right looking along s (its left with side = "left")
        turn = -1.0 if design.settings()["frame"].get("side", "right") == "right" else 1.0
    g = terrain.ground()
    out = []
    for s, p, u in stations(pts, step):
        right = turn * np.array([u[1], -u[0]])
        at = {off: to_en(p + right * off) for off in offsets}
        en = np.array(list(at.values()), float).reshape(-1, 2)
        z = g.sample(en[:, 0], en[:, 1])
        out.append((s, p, dict(zip(offsets, np.atleast_1d(z).astype(float), strict=True))))
    return out


def main():
    ap = argparse.ArgumentParser(prog="domus prep profile", description=__doc__.split("\n\n")[0])
    ap.add_argument("points", help='the line\'s points, "s,o s,o ..." (or "east,north ..." with --grid)')
    ap.add_argument("--offsets", type=float, nargs="+", default=[0.0], help="metres across the line (left negative)")
    ap.add_argument("--step", type=float, default=0.5, help="metres between stations (0: the line's points only)")
    ap.add_argument("--grid", action="store_true", help="the points are on the site's grid, not the options' frame")
    ap.add_argument("--csv", help="also write the table to this file")
    args = ap.parse_args()
    try:
        points = [[float(v) for v in p.split(",")] for p in args.points.split()]
    except ValueError as e:
        raise DomusError(f'Give the line\'s points as "s,o s,o ...": {e}') from e
    rows = profile(points, args.offsets, args.step, args.grid)
    names = ("E", "N") if args.grid else ("s", "o")
    head = ["along", names[0], names[1], *(f"{o:+g}" for o in args.offsets)]
    print("  ".join(f"{h:>8}" for h in head))
    table = []
    for s, p, z in rows:
        row = [s, p[0], p[1], *(z[o] for o in args.offsets)]
        table.append(row)
        print("  ".join(f"{v:8.2f}" for v in row))
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(head)
            w.writerows([[round(v, 3) for v in row] for row in table])
    return 0
