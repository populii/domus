"""The weatherboards' spacing on a rectified photo of a wall: how far apart their lap lines are up the wall, and the
lap lines' heights. A check of [house] board_m, and of where the boards are counted from.

    domus prep boards [FACE ...] [--along U0 U1] [--band 0.5] [--heights Z0 Z1] [--spacing 0.08 0.25]
                      [--step 0.0002] [--above HEIGHT]

A FACE is a wall's rectified photo in data/house/faces.json, as domus prep house-textures writes it: every face there
unless some are named. The face is cut along its length into bands --band metres wide (0 for one band), over the part
--along gives in metres along the face (all of it unless given). Each band's rows are averaged across it into a
profile up the wall, over the rows the photo covers right across the band, within --heights (the photo's whole height
unless given).

The profile less its running mean over the shortest spacing tried keeps the shadow under each lap, a dark line, and
little else. Each spacing from --spacing's first to its last, --step apart, is tried as a comb of lines up the wall:
the profile is averaged at twelve phases of the spacing (fewer if the spacing spans fewer pixel rows, so that no phase
is narrower than a row), and the shadows show as a dip at one of them. A spacing's contrast is how far its phases'
averages differ, in grey levels. A comb twice the boards' spacing fits nearly as well as theirs, so the boards'
spacing is the best comb's unless a shorter one's contrast is at least 85% of it: then the shortest such, at the top
of its peak, unless the best is twice or three times that and shows a single dark line a cycle. The lap lines are where
the profile, less its running mean over that spacing and folded at it, is darkest: that phase, placed between its
neighbours by a parabola, gives their heights.

Printed for each band: the rows it read and their heights, the spacing, its contrast (the higher, the clearer the lap
lines) and the lap lines' heights, as the lowest in the band plus whole spacings; with --above, the first lap line at or
above that height too, such as the top of a plastered base the boards are counted up from. Then, with more than one
band, the face's spacing: the median of its bands'. A band needs rows over at least four of the longest spacing tried,
and a spacing found at either end of the range tried is marked, as the boards' may lie beyond it. A band across a
window or a door reads less clearly, or wrongly: its contrast is lower than its neighbours'.
"""

import argparse
import json

import numpy as np
from PIL import Image
from scipy.ndimage import uniform_filter1d

from ..errors import DomusError

PHASES = 12             # the most phases a spacing is averaged at: each then about as wide as a lap's shadow
SHARE = 0.85            # a spacing whose contrast is this share of the best's is taken over a longer one
MIN_SPACINGS = 4        # a band reads the boards from rows over at least this many of the longest spacing tried


def house_dir(site):
    """The site's rectified wall photos, data/house/."""
    return site.path / "data" / "house"


def faces(site):
    """data/house/faces.json: each rectified wall photo's record, by face."""
    path = house_dir(site) / "faces.json"
    if not path.exists():
        raise DomusError(f"There's no {path}: domus prep house-textures writes it, with each wall's rectified photo")
    return json.loads(path.read_text(encoding="utf-8"))


def read_face(site, name, index=None):
    """A face's photo in grey (rows down from its top), where the photo covers the face (True), and its record."""
    index = faces(site) if index is None else index
    if name not in index:
        raise DomusError(f"data/house/faces.json has no face {name!r}: it has {', '.join(sorted(index)) or 'none'}")
    rec = index[name]
    grey = np.asarray(Image.open(house_dir(site) / rec["file"]).convert("L"), float)
    covered = np.asarray(Image.open(house_dir(site) / rec["mask"]).convert("L")) > 127 if rec.get("mask") else \
        np.ones(grey.shape, bool)
    return grey, covered, rec


def bands(u0, u1, width):
    """[(from, to)]: bands `width` metres wide from u0 towards u1, as many as fit; one band from u0 to u1 if `width` is 0
    or wider than that."""
    n = int(np.floor((u1 - u0) / width + 1e-9)) if width > 0 else 0
    return [(u0 + k * width, u0 + (k + 1) * width) for k in range(n)] if n else [(u0, u1)]


def profile(grey, covered, rec, u0, u1, heights=None):
    """A band's profile up the wall, from u0 to u1 metres along the face: the heights of the photo's rows, the mean
    grey of each across the band, and which rows to read (those the photo covers right across the band, between
    `heights` if given)."""
    ppm, start = rec["ppm"], rec.get("u0", 0.0)
    c0, c1 = max(int(round((u0 - start) * ppm)), 0), min(int(round((u1 - start) * ppm)), grey.shape[1])
    z = rec["z1"] - (np.arange(grey.shape[0]) + 0.5) / ppm
    if c1 <= c0:
        return z, np.zeros(len(z)), np.zeros(len(z), bool)
    read = covered[:, c0:c1].all(axis=1)
    if heights is not None:
        read &= (z >= min(heights)) & (z <= max(heights))
    return z, grey[:, c0:c1].mean(axis=1), read


def high_pass(values, read, rows):
    """`values` less their running mean over `rows` rows, of the rows `read` only: what changes faster than that."""
    w = read.astype(float)
    mean = uniform_filter1d(values * w, rows, mode="constant") / np.maximum(uniform_filter1d(w, rows, mode="constant"), 1e-9)
    return values - mean


def folded(z, values, spacing, phases=PHASES):
    """A profile's means at `phases` phases of `spacing` (`values` at heights `z`), and which phases have rows."""
    k = np.minimum((np.mod(z, spacing) / spacing * phases).astype(int), phases - 1)
    n = np.bincount(k, minlength=phases)
    return np.bincount(k, values, minlength=phases) / np.maximum(n, 1), n > 0


def comb(z, values, spacing, phases=PHASES):
    """How a comb of lines `spacing` apart fits a high-passed profile (`values` at heights `z`): (its contrast, the
    largest less the smallest of the profile's means at `phases` phases of the spacing; the height of the darkest
    phase, less whole spacings, placed between its neighbours by a parabola through the three)."""
    mean, seen = folded(z, values, spacing, phases)
    i = int(np.argmin(np.where(seen, mean, np.inf)))
    a, b, c = mean[(i - 1) % phases], mean[i], mean[(i + 1) % phases]
    bend = a - 2 * b + c
    shift = 0.5 * (a - c) / bend if bend > 0 and seen[(i - 1) % phases] and seen[(i + 1) % phases] else 0.0
    return float(mean[seen].max() - mean[seen].min()), float((i + 0.5 + shift) / phases * spacing % spacing)


def dips(z, values, spacing, k, phases=PHASES):
    """Whether a profile folded at `spacing` has `k` dark lines, evenly spaced: each at least half as deep as the
    darkest, below the phases' median. A comb k times the boards' spacing shows k of their lap lines in each cycle."""
    mean, seen = folded(z, values, spacing, phases)
    mean = np.where(seen, mean, np.median(mean[seen]))
    i, base = int(np.argmin(mean)), np.median(mean)
    near = lambda j: min(mean[(j + d) % phases] for d in (-1, 0, 1))
    return all(base - near(i + round(m * phases / k)) >= 0.5 * (base - mean[i]) for m in range(1, k))


def phases_of(spacing, ppm):
    """How many phases a spacing is averaged at, `ppm` pixel rows a metre: PHASES, or fewer so that none is narrower
    than a row, which would let half the boards' spacing fit as well as theirs."""
    return max(min(PHASES, int(spacing * ppm)), 3)


def best_spacing(z, values, spacings, ppm):
    """The boards' spacing among `spacings` for a high-passed profile (`values` at heights `z`, `ppm` rows a metre),
    and its comb's contrast. The best comb's spacing, unless a shorter one's contrast is at least SHARE of the best's:
    then the shortest such, at the top of its peak, since a comb twice the boards' spacing fits nearly as well as
    theirs. But if the best's spacing is twice or three times that one's and the profile folded at the best shows a
    single lap line, the best stands."""
    phases = [phases_of(s, ppm) for s in spacings]
    contrast = np.array([comb(z, values, s, n)[0] for s, n in zip(spacings, phases, strict=True)])
    best = int(np.argmax(contrast))
    first = int(np.argmax(contrast >= SHARE * contrast[best]))
    peak = np.flatnonzero((spacings >= spacings[first]) & (spacings <= 1.05 * spacings[first]))
    i = int(peak[np.argmax(contrast[peak])])
    k = round(spacings[best] / spacings[i])
    if k in (2, 3) and abs(spacings[best] / spacings[i] - k) < 0.03 * k and \
            not dips(z, values, spacings[best], k, phases[best]):
        i = best
    return float(spacings[i]), float(contrast[i])


def measure_photo(grey, covered, rec, along=None, band=0.5, heights=None, spacing=(0.08, 0.25), step=0.0002):
    """The boards' spacing in each band of a rectified wall photo (`grey`, `covered` and its record in faces.json):
    [{"u": (from, to), "rows": rows read, and if there were enough, "z": (lowest, highest) row read, "spacing",
    "lap" (the lowest lap line's height in the band), "contrast" and "edge" (the spacing is at an end of those tried)}]."""
    lo, hi = sorted(float(s) for s in spacing)
    if lo <= 0 or step <= 0 or band < 0:
        raise DomusError("The spacings tried, the step between them and the bands' width must be more than 0")
    ppm, start = rec["ppm"], rec.get("u0", 0.0)
    u_from, u_to = sorted(along) if along else (start, start + grey.shape[1] / ppm)
    tried = np.arange(lo, hi + step / 2, step)
    rows = lambda metres: max(int(round(metres * ppm)) // 2 * 2 + 1, 3)       # an odd number of rows, three or more
    out = []
    for u0, u1 in bands(u_from, u_to, band):
        z, values, read = profile(grey, covered, rec, u0, u1, heights)
        row = {"u": (u0, u1), "rows": int(read.sum())}
        if read.sum() >= MIN_SPACINGS * hi * ppm:
            zr = z[read]
            s, contrast = best_spacing(zr, high_pass(values, read, rows(lo))[read], tried, ppm)
            phase = comb(zr, high_pass(values, read, rows(s))[read], s, phases_of(s, ppm))[1]
            lap = phase + np.ceil((zr.min() - phase) / s) * s
            row.update(z=(float(zr.min()), float(zr.max())), spacing=s, lap=float(lap), contrast=contrast,
                       edge=bool(s < lo + step / 2 or s > tried[-1] - step / 2))
        out.append(row)
    return out


def measure(site, name, along=None, band=0.5, heights=None, spacing=(0.08, 0.25), step=0.0002, index=None):
    """measure_photo for the face `name` in data/house/faces.json."""
    return measure_photo(*read_face(site, name, index), along, band, heights, spacing, step)


def band_line(row, above=None):
    """A band's reading as `domus prep boards` prints it."""
    head = f"  u {row['u'][0]:.2f}-{row['u'][1]:.2f}: {row['rows']} rows"
    if "spacing" not in row:
        return head + ", too few to read"
    s, lap = row["spacing"], row["lap"]
    line = (f"{head} from {row['z'][0]:.2f} to {row['z'][1]:.2f}: spacing {s * 1000:.1f} mm"
            f"{' (at an end of the range tried)' if row['edge'] else ''}, contrast {row['contrast']:.1f}; "
            f"lap lines at {lap:.3f} + n x {s:.4f}")
    if above is not None:
        line += f"; the first at or above {above:.3f}: {lap + np.ceil((above - lap) / s - 1e-9) * s:.3f}"
    return line


def main(site):
    """`domus prep boards`."""
    ap = argparse.ArgumentParser(prog="domus prep boards", description=__doc__.split("\n\n")[0])
    ap.add_argument("faces", nargs="*", help="faces in data/house/faces.json (all of them unless named)")
    ap.add_argument("--along", type=float, nargs=2, metavar=("U0", "U1"),
                    help="the part of the face to read, in metres along it (all of it unless given)")
    ap.add_argument("--band", type=float, default=0.5, metavar="WIDTH",
                    help="the bands' width along the face, in metres (0: one band)")
    ap.add_argument("--heights", type=float, nargs=2, metavar=("Z0", "Z1"),
                    help="the heights to read between (the photo's whole height unless given)")
    ap.add_argument("--spacing", type=float, nargs=2, default=[0.08, 0.25], metavar=("MIN", "MAX"),
                    help="the range of spacings to try, in metres")
    ap.add_argument("--step", type=float, default=0.0002, help="metres between the spacings tried")
    ap.add_argument("--above", type=float, metavar="HEIGHT", help="also give the first lap line at or above this height")
    args = ap.parse_args()
    index = faces(site)
    for name in args.faces or sorted(index):
        rows = measure(site, name, args.along, args.band, args.heights, args.spacing, args.step, index)
        print(f"{name}: {len(rows)} band{'s' if len(rows) != 1 else ''} from {rows[0]['u'][0]:.2f} to "
              f"{rows[-1]['u'][1]:.2f} m along it")
        for row in rows:
            print(band_line(row, args.above))
        found = [r["spacing"] for r in rows if "spacing" in r]
        if len(found) > 1:
            print(f"{name}: spacing {np.median(found) * 1000:.1f} mm, the median of {len(found)} bands "
                  f"({min(found) * 1000:.1f} to {max(found) * 1000:.1f})")
        elif not found:
            print(f"{name}: no band has enough rows to read")
    return 0
