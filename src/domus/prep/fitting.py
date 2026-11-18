"""Fitting a site's parts to a placed video by hand: a wall's face in a cloud, and where a frame's pixels fall on a plane.

    domus prep face-fit CLOUD SPEC.json [--all]
    domus prep frame-points TAG SPEC.json [--georef FILE.json]

`face-fit` fits vertical faces in a placed cloud (named as domus prep cloud takes it: data/video/<tag>-dense, ...).
SPEC.json names each face with its box in the wall options' frame, `"box": [s0, s1, o0, o1, z0, z1]`, the axis it runs
along, `"along": "s"` or `"o"`, and if you like the model's line for it, `"model": [[s, o], [s, o]]`. The face's other
coordinate is fitted as a straight line along it: from the commonest offset (in 2 cm steps, about the model line's
slope), then by least squares on the points within 5 cm of that and within 4 cm of the line, three times. Printed are
how many points lie on the face, their spread, the fitted offset at both ends, and with a model line its offset there
and the mean of the two differences.

`frame-points` finds where pixels of a placed frame fall on a plane. SPEC.json is a list of frames, each with its
`"frame"` (its name in the reconstruction, such as t012.500), its `"plane"`, `{"through_so": [[s, o], [s, o]]}` for a
vertical plane through two points or `{"level": H}` for a level one, and its `"points"`, pixels by name
(`"window's jamb": [965, 100]`). Each pixel's ray from the frame's camera, in data/video/<TAG>-sfm.npz as placed by
data/video/<TAG>-georef.json (or --georef), meets the plane; printed are the camera's place, then each point's place in
the frame and its height.
"""

import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from ..errors import DomusError
from .cloud import read_clouds


def video_dir(site):
    """The site's video work, data/video/."""
    return site.path / "data" / "video"


def face_fit(so, z, item):
    """Fit one face (a SPEC.json entry) to points given in the frame (`so`) with their heights (`z`): a dict with the
    points in the box (`points`), those on the face (`on`), their spread in metres, the fitted offset at both ends
    (`ends`: [(along, offset)]) and with a model line its offset at the same ends (`model`). None if the box has fewer
    than 30 points."""
    s0, s1, o0, o1, z0, z1 = item["box"]
    m = (so[:, 0] >= s0) & (so[:, 0] <= s1) & (so[:, 1] >= o0) & (so[:, 1] <= o1) & (z >= z0) & (z <= z1)
    if item["along"] not in ("s", "o"):
        raise DomusError(f'"along": {item["along"]!r}: give "s" or "o", the axis the face runs along')
    a_ax, f_ax = (0, 1) if item["along"] == "s" else (1, 0)
    t, v = so[m, a_ax], so[m, f_ax]
    if len(t) < 30:
        return {"points": len(t)}
    slope, mm = 0.0, None
    if "model" in item:
        mm = np.array(item["model"], float)
        slope = (mm[1, f_ax] - mm[0, f_ax]) / (mm[1, a_ax] - mm[0, a_ax])
    r = v - slope * t
    h, e = np.histogram(r, bins=np.arange(r.min(), r.max() + 0.02, 0.02))
    mode = e[np.argmax(h)] + 0.01
    keep = np.abs(r - mode) < 0.05
    for _ in range(3):
        a = np.c_[np.ones(keep.sum()), t[keep]]
        coef, *_ = np.linalg.lstsq(a, v[keep], rcond=None)
        res = v - (coef[0] + coef[1] * t)
        keep = np.abs(res) < 0.04
    ends = [t[keep].min(), t[keep].max()]
    out = {"points": len(t), "on": int(keep.sum()), "spread": float(res[keep].std()),
           "ends": [(float(u), float(coef[0] + coef[1] * u)) for u in ends]}
    if mm is not None:
        out["model"] = [(float(u), float(mm[0, f_ax] + slope * (u - mm[0, a_ax]))) for u in ends]
    return out


def face_line(name, along, fit):
    """A fitted face as face-fit prints it."""
    if "on" not in fit:
        return f"{name}: only {fit['points']} points"
    line = f"{name}: {fit['on']} of {fit['points']} points on the face, spread {fit['spread'] * 100:.1f} cm; "
    line += "; ".join(f"at {along} {u:.2f}: {w:.3f}" for u, w in fit["ends"])
    if "model" in fit:
        line += "   | model: " + "; ".join(f"at {along} {u:.2f}: {w:.3f}" for u, w in fit["model"])
        line += f"   | cloud - model: {np.mean([a[1] - b[1] for a, b in zip(fit['ends'], fit['model'], strict=True)]):+.3f}"
    return line


def ray(u, v, f, k1, cx, cy):
    """A pixel's ray in its camera's coordinates, its radial distortion (k1) taken out."""
    x, y = (u - cx) / f, (v - cy) / f
    xu, yu = x, y
    for _ in range(20):
        r2 = xu * xu + yu * yu
        xu, yu = x / (1 + k1 * r2), y / (1 + k1 * r2)
    return np.array([xu, yu, 1.0])


class Placed:
    """A placed reconstruction: its frames' cameras (data/video/<tag>-sfm.npz) and their placement on the site
    (data/video/<tag>-georef.json, or the file `georef`)."""

    def __init__(self, site, tag, georef=None):
        sfm, geo = video_dir(site) / f"{tag}-sfm.npz", georef or video_dir(site) / f"{tag}-georef.json"
        for path in (sfm, geo):
            if not path.exists():
                raise DomusError(f"{tag} isn't a placed reconstruction: there's no {path}")
        with np.load(sfm, allow_pickle=False) as rec:
            self.rvecs, self.tvecs = np.asarray(rec["rvecs"], float), np.asarray(rec["tvecs"], float)
            self.intrinsics = tuple(float(v) for v in rec["intrinsics"])
            self.names = [str(n) for n in rec["frame_names"]]
        params = np.asarray(json.loads(Path(geo).read_text(encoding="utf-8"))["params"], float)
        self.scale, self.rot, self.shift = np.exp(params[0]), Rotation.from_rotvec(params[1:4]).as_matrix(), params[4:7]

    def camera(self, frame):
        """(the frame's camera rotation, its centre on the site), or None if the frame isn't registered."""
        if frame not in self.names or not np.isfinite(self.rvecs[self.names.index(frame)]).all():
            return None
        i = self.names.index(frame)
        rc = Rotation.from_rotvec(self.rvecs[i]).as_matrix()
        return rc, self.scale * (-(rc.T @ self.tvecs[i])) @ self.rot.T + self.shift

    def project(self, pts, i):
        """Points on the site into the pixels of the reconstruction's frame `i`, and their depth in front of its
        camera (negative behind it)."""
        local = (np.asarray(pts, float) - self.shift) @ self.rot / self.scale
        rc = Rotation.from_rotvec(self.rvecs[i]).as_matrix()
        pc = local @ rc.T + self.tvecs[i]
        z = pc[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            x, y = pc[:, 0] / z, pc[:, 1] / z
        f, k1, cx, cy = self.intrinsics
        rad = 1 + k1 * (x * x + y * y)
        return np.column_stack([f * rad * x + cx, f * rad * y + cy]), z

    def on_plane(self, frame, plane, pixels, fr):
        """Where `pixels` ({name: (u, v)}) of `frame` meet `plane`: (the camera's centre, what the plane is,
        {name: (point, behind)}), `behind` where the plane is behind the camera; None if the frame isn't registered."""
        cam = self.camera(frame)
        if cam is None:
            return None
        rc, c = cam
        if "level" in plane:
            p0, n = np.array([c[0], c[1], float(plane["level"])]), np.array([0.0, 0.0, 1.0])
            what = f"level {plane['level']}"
        else:
            a, b = fr.en(np.array(plane["through_so"], float))
            d = np.r_[b - a, 0.0]
            n = np.cross(d / np.linalg.norm(d), [0.0, 0.0, 1.0])
            p0 = np.r_[a, 0.0]
            what = f"vertical plane through (s, o) {plane['through_so']}"
        out = {}
        for label, (u, v) in pixels.items():
            dvec = self.rot @ (rc.T @ ray(u, v, *self.intrinsics))
            t = ((p0 - c) @ n) / (dvec @ n)
            out[label] = (c + t * dvec, t < 0)
        return c, what, out


def fit_faces(site, cloud, spec, all_points=False):
    """`domus prep face-fit`: vertical faces fitted in the placed cloud `cloud` (a name in data/video, or an .npz
    file), as `spec` says (a dict of name: {box, along, model}, or a JSON file holding one)."""
    from ..options import design

    xyz, _ = read_clouds(site, [cloud], all_points)
    so, z = design.frame(site).so(xyz[:, :2]), xyz[:, 2]
    for name, item in (spec if isinstance(spec, dict) else json.loads(Path(spec).read_text(encoding="utf-8"))).items():
        print(face_line(name, item["along"], face_fit(so, z, item)))


def frame_points(site, tag, spec, georef=None):
    """`domus prep frame-points`: where pixels of placed reconstruction `tag`'s frames meet a plane, as `spec`
    says (a list of {frame, plane, points}, or a JSON file holding one); `georef` is another placement of it."""
    from ..options import design

    fr = design.frame(site)
    placed = Placed(site, tag, georef)
    for item in spec if isinstance(spec, list) else json.loads(Path(spec).read_text(encoding="utf-8")):
        found = placed.on_plane(item["frame"], item["plane"], item["points"], fr)
        if found is None:
            print(item["frame"], "is not a registered frame of", tag)
            continue
        c, what, points = found
        cs = fr.so(c[None, :2])[0]
        print(f"{item['frame']}: camera at s {cs[0]:.2f} o {cs[1]:.2f} H {c[2]:.2f}; {what}")
        for label, (p, behind) in points.items():
            so = fr.so(p[None, :2])[0]
            print(f"   {label:38s} s {so[0]:6.2f}  o {so[1]:6.2f}  H {p[2]:6.2f}" + ("   (behind the camera)" if behind else ""))
