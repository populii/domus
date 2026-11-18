"""Place a video reconstruction on the site grid.

    domus video place VIDEO
    domus video place VIDEO --via VIDEO

The control file data/video/<tag>-control.json says how. Features are named by the marks in
a picks file (data/video/picks/, pixel positions in frames) or given inline ("obs"), and
triangulated with the reconstructed cameras. Site heights left null are read off the
LiDAR ground. Then, by what the control holds:
- "anchors", three or more: a similarity fitted to them;
- "anchors", two: their distance fixes the scale; the turn about the line through them is
  searched (1 degree steps) for the best fit to the ground;
- "anchor" and "vertical" (a corner's foot and a point straight above it), or "anchor" and
  "up" (the foot and top of any vertical edge): heading and scale are searched about the
  anchor (search_pose);
- "points": a least-squares fit to whatever coordinates are known, then held like anchors.
A point marked "check" is only reported, never held. "paved" is a polygon (site E, N) of hard
ground the LiDAR sees clearly, such as the driveway: the lowest points in it always count as
ground in the refinement, however far off they start.
"lengths" adds known distances between two marks (a slot drain's length, a door), and "on_line"
marks known to lie on a wall line in plan; both, and any further control points, count in
the search and are held in the refinement by their sigma_m.
"plumb" adds the frames' own vertical, so that control on a short or near-straight line (two
anchors, heights along a driveway) cannot leave the placement turned about that line. In each
frame the straight near-vertical edges (house corners, door and window frames, posts) meet at
a vanishing point; the camera's reconstructed rotation carries it into the reconstruction
(frame_ups, the same measurement as domus prep check plumb). "frames" says which frames:
"auto" (every registered frame) or a list of frame names, {"frame": name, "roi": [x0, y0,
x1, y1]} (only edges whose midpoint lies in that pixel box, to leave out foliage) and
{"from": s, "to": s} time ranges in seconds (with "clip" for an extra clip's frames, and
"roi"). False verticals are guarded against as the check does: a frame counts only with five
or more edges of 60 px or longer, 500 px of them in all and an rms of 0.5 degrees about their
vanishing point; frames more than max(2, twice the median) degrees off the mean are dropped,
and if the rest still scatter by more than "max_spread_deg" (1 unless given) the tool stops,
since strap-like leaves and a drain grate's slots pass for verticals and a bent reconstruction
has no single vertical: name the frames (and boxes) that see true verticals. The start is
turned level about the control (or the anchor) and the refinement holds the vertical by
"sigma_deg" (0.5 unless given); with "check": true it is only reported. A frame fixes its
vertical best sideways (along the camera's x axis); toward or away from the camera it rests on
the edges' slight convergence and the lens model. Where that part is known to be weaker, as
for a distant house seen by frames that all look one way, "toward_away_weight" (below 1)
weighs it down in combining the frames (plumb_consensus) and the hold then loosens across the
direction the frames fix best, leaving that tilt to the other control. The tilt of the start
and of the result against the frames' vertical go into the -georef.json.
Refinement: an iterative closest-surface fit of all reconstructed points to the house walls
(vertical planes, from the house's source: domus.model.house_source) and the ground, re-labelling points to the
nearest surface each round; control is held (a single anchor exactly in plan and the other
points by their stated uncertainty, sigma_m, 3 cm unless given; unknown coordinates are free),
and the cameras' median height above the ground is kept between 1 and 2 m. The ground is the
LiDAR's only outside the areas of data/site/ground-fixes.geojson, where it is known wrong or
out of date (banks under trees, ground regraded since): points and cameras there
take no part in the ground fit or the height check.

With --via, the link to an already placed model (domus video link) composed with that
model's placement gives the start instead of a control file; a control file of the model's
own, if there is one, then only adds its "plumb".

Writes data/video/<tag>-georef.json (transform and residuals) and data/video/<tag>-site.npz
(points on the site's grid and heights, with colours, and the cameras).
"""

import json
import math

import cv2
import numpy as np
import shapely
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from ..errors import DomusError
from ..model import house_source, terrain
from ..project import rasters
from . import sfm
from .sfm_georef import apply, fit_similarity, horn_initial, load, triangulate_pixels


def video_dir(site):
    """The site's video work, data/video/."""
    return site.path / "data" / "video"
def frames_dir(site):
    """The clips' frames, sources/raw/frames6/."""
    return site.path / "sources" / "raw" / "frames6"


def house_planes(site):
    """The house's walls as planes, each (a corner, the unit vector along the wall, its length), from the house's
    walls as its source gives them (domus.model.house_source); a side under a metre long is left out."""
    walls = house_source.house(site).walls
    if walls is None:
        raise DomusError("Placing a video fits it to the house's walls, and this site has no house: house-walls in "
                         "data/site/house.geojson, or its IFC file")
    ring = np.asarray(walls["geometry"]["coordinates"][0])[:-1]
    planes = []
    for a, b in zip(ring, np.roll(ring, -1, axis=0)):
        d = b - a
        length = np.hypot(*d)
        if length < 1.0:
            continue
        u = d / length
        planes.append((a, u, length))
    return planes


def classify(site_pts, planes, dem_z, eaves, tol):
    """Label each point: index of the house wall plane it lies on, -1 ground, -2 neither."""
    lab = np.full(len(site_pts), -2)
    best = np.full(len(site_pts), np.inf)
    for k, (a, u, length) in enumerate(planes):
        rel = site_pts[:, :2] - a
        along = rel @ u
        across = rel @ np.array([-u[1], u[0]])
        ok = (along > 0.2) & (along < length - 0.2) & (np.abs(across) < tol) & \
             (site_pts[:, 2] > dem_z - 0.3) & (site_pts[:, 2] < eaves)
        better = ok & (np.abs(across) < best)
        lab[better] = k
        best[better] = np.abs(across[better])
    ground = (np.abs(site_pts[:, 2] - dem_z) < tol) & (lab == -2)
    lab[ground] = -1
    return lab


class Grid:
    """The DEM resampled once onto a site-aligned 0.25 m grid, for fast bilinear lookups."""

    def __init__(self, dem, extent, step=0.25):
        self.e0, self.n0, e1, n1 = extent
        self.step = step
        E, N = np.meshgrid(np.arange(self.e0, e1 + step, step), np.arange(self.n0, n1 + step, step))
        self.z = dem.sample(E, N)

    def __call__(self, e, n):
        c = (np.asarray(e) - self.e0) / self.step
        r = (np.asarray(n) - self.n0) / self.step
        ok = (c >= 0) & (r >= 0) & (c < self.z.shape[1] - 1) & (r < self.z.shape[0] - 1)
        c, r = np.where(ok, c, 0), np.where(ok, r, 0)
        c0, r0 = np.floor(c).astype(int), np.floor(r).astype(int)
        fc, fr = c - c0, r - r0
        z = (self.z[r0, c0] * (1 - fc) * (1 - fr) + self.z[r0, c0 + 1] * fc * (1 - fr)
             + self.z[r0 + 1, c0] * (1 - fc) * fr + self.z[r0 + 1, c0 + 1] * fc * fr)
        return np.where(ok, z, np.nan)


def line_offsets(params, on_line):
    """Distance in plan from each on-line control point to its line, metres."""
    out = []
    for local, a, b, _ in on_line:
        x = apply(params, local[None])[0]
        u = (b - a) / np.linalg.norm(b - a)
        out.append(-(x[0] - a[0]) * u[1] + (x[1] - a[1]) * u[0])
    return np.array(out)


def score(params, q, qc, planes, ground, eaves, on_line=(), known=()):
    """How well a placement fits: points on the ground or on a house wall, cameras at a
    hand-held height above the ground, less a penalty for points well below the ground and for
    control points off the wall line they belong on."""
    x = apply(params, q)
    dz = x[:, 2] - ground(x[:, 0], x[:, 1])
    on_wall = np.zeros(len(x), bool)
    for a, u, length in planes:
        along = (x[:, 0] - a[0]) * u[0] + (x[:, 1] - a[1]) * u[1]
        across = -(x[:, 0] - a[0]) * u[1] + (x[:, 1] - a[1]) * u[0]
        on_wall |= (np.abs(across) < 0.08) & (along > 0.2) & (along < length - 0.2) & (x[:, 2] < eaves)
    c = apply(params, qc)
    hc = c[:, 2] - ground(c[:, 0], c[:, 1])
    flat = np.abs(dz) < 0.1
    off = np.sum(np.minimum(np.abs(line_offsets(params, on_line)), 2.0)) if len(on_line) else 0.0
    for local, site, _ in known:      # control points with a known plan position
        if np.all(np.isfinite(site[:2])):
            off += min(float(np.hypot(*(apply(params, local[None])[0, :2] - site[:2]))), 2.0)
    return (np.sum(flat) + np.sum(on_wall & ~flat) - 0.5 * np.sum(dz < -0.3)
            + 40 * np.sum((hc > 0.8) & (hc < 2.2)) - 0.5 * len(q) * off)


def search_pose(pts, cams, anchor_local, up, anchor_site, planes, ground, eaves, on_line=(), known=()):
    """Placement from one anchor and the vertical (`up`, a vector in the reconstruction):
    every heading (2 degrees) and scale (2 %) is scored; returns similarity parameters."""
    r0 = Rotation.align_vectors([[0, 0, 1]], [up / np.linalg.norm(up)])[0]
    rng = np.random.default_rng(0)
    sub = pts[rng.choice(len(pts), min(8000, len(pts)), replace=False)]
    best = (-np.inf, None)
    for s in np.exp(np.arange(np.log(0.05), np.log(2.0), 0.02)):
        for yaw in np.radians(np.arange(0, 360, 2.0)):
            rot = Rotation.from_euler("z", yaw) * r0
            p = np.r_[np.log(s), rot.as_rotvec(), np.asarray(anchor_site) - s * rot.apply(anchor_local)]
            sc = score(p, sub, cams, planes, ground, eaves, on_line, known)
            if sc > best[0]:
                best = (sc, p)
    print(f"search: scale {np.exp(best[1][0]):.4f}, score {best[0]:.0f}")
    return best[1]


def search_axis(pts, cams, local, site, planes, ground, eaves, on_line=()):
    """Placement from two anchors: their distance fixes the scale, and the turn about the
    line through them is searched in 1 degree steps."""
    s = np.linalg.norm(site[1] - site[0]) / np.linalg.norm(local[1] - local[0])
    r_align = Rotation.align_vectors([site[1] - site[0]], [local[1] - local[0]])[0]
    axis = (site[1] - site[0]) / np.linalg.norm(site[1] - site[0])
    rng = np.random.default_rng(0)
    sub = pts[rng.choice(len(pts), min(8000, len(pts)), replace=False)]
    best = (-np.inf, None)
    for ang in np.radians(np.arange(0, 360, 1.0)):
        rot = Rotation.from_rotvec(axis * ang) * r_align
        p = np.r_[np.log(s), rot.as_rotvec(), site[0] - s * rot.apply(local[0])]
        sc = score(p, sub, cams, planes, ground, eaves, on_line)
        if sc > best[0]:
            best = (sc, p)
    print(f"two-anchor search: scale {s:.4f}, score {best[0]:.0f}")
    return best[1]


# The frames' vertical (tools/check_model.py plumb measures tilts with the same code)

_LSD = cv2.createLineSegmentDetector(0)


def vertical_up(intr, img, min_len=60, thresh_deg=0.6, iters=3000, seed=0, roi=None):
    """Up in camera coordinates (y down) from the frame's near-vertical straight edges, or None.

    Segments (OpenCV's LSD, min_len px or longer, within 30 degrees of the image's vertical; with
    roi [x0, y0, x1, y1] only those whose midpoint lies inside) are undistorted with the model's
    k1. Each with the camera centre spans a plane, and a vertical direction lies in the plane of
    every vertical segment: RANSAC over pairs finds the direction within 37 degrees of the image's
    vertical that the most segment length agrees with (within thresh_deg), refined over its
    inliers. A frame counts only with 5 or more inliers, 500 px of them, and an rms of 0.5
    degrees."""
    lines = _LSD.detect(img)[0]
    if lines is None:
        return None
    s = np.asarray(lines, float).reshape(-1, 4)
    s = s[np.hypot(s[:, 2] - s[:, 0], s[:, 3] - s[:, 1]) >= min_len]
    if roi is not None:
        mx, my = (s[:, 0] + s[:, 2]) / 2, (s[:, 1] + s[:, 3]) / 2
        s = s[(mx >= roi[0]) & (mx <= roi[2]) & (my >= roi[1]) & (my <= roi[3])]
    if len(s) < 4:
        return None
    f, k1, cx, cy = intr
    shim = sfm.Reconstruction([], f=f, k1=k1, cx=cx, cy=cy)

    def rays(uv):
        u, v = sfm.undistort(shim, uv.T)
        return np.column_stack([u, v, np.ones_like(u)])
    nrm = np.cross(rays(s[:, :2]), rays(s[:, 2:]))
    nrm /= np.linalg.norm(nrm, axis=1)[:, None]
    w = np.hypot(s[:, 2] - s[:, 0], s[:, 3] - s[:, 1])
    cand = np.flatnonzero(np.degrees(np.arctan2(np.abs(s[:, 2] - s[:, 0]), np.abs(s[:, 3] - s[:, 1]))) < 30)
    if len(cand) < 4:
        return None
    rng = np.random.default_rng(seed)
    st = math.sin(math.radians(thresh_deg))
    best = (0.0, None)
    for _ in range(iters):
        i, j = rng.choice(cand, 2, replace=False)
        d = np.cross(nrm[i], nrm[j])
        if np.linalg.norm(d) < 1e-6:
            continue
        d /= np.linalg.norm(d)
        if abs(d[1]) < 0.8:
            continue
        inl = cand[np.abs(nrm[cand] @ d) < st]
        if w[inl].sum() > best[0]:
            best = (w[inl].sum(), inl)
    if best[1] is None or len(best[1]) < 5 or best[0] < 500:
        return None
    inl = best[1]
    for _ in range(3):
        d = np.linalg.eigh((nrm[inl] * w[inl, None]).T @ nrm[inl])[1][:, 0]
        inl = cand[np.abs(nrm[cand] @ d) < st]
    rms = float(np.sqrt(np.mean(np.degrees(np.arcsin(np.abs(nrm[inl] @ d))) ** 2)))
    if rms > 0.5:
        return None
    return -d if d[1] > 0 else d


def frame_path(site, tag, name):
    """A frame of the reconstruction: 'tNNN.NNN' of its own clip, or 'clip/tNNN.NNN'."""
    clip, stem = name.split("/", 1) if "/" in name else (tag.split("-")[0], name)
    return frames_dir(site) / clip / f"{stem}.jpg"


def frame_ups(rec, frames, image):
    """[(frame, up in reconstruction coordinates)] for the frames (name, roi) that show a vertical
    vanishing point; image(name) gives the frame in grey (None if missing)."""
    out = []
    for name, roi in frames:
        i = rec["names"].index(name)
        if not np.all(np.isfinite(rec["rvecs"][i])):
            continue
        img = image(name)
        if img is None:
            continue
        u = vertical_up(rec["intr"], img, roi=roi)
        if u is not None:
            out.append((name, Rotation.from_rotvec(rec["rvecs"][i]).as_matrix().T @ u))
    return out


def consensus_up(ups):
    """Robust mean of unit vectors: frames more than max(2, twice the median) degrees off the mean
    are dropped, five times over. Returns the mean, the frames kept and each frame's offset."""
    u = np.asarray(ups)
    m = u.mean(0) / np.linalg.norm(u.mean(0))
    for _ in range(5):
        off = np.degrees(np.arccos(np.clip(u @ m, -1, 1)))
        keep = off < max(2.0, 2 * np.median(off))
        m = u[keep].mean(0) / np.linalg.norm(u[keep].mean(0))
    off = np.degrees(np.arccos(np.clip(u @ m, -1, 1)))
    return m, keep, off


def frame_axes(rec, names):
    """Each frame's camera x axis (the image's right) and viewing direction, in reconstruction
    coordinates."""
    out = []
    for name in names:
        r = Rotation.from_rotvec(rec["rvecs"][rec["names"].index(name)]).as_matrix()
        out.append((r[0], r[2]))
    return out


def _perp_basis(m):
    a = np.array([1.0, 0, 0]) if abs(m[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(m, a)
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(m, e1)


def plumb_consensus(ups, axes, alpha):
    """The reconstruction's vertical from the frames' verticals (unit vectors) and their camera
    axes (frame_axes), each frame's part toward or away from the camera weighed by alpha against
    its part sideways (along the camera's x axis). Starts from consensus_up (which drops gross
    outliers, such as a grate's slots), then drops frames more than max(2, twice the median)
    degrees off sideways or max(6, three times the median) toward/away, ten times over.
    Returns {"up", "keep", "side_deg", "along_deg", "alpha", "info"}: offsets per frame, and info
    (3x3) the weight the frames give a lean in each direction."""
    u = np.asarray(ups, float)
    x, t = [], []
    for ui, (xi, fi) in zip(u, axes):
        xi, fi = xi - (xi @ ui) * ui, fi - (fi @ ui) * ui
        x.append(xi / np.linalg.norm(xi))
        t.append(fi / np.linalg.norm(fi))
    x, t = np.array(x), np.array(t)
    m, keep, _ = consensus_up(u)
    for _ in range(10):
        e1, e2 = _perp_basis(m)
        w = np.sqrt(alpha)
        a = np.vstack([np.column_stack([x[keep] @ e1, x[keep] @ e2]), w * np.column_stack([t[keep] @ e1, t[keep] @ e2])])
        b = np.r_[-np.einsum("ij,ij->i", x[keep], m - u[keep]), -w * np.einsum("ij,ij->i", t[keep], m - u[keep])]
        d = np.linalg.lstsq(a, b, rcond=None)[0]
        m = m + d[0] * e1 + d[1] * e2
        m /= np.linalg.norm(m)
        side = np.degrees(np.einsum("ij,ij->i", x, m - u))
        along = np.degrees(np.einsum("ij,ij->i", t, m - u))
        keep = (np.abs(side) < max(2.0, 2 * np.median(np.abs(side[keep])))) & \
               (np.abs(along) < max(6.0, 3 * np.median(np.abs(along[keep]))))
    info = x[keep].T @ x[keep] + alpha * t[keep].T @ t[keep]
    return {"up": m, "keep": keep, "side_deg": side, "along_deg": along, "alpha": alpha, "info": info}


def tilt_of(params, up):
    """How far a placement leans: the angle (degrees) between the site's vertical and where it
    puts the reconstruction direction `up`, and the bearing up leans toward (level ground in the
    placed cloud falls that way)."""
    v = Rotation.from_rotvec(params[1:4]).apply(np.asarray(up) / np.linalg.norm(up))
    return float(np.degrees(np.arccos(np.clip(v[2], -1, 1)))), float(np.degrees(np.arctan2(v[0], v[1])) % 360)


def plumb_lean(params, up, info=None):
    """Where a placement puts the reconstruction direction `up`: its east and north components
    (radians of lean, for a small tilt). With info (plumb_consensus), weighed by what the frames
    fix in each direction: in full along the best-fixed direction, less across it."""
    rot = Rotation.from_rotvec(params[1:4])
    lean = rot.apply(np.asarray(up) / np.linalg.norm(up))[:2]
    if info is None:
        return lean
    r = rot.as_matrix()
    ev, vec = np.linalg.eigh((r @ info @ r.T)[:2, :2])
    ev = np.clip(ev / ev[-1], 0, 1)
    return vec @ (np.sqrt(ev) * (vec.T @ lean))


def plumb_directions(params, plumb):
    """The bearing (degrees, 0-180) of the lean the frames fix least, the ratio of the weight they
    give the best- and least-fixed directions, and the placement's lean (degrees) along each."""
    rot = Rotation.from_rotvec(params[1:4])
    r = rot.as_matrix()
    info = plumb["info"] if plumb["info"] is not None else np.eye(3) - np.outer(plumb["up"], plumb["up"])
    ev, vec = np.linalg.eigh((r @ info @ r.T)[:2, :2])
    lean = rot.apply(plumb["up"])[:2]
    weak, strong = vec[:, 0], vec[:, 1]
    return (float(np.degrees(np.arctan2(weak[0], weak[1])) % 180), float(ev[1] / max(ev[0], 1e-12)),
            float(np.degrees(strong @ lean)), float(np.degrees(weak @ lean)))


def level(params, up, pivot):
    """The placement turned about `pivot` (reconstruction coordinates, which keeps its site position)
    by the smallest rotation that makes `up` vertical."""
    rot = Rotation.from_rotvec(params[1:4])
    q = Rotation.align_vectors([[0.0, 0.0, 1.0]], [rot.apply(np.asarray(up) / np.linalg.norm(up))])[0]
    turned = q * rot
    p = np.r_[params[0], turned.as_rotvec(), np.zeros(3)]
    p[4:7] = apply(params, np.asarray(pivot)[None])[0] - apply(p, np.asarray(pivot)[None])[0]
    return p


def plumb_frames(rec, tag, spec):
    """(frame, roi) pairs for the control's "frames": "auto", or a list of names, {"frame", "roi"}
    and {"from", "to", "clip", "roi"} time ranges (seconds)."""
    reg = [n for n, rv in zip(rec["names"], rec["rvecs"]) if np.all(np.isfinite(rv))]
    if spec == "auto":
        return [(n, None) for n in reg]
    out = []
    for item in spec:
        if isinstance(item, str):
            item = {"frame": item}
        if "frame" in item:
            if item["frame"] not in reg:
                print(f"  plumb: frame {item['frame']} is not registered, left out")
                continue
            out.append((item["frame"], item.get("roi")))
            continue
        clip = item.get("clip")
        for n in reg:
            c, stem = n.split("/", 1) if "/" in n else (None, n)
            if c == clip and item["from"] <= float(stem[1:]) <= item["to"]:
                out.append((n, item.get("roi")))
    return out


def measure_plumb(site, rec, tag, cfg):
    """The reconstruction's up direction from the frames the control names (see the notes above)."""
    frames = plumb_frames(rec, tag, cfg.get("frames", "auto"))
    ups = frame_ups(rec, frames, lambda n: cv2.imread(str(frame_path(site, tag, n)), cv2.IMREAD_GRAYSCALE))
    if len(ups) < 3:
        raise DomusError(f"plumb: only {len(ups)} of {len(frames)} frames show enough long near-vertical "
                         "edges; name other frames or leave the plumb out")
    names = [n for n, _ in ups]
    alpha = float(cfg.get("toward_away_weight", 1.0))
    if alpha < 1:
        c = plumb_consensus([u for _, u in ups], frame_axes(rec, names), alpha)
        m, keep, info = c["up"], c["keep"], c["info"]
        spread = float(np.median(np.abs(c["side_deg"][keep])))
        what = (f"median {spread:.2f} deg off sideways, {np.median(np.abs(c['along_deg'][keep])):.2f} toward or "
                f"away, weighed {alpha}")
    else:
        m, keep, off = consensus_up([u for _, u in ups])
        info = None
        spread = float(np.median(off[keep]))
        what = f"median {spread:.2f} deg off their mean"
    print(f"plumb: {len(frames)} frames, {len(ups)} with a vertical vanishing point, {int(keep.sum())} agree ({what})")
    limit = cfg.get("max_spread_deg", 1.0)
    if spread > limit and not cfg.get("check"):
        raise DomusError(f"plumb: the frames scatter ({spread:.2f} deg, more than {limit}): foliage or a "
                         "grate passing for verticals, or a bent reconstruction; name the frames (and boxes) "
                         "that see true verticals")
    return {"up": m, "info": info, "alpha": alpha, "frames": [n for n, k in zip(names, keep) if k],
            "dropped": [n for n, k in zip(names, keep) if not k], "tried": len(frames), "spread_deg": spread,
            "sigma_deg": float(cfg.get("sigma_deg", 0.5)), "check": bool(cfg.get("check"))}


def compose(outer, inner):
    """Parameters of x -> apply(outer, apply(inner, x))."""
    so, ro = np.exp(outer[0]), Rotation.from_rotvec(outer[1:4]).as_matrix()
    si, ri = np.exp(inner[0]), Rotation.from_rotvec(inner[1:4]).as_matrix()
    return np.r_[np.log(so * si), Rotation.from_matrix(ro @ ri).as_rotvec(), so * ro @ inner[4:7] + outer[4:7]]


def control_points(site, rec, control, tag, dem):
    """Name -> (local xyz, site xyz) for every feature the control file can place."""
    marks = {}
    if control.get("picks"):
        main_clip = tag.split("-")[0]
        for f in json.loads((site.path / control["picks"]).read_text(encoding="utf-8"))["features"]:
            obs = [{"frame": o["frame"].split("/", 1)[1] if o["frame"].startswith(main_clip + "/") else o["frame"],
                    "px": o["px"]} for o in f["obs"]]
            marks[f["id"]] = [o for o in obs if o["frame"] in rec["names"]
                              and np.all(np.isfinite(rec["rvecs"][rec["names"].index(o["frame"])]))]
    out = {}
    for c in control.get("anchors", []) + control.get("points", []):
        # "pick" names one mark, or a list of marks whose 3D midpoint is the point (two
        # diagonal corners of a grate, say).
        picks = c.get("pick", c["name"])
        obs_sets = [c["obs"]] if c.get("obs") else [marks.get(m, []) for m in ([picks] if isinstance(picks, str) else picks)]
        if min(len(o) for o in obs_sets) < 2:
            print(f"  {c['name']}: seen in fewer than two registered frames, not used")
            continue
        at = np.array([np.nan if v is None else v for v in c["site"]], float)
        if np.isnan(at[2]) and c.get("height") == "ground":
            at[2] = float(dem.sample(np.array([at[0]]), np.array([at[1]]))[0])
        local = np.mean([triangulate_pixels(rec, o) for o in obs_sets], axis=0)
        out[c["name"]] = (local, at, None if c.get("check") else c.get("sigma_m", 0.03))
    return out


def marks_in(site, rec, control, tag):
    """Mark id -> its observations in registered frames of this reconstruction."""
    main_clip = tag.split("-")[0]
    marks = {}
    for f in json.loads((site.path / control["picks"]).read_text(encoding="utf-8"))["features"]:
        obs = [{"frame": o["frame"].split("/", 1)[1] if o["frame"].startswith(main_clip + "/") else o["frame"],
                "px": o["px"]} for o in f["obs"]]
        marks[f["id"]] = [o for o in obs if o["frame"] in rec["names"]
                          and np.all(np.isfinite(rec["rvecs"][rec["names"].index(o["frame"])]))]
    return marks


def on_line_points(site, rec, control, tag):
    """(local xyz, line start, line end, sigma) for marks known to lie on a wall line in plan
    (a door in the west wall, say)."""
    if not control.get("on_line"):
        return []
    marks = marks_in(site, rec, control, tag)
    out = []
    for c in control["on_line"]:
        a, b = (np.asarray(v, float) for v in c["line"])
        for m in c["picks"]:
            if len(marks.get(m, [])) >= 2:
                out.append((triangulate_pixels(rec, marks[m]), a, b, c.get("sigma_m", 0.05)))
                print(f"  on line: {m}")
    return out


def known_lengths(site, rec, control, tag):
    """(local a, local b, metres, sigma) for each known distance between two marks."""
    if not control.get("lengths"):
        return []
    main_clip = tag.split("-")[0]
    marks = {}
    for f in json.loads((site.path / control["picks"]).read_text(encoding="utf-8"))["features"]:
        marks[f["id"]] = [{"frame": o["frame"].split("/", 1)[1] if o["frame"].startswith(main_clip + "/") else o["frame"],
                           "px": o["px"]} for o in f["obs"]]
    out = []
    for ln in control["lengths"]:
        ends = []
        for m in ln["between"]:
            obs = [o for o in marks.get(m, []) if o["frame"] in rec["names"]
                   and np.all(np.isfinite(rec["rvecs"][rec["names"].index(o["frame"])]))]
            ends.append(triangulate_pixels(rec, obs) if len(obs) >= 2 else None)
        if all(e is not None for e in ends):
            out.append((ends[0], ends[1], ln["m"], ln.get("sigma_m", 0.05)))
            print(f"  length {ln['between'][0]}-{ln['between'][1]}: {ln['m']} m")
    return out


def eaves_height(house):
    """The eaves' height on the model's datum, from [house]: points on a wall's plane below it count as wall points.
    It's fl_upper plus roof_at_wall_above_fl where the site gives that, else roof_plane_at_wall, the same height,
    which every house has."""
    above = house.get("roof_at_wall_above_fl")
    return house["roof_plane_at_wall"] if above is None else house["fl_upper"] + above


def place(site, tag, via=None):
    """`domus video place`: reconstruction `tag` on the site's grid, by its control file or, `via` a placed
    reconstruction it's linked to, by that one's placement; the module's docstring says how. Writes
    data/video/<tag>-georef.json and <tag>-site.npz, and gives what the -georef.json holds."""
    rec = load(video_dir(site) / f"{tag}-sfm.npz")
    house = house_source.house(site).settings
    eaves = eaves_height(house)
    planes = house_planes(site)
    dem = rasters.dem(site)
    ground = Grid(dem, site.settings()["model"]["extent"])
    pts = rec["xyz"][rec["nview"] >= 3]
    cam_local = np.array([-Rotation.from_rotvec(rv).as_matrix().T @ tv
                          for rv, tv in zip(rec["rvecs"], rec["tvecs"]) if np.all(np.isfinite(rv))])

    pin, held, res0 = None, {}, {}
    control_path = video_dir(site) / f"{tag}-control.json"
    if not via:
        control = json.loads(control_path.read_text(encoding="utf-8"))
    else:      # a linked model takes only the vertical from a control file of its own
        control = {k: v for k, v in json.loads(control_path.read_text(encoding="utf-8")).items() if k == "plumb"} \
            if control_path.exists() else {}
    cps = control_points(site, rec, control, tag, dem)
    on_line = on_line_points(site, rec, control, tag)
    anchors = {c["name"]: cps[c["name"]] for c in control.get("anchors", []) if c["name"] in cps}
    plumb = measure_plumb(site, rec, tag, control["plumb"]) if control.get("plumb") else None
    hold_plumb = plumb is not None and not plumb["check"]
    if via:
        link = json.loads((video_dir(site) / f"{tag}-to-{via}.json").read_text(encoding="utf-8"))
        base = json.loads((video_dir(site) / f"{via}-georef.json").read_text(encoding="utf-8"))
        p0 = compose(np.array(base["params"]), np.array(link["params"]))
        print(f"via {via}: {link['inliers']} linking points, median error {link['median_error']:.4f}")
    elif control.get("anchor"):
        a_local, a_site, _ = cps[control["anchor"]]
        foot, top = control["up"] if control.get("up") else (control["anchor"], control["vertical"])
        others = [v for n, v in cps.items() if n not in (control["anchor"], foot, top) and v[2] is not None]
        corner_up = cps[top][0] - cps[foot][0]
        if plumb is not None:          # the start the corner's vertical alone would give
            ang = np.degrees(np.arccos(np.clip(corner_up @ plumb["up"] / np.linalg.norm(corner_up), -1, 1)))
            plumb["start_tilt_deg"] = round(float(ang), 2)
        p0 = search_pose(pts, cam_local, a_local, plumb["up"] if hold_plumb else corner_up, a_site, planes,
                         ground, eaves, on_line, others)
        pin = (a_local, a_site[:2])
        held = {n: v for n, v in cps.items() if n != control["anchor"] and v[2] is not None}
    elif len(anchors) >= 3:
        loc = np.array([v[0] for v in anchors.values()])
        sit = np.array([v[1] for v in anchors.values()])
        p0 = horn_initial(loc, sit)
        held = {n: v for n, v in anchors.items() if v[2] is not None}
    elif len(anchors) == 2:
        loc = np.array([v[0] for v in anchors.values()])
        sit = np.array([v[1] for v in anchors.values()])
        p0 = search_axis(pts, cam_local, loc, sit, planes, ground, eaves, on_line)
        held = {n: v for n, v in anchors.items() if v[2] is not None}
    else:
        loc = np.array([v[0] for v in cps.values()])
        sit = np.array([v[1] for v in cps.values()])
        p0, _ = fit_similarity(loc, sit)
        held = {n: v for n, v in cps.items() if v[2] is not None}
    if plumb is not None and "start_tilt_deg" not in plumb:
        tilt, toward = tilt_of(p0, plumb["up"])
        plumb["start_tilt_deg"], plumb["start_toward_deg"] = round(tilt, 2), round(toward)
        if hold_plumb:          # turn the start level about the control it rests on
            pivot = (np.mean([v[0] for v in held.values()], axis=0) if held else
                     np.mean([v[0] for v in cps.values()], axis=0) if cps else cam_local.mean(0))
            p0 = level(p0, plumb["up"], pivot)
    if plumb is not None:
        print(f"plumb: the start leans {plumb['start_tilt_deg']:.2f} deg from the frames' vertical"
              + (", turned level" if hold_plumb else ""))
    for name, (loc_i, site_i, _) in cps.items():
        res0[name] = np.round(apply(p0, loc_i[None])[0] - site_i, 3).tolist()
    print(f"rough fit: scale {np.exp(p0[0]):.4f}")
    for name, r in res0.items():
        print(f"   {name:<20} {r}")

    def full(p):
        """Free parameters -> similarity. With a pin, its plan position is held exactly and the
        free parameters are scale, rotation and the pin's height."""
        if pin is None:
            return p
        s, rot = np.exp(p[0]), Rotation.from_rotvec(p[1:4])
        moved = s * rot.apply(pin[0])
        return np.r_[p[:4], pin[1] - moved[:2], p[4] - moved[2]]

    lengths = known_lengths(site, rec, control, tag)
    held_local = np.array([v[0] for v in held.values()]) if held else np.zeros((0, 3))
    held_site = np.array([v[1] for v in held.values()]) if held else np.zeros((0, 3))
    held_sigma = np.array([v[2] for v in held.values()]) if held else np.zeros(0)
    params = p0 if pin is None else np.r_[p0[:4], apply(p0, pin[0][None])[0, 2]]
    delta = 0.05

    strip = Polygon(control["paved"]) if control.get("paved") else None
    fix_areas = terrain.read_fixes(site)[0]
    untrusted = unary_union([Polygon(a) for a in fix_areas]) if fix_areas else None

    for tol in (0.6, 0.35, 0.2, 0.15):
        sp = apply(full(params), pts)
        dz = dem.sample(sp[:, 0], sp[:, 1])
        lab = classify(sp, planes, dz, eaves, tol)
        if strip is not None:
            # Paved ground (a driveway the LiDAR sees clearly) counts however far off it is, so a
            # tilt that lifts one end of the walk cannot hide by dropping that end from the fit:
            # in each 0.4 m cell inside the strip, the lowest fifth of the points are ground.
            inside = np.array([strip.contains(Point(e, n)) for e, n in sp[:, :2]])
            cells = np.floor(sp[:, :2] / 0.4).astype(np.int64)
            idx = np.flatnonzero(inside)
            if len(idx):
                keys, inv = np.unique(cells[idx], axis=0, return_inverse=True)
                inv = inv.ravel()
                for k in range(len(keys)):
                    members = idx[inv == k]
                    if len(members) >= 5:
                        z = sp[members, 2]
                        lab[members[z <= np.percentile(z, 20)]] = -1
        if untrusted is not None:
            # The LiDAR is known wrong or out of date in the ground-fix areas: no ground there.
            lab[(lab == -1) & shapely.contains_xy(untrusted, sp[:, 0], sp[:, 1])] = -2
        sel = lab >= -1
        P = pts[sel]
        L = lab[sel]

        def surface_resid(p, P=P, L=L):
            q = apply(full(p), P)
            out = np.empty(len(q))
            g = L == -1
            out[g] = q[g, 2] - dem.sample(q[g, 0], q[g, 1])
            for k, (a, u, _) in enumerate(planes):
                m = L == k
                if m.any():
                    out[m] = (q[m, :2] - a) @ np.array([-u[1], u[0]])
            return np.nan_to_num(out)

        def resid(p, surface_resid=surface_resid):
            r = surface_resid(p)
            # Huber on the surface residuals (as square roots, so the solver's own loss is plain least squares)
            r = np.sign(r) * np.sqrt(np.where(np.abs(r) <= delta, r * r, 2 * delta * np.abs(r) - delta * delta))
            if on_line:
                d = line_offsets(full(p), on_line) / np.array([c[3] for c in on_line])
                r = np.concatenate([r, d * np.sqrt(max(1.0, len(r) / 50))])
            if len(held_local):
                d = (apply(full(p), held_local) - held_site) / held_sigma[:, None]
                d = np.nan_to_num(d) * np.sqrt(max(1.0, len(r) / 50))
                r = np.concatenate([r, d.ravel()])
            for a, b, m, sig in lengths:
                d = np.exp(p[0]) * np.linalg.norm(a - b) - m
                r = np.concatenate([r, [d / sig * np.sqrt(max(1.0, len(r) / 50))]])
            if hold_plumb:
                lean = plumb_lean(full(p), plumb["up"], plumb["info"]) / math.radians(plumb["sigma_deg"])
                r = np.concatenate([r, lean * np.sqrt(max(1.0, len(r) / 50))])
            # A hand-held phone: the cameras' median height above the ground stays within
            # 1-2 m (otherwise a fit pinned at one point can shrink the scene onto the ground).
            c = apply(full(p), cam_local)
            if untrusted is not None:
                c = c[~shapely.contains_xy(untrusted, c[:, 0], c[:, 1])]
            h = np.nanmedian(c[:, 2] - dem.sample(c[:, 0], c[:, 1])) if len(c) >= 3 else 1.5
            off = max(0.0, 1.0 - h) + max(0.0, h - 2.0)
            return np.concatenate([r, [off / 0.05 * np.sqrt(len(r))]])

        sol = least_squares(resid, params)
        params = sol.x
        r = surface_resid(params)
        print(f"tol {tol}: {int((L == -1).sum())} ground pts, {int((L >= 0).sum())} wall pts, "
              f"median |res| ground {np.median(np.abs(r[L == -1])):.3f} m, walls "
              f"{np.median(np.abs(r[L >= 0])) if (L >= 0).any() else float('nan'):.3f} m, scale {np.exp(params[0]):.4f}")
    params = full(params)
    checks = {}
    for name, (loc_i, site_i, _) in cps.items():
        checks[name] = np.round(apply(params, loc_i[None])[0] - site_i, 3).tolist()
        print(f"   check {name:<20} {checks[name]}")

    site_all = apply(params, rec["xyz"])
    cams = []
    for name, rv, tv in zip(rec["names"], rec["rvecs"], rec["tvecs"]):
        if not np.all(np.isfinite(rv)):
            continue
        rot = Rotation.from_rotvec(rv).as_matrix()
        cams.append((name, *apply(params, (-rot.T @ tv)[None])[0]))
    np.savez_compressed(video_dir(site) / f"{tag}-site.npz", xyz=site_all, rgb=rec["rgb"], nview=rec["nview"],
                        cameras=np.array([c[1:] for c in cams]), camera_names=np.array([c[0] for c in cams]))
    hc = np.array([c[3] for c in cams]) - dem.sample(np.array([c[1] for c in cams]), np.array([c[2] for c in cams]))
    out = {"clip": tag, "params": params.tolist(), "scale": float(np.exp(params[0])),
           "rough_fit_residuals_m": res0, "control_residuals_m": checks,
           "ground_median_abs_m": float(np.median(np.abs(r[L == -1]))),
           "wall_median_abs_m": float(np.median(np.abs(r[L >= 0]))) if (L >= 0).any() else None,
           "ground_points": int((L == -1).sum()), "wall_points": int((L >= 0).sum()),
           "camera_height_median_m": float(np.nanmedian(hc))}
    if plumb is not None:
        tilt, toward = tilt_of(params, plumb["up"])
        weak_bearing, ratio, lean_strong, lean_weak = plumb_directions(params, plumb)
        out["plumb"] = {
            "held": hold_plumb, "sigma_deg": plumb["sigma_deg"], "tilt_deg": round(tilt, 3),
            "toward_deg": round(toward), "lean_best_fixed_deg": round(lean_strong, 3),
            "lean_least_fixed_deg": round(lean_weak, 3), "least_fixed_bearing_deg": round(weak_bearing),
            "weight_ratio": round(ratio, 1), "start_tilt_deg": plumb["start_tilt_deg"],
            "start_toward_deg": plumb.get("start_toward_deg"),
            "frame_spread_deg": round(plumb["spread_deg"], 2), "toward_away_weight": plumb["alpha"],
            "frames_tried": plumb["tried"], "up_reconstruction": [round(float(v), 6) for v in plumb["up"]],
            "frames": plumb["frames"], "frames_dropped": plumb["dropped"],
            "note": ("tilt_deg: the angle between the site's vertical and the frames' vertical in this placement "
                     "(the plumb residual), leaning toward toward_deg; lean_*: its parts along the direction the "
                     "frames fix best and across it (least_fixed_bearing_deg), which they fix weight_ratio times "
                     "less; start_tilt_deg: the same for the start the control alone gives; frame_spread_deg: the "
                     "frames' median offset from their consensus (sideways, where toward_away_weight is below 1)")}
        print(f"plumb: placed {tilt:.2f} deg from the frames' vertical (toward {toward:.0f}; {lean_strong:+.2f} along "
              f"the best-fixed direction, {lean_weak:+.2f} across it, bearing {weak_bearing:.0f}, fixed {ratio:.1f}x "
              f"less); the start was {plumb['start_tilt_deg']:.2f}")
    (video_dir(site) / f"{tag}-georef.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("params",)}))
    return out
