"""Join two reconstructions of one clip that share frames, then re-adjust them as one.

    domus video merge VIDEO VIDEO VIDEO-178 [out-tag]

The clip's cached features and matches give its full set of tracks. The first model keeps
its frame; the second is carried into it by a similarity fitted to the points the two
share (the same keypoint used by a point in each, RANSAC then least squares). Every track
seen by the joined cameras is then triangulated afresh and a global bundle adjustment
settles poses, points and the shared intrinsics. Writes data/video/<out-tag>-sfm.npz.
"""

import json
import time

import numpy as np
from scipy.spatial.transform import Rotation

from . import safe_npz, sfm
from .sfm_georef import apply, horn_initial


def video_dir(site):
    """The site's video work, data/video/."""
    return site.path / "data" / "video"


T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)


def keyed_points(d):
    """(frame name, pixel) -> point index, for every observation of a model."""
    names = list(d["frame_names"])
    return {(names[int(fi)], round(float(u), 2), round(float(v), 2)): int(k) for k, fi, u, v in d["obs"]}


def similarity(a, b):
    """Similarity carrying model b into model a, from the points they share."""
    ka, kb = keyed_points(a), keyed_points(b)
    pairs = {kb[k]: ka[k] for k in kb.keys() & ka.keys()}
    pb = np.array([b["xyz"][t] for t in pairs])
    pa = np.array([a["xyz"][t] for t in pairs.values()])
    tol = 0.02 * np.median(np.linalg.norm(pa - pa.mean(0), axis=1))
    rng = np.random.default_rng(0)
    best_in = None
    for _ in range(2000):
        idx = rng.choice(len(pa), 3, replace=False)
        try:
            p = horn_initial(pb[idx], pa[idx])
        except np.linalg.LinAlgError:
            continue
        inl = np.linalg.norm(apply(p, pb) - pa, axis=1) < tol
        if best_in is None or inl.sum() > best_in.sum():
            best_in = inl
    p = horn_initial(pb[best_in], pa[best_in])
    err = np.linalg.norm(apply(p, pb) - pa, axis=1)
    log(f"{len(pairs)} shared points, {best_in.sum()} inliers, median error {np.median(err[best_in]):.4f}")
    return p


def merge(site, clip, a_tag, b_tag, out_tag=None):
    """`domus video merge`: reconstructions `a_tag` and `b_tag` of `clip`, which share frames, joined in `a_tag`'s
    frame and adjusted as one, as data/video/<out_tag>-sfm.npz (<clip>-joined unless given)."""
    out_tag = out_tag or f"{clip}-joined"
    a, b = np.load(video_dir(site) / f"{a_tag}-sfm.npz"), np.load(video_dir(site) / f"{b_tag}-sfm.npz")
    c = safe_npz.load(video_dir(site) / "cache" / f"{clip}-step1-w6.npz")
    frames = [sfm.Frame(n, k, d, col) for n, k, d, col in zip(c["names"], c["kps"], c["des"], c["cols"])]
    matches = c["matches"].item()
    f, k1, cx, cy = a["intrinsics"]
    rec = sfm.Reconstruction(frames, f=float(f), k1=float(k1), cx=float(cx), cy=float(cy))
    rec.tracks = sfm.build_tracks(len(frames), matches)
    index = {fr.name: i for i, fr in enumerate(frames)}

    for name, rv, tv in zip(a["frame_names"], a["rvecs"], a["tvecs"]):
        if np.all(np.isfinite(rv)):
            frames[index[str(name)]].rvec, frames[index[str(name)]].tvec = rv.copy(), tv.copy()
    p = similarity(a, b)
    s, r = np.exp(p[0]), Rotation.from_rotvec(p[1:4]).as_matrix()
    added, checks = 0, []
    for name, rv, tv in zip(b["frame_names"], b["rvecs"], b["tvecs"]):
        if not np.all(np.isfinite(rv)):
            continue
        rb = Rotation.from_rotvec(rv).as_matrix()
        ra = rb @ r.T
        centre = s * r @ (-rb.T @ tv) + p[4:7]
        fr = frames[index[str(name)]]
        if fr.rvec is not None:   # in both: how well do the two placements agree?
            ang = np.degrees(np.linalg.norm(Rotation.from_matrix(ra @ Rotation.from_rotvec(fr.rvec).as_matrix().T)
                                            .as_rotvec()))
            checks.append((ang, np.linalg.norm(centre - (-Rotation.from_rotvec(fr.rvec).as_matrix().T @ fr.tvec))))
            continue
        fr.rvec, fr.tvec = Rotation.from_matrix(ra).as_rotvec(), -ra @ centre
        added += 1
    if checks:
        ang, dist = np.array(checks).T
        log(f"shared frames: rotation differs by median {np.median(ang):.2f} deg, centre by {np.median(dist):.3f}")
    log(f"added {added} frames from {b_tag}")

    def triangulate_all(max_reproj):
        new = 0
        for tid in range(len(rec.tracks)):
            if tid not in rec.points:
                x = sfm.triangulate(rec, tid, max_reproj=max_reproj)
                if x is not None:
                    rec.points[tid] = x
                    new += 1
        return new

    log(f"triangulated {triangulate_all(6.0)} points")
    fix = [min(i for i, fr in enumerate(frames) if fr.rvec is not None)]
    for it in range(4):
        rms = sfm.bundle_adjust(rec, fix_frames=fix, refine_intrinsics=True, max_nfev=80)
        dropped = sfm.prune(rec, max_reproj=2.5)
        new = triangulate_all(3.0) if it < 2 else 0
        log(f"pass {it + 1}: median err {rms:.2f}px, f={rec.f:.1f}, k1={rec.k1:.4f}, dropped {dropped}, "
            f"new {new}, {len(rec.points)} points")
    log(json.dumps(sfm.save(rec, video_dir(site), out_tag, rms)))
