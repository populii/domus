"""Place a video reconstruction (domus.capture.sfm output) on the site grid.

Control points are features picked in two or more frames whose site coordinates are known
(house corners from the plans, ground heights from the LiDAR). Each is triangulated with
the reconstructed cameras, then a 7-parameter similarity (scale, rotation, shift) is fitted
from the reconstruction frame to site coordinates (grid east/north, height).
Points whose height is unknown constrain east and north only.
"""

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from . import sfm


def load(path):
    d = np.load(path)
    names = list(d["frame_names"])
    return {
        "xyz": d["xyz"], "rgb": d["rgb"], "nview": d["nview"], "names": names,
        "rvecs": d["rvecs"], "tvecs": d["tvecs"], "intr": d["intrinsics"],
    }


def triangulate_pixels(rec, observations):
    """DLT triangulation of one feature seen at pixel `px` in frames `frame` (by name)."""
    f, k1, cx, cy = rec["intr"]
    shim = sfm.Reconstruction([], f=f, k1=k1, cx=cx, cy=cy)
    rows = []
    for o in observations:
        i = rec["names"].index(o["frame"])
        rvec, tvec = rec["rvecs"][i], rec["tvecs"][i]
        if not np.all(np.isfinite(rvec)):
            raise ValueError(f"frame {o['frame']} is not registered")
        r = Rotation.from_rotvec(rvec).as_matrix()
        p = np.hstack([r, tvec.reshape(3, 1)])
        u, v = sfm.undistort(shim, np.asarray(o["px"], float))
        rows.append(u * p[2] - p[0])
        rows.append(v * p[2] - p[1])
    _, _, vt = np.linalg.svd(np.array(rows))
    x = vt[-1]
    return x[:3] / x[3]


def apply(params, pts):
    s = np.exp(params[0])
    r = Rotation.from_rotvec(params[1:4]).as_matrix()
    return s * (np.asarray(pts) @ r.T) + params[4:7]


def fit_similarity(local, site, initial=None):
    """local: (N,3) reconstruction coords; site: (N,3) with NaN for unknown components."""
    local = np.asarray(local, float)
    site = np.asarray(site, float)
    known = np.isfinite(site)

    def resid(p):
        return (apply(p, local) - np.where(known, site, 0))[known]

    if initial is None:
        initial = horn_initial(local[known.all(1)], site[known.all(1)]) if known.all(1).sum() >= 3 \
            else np.r_[0, 0, 0, 0, np.nanmean(site, 0)]
    sol = least_squares(resid, initial, loss="soft_l1", f_scale=0.05)
    res = np.full(site.shape, np.nan)
    res[known] = resid(sol.x)
    return sol.x, res


def horn_initial(a, b):
    """Closed-form similarity (Umeyama) from >= 3 full correspondences, as fit parameters."""
    ma, mb = a.mean(0), b.mean(0)
    aa, bb = a - ma, b - mb
    u, s, vt = np.linalg.svd(bb.T @ aa)
    d = np.sign(np.linalg.det(u @ vt))
    dmat = np.diag([1, 1, d])
    r = u @ dmat @ vt
    scale = (s * np.diag(dmat)).sum() / (aa ** 2).sum()
    t = mb - scale * r @ ma
    return np.r_[np.log(scale), Rotation.from_matrix(r).as_rotvec(), t]
