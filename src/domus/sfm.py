"""Structure from motion for a walking video: frames in, camera poses and a 3D point cloud out.

Incremental reconstruction of an ordered frame sequence:
SIFT features, matching within a sliding window, fundamental-matrix checks, tracks,
two-view start, PnP registration of each further frame, triangulation, and bundle
adjustment (scipy) of poses, points and a shared focal length and radial distortion.
The result is in an arbitrary frame and scale; domus.sfm_georef places it on the site.

Features and pair matching can run in worker processes (`workers`); the matching workers
read the descriptors from a memory-mapped file, so each pair costs no copying.
"""

import json
import math
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import coo_matrix

# --------------------------------------------------------------------------- geometry helpers

def rodrigues_many(rvecs):
    """Vectorised Rodrigues: (N,3) rotation vectors -> (N,3,3) matrices."""
    theta = np.linalg.norm(rvecs, axis=1)
    small = theta < 1e-12
    k = np.where(small[:, None], 0.0, rvecs / np.where(small, 1.0, theta)[:, None])
    kx, ky, kz = k.T
    zero = np.zeros_like(kx)
    kmat = np.stack([np.stack([zero, -kz, ky], -1), np.stack([kz, zero, -kx], -1),
                     np.stack([-ky, kx, zero], -1)], 1)
    s = np.sin(theta)[:, None, None]
    c = np.cos(theta)[:, None, None]
    eye = np.eye(3)[None]
    return eye + s * kmat + (1 - c) * (kmat @ kmat)


def _skew(v):
    """(N,3) vectors -> (N,3,3) cross-product matrices."""
    z = np.zeros(len(v))
    return np.stack([np.stack([z, -v[:, 2], v[:, 1]], -1), np.stack([v[:, 2], z, -v[:, 0]], -1),
                     np.stack([-v[:, 1], v[:, 0], z], -1)], 1)


def _left_jacobian(w):
    """Left Jacobian of SO(3) at rotation vectors w: d(R(w) x)/dw = -[R x]_x J_l(w)."""
    th = np.linalg.norm(w, axis=1)
    small = th < 1e-8
    ts = np.where(small, 1.0, th)
    a = np.where(small, 0.5, (1 - np.cos(ts)) / ts ** 2)
    b = np.where(small, 1 / 6, (ts - np.sin(ts)) / ts ** 3)
    k = _skew(w)
    return np.eye(3)[None] + a[:, None, None] * k + b[:, None, None] * (k @ k)


def project(points, rvecs, tvecs, f, k1, cx, cy):
    """Project per-observation points with per-observation camera params."""
    r = rodrigues_many(rvecs)
    pc = np.einsum("nij,nj->ni", r, points) + tvecs
    z = pc[:, 2]
    x = pc[:, 0] / z
    y = pc[:, 1] / z
    rad = 1 + k1 * (x * x + y * y)
    return np.column_stack([f * rad * x + cx, f * rad * y + cy]), z


# --------------------------------------------------------------------------- data

@dataclass
class Frame:
    name: str
    keypoints: np.ndarray          # (M,2) pixel coords
    descriptors: np.ndarray        # (M,128) RootSIFT
    colours: np.ndarray            # (M,3) RGB at keypoints
    rvec: np.ndarray | None = None
    tvec: np.ndarray | None = None


@dataclass
class Reconstruction:
    frames: list
    f: float
    k1: float
    cx: float
    cy: float
    tracks: list = field(default_factory=list)       # list of {frame: keypoint index}
    points: dict = field(default_factory=dict)       # track id -> xyz


def _frame_features(job):
    path, nfeatures, scale = job
    cv2.setNumThreads(1)
    sift = cv2.SIFT_create(nfeatures=nfeatures, contrastThreshold=0.02)
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if scale != 1.0:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    kps, des = sift.detectAndCompute(gray, None)
    pts = np.array([k.pt for k in kps], np.float64)
    des = des.astype(np.float32)
    des /= des.sum(axis=1, keepdims=True) + 1e-7     # RootSIFT
    des = np.sqrt(des)
    cols = img[np.clip(pts[:, 1].round().astype(int), 0, img.shape[0] - 1),
               np.clip(pts[:, 0].round().astype(int), 0, img.shape[1] - 1)][:, ::-1]
    return Frame(Path(path).stem, pts, des, cols)


def load_features(paths, nfeatures=6000, scale=1.0, workers=1):
    jobs = [(p, nfeatures, scale) for p in paths]
    if workers <= 1:
        return [_frame_features(j) for j in jobs]
    with ProcessPoolExecutor(workers) as ex:
        return list(ex.map(_frame_features, jobs, chunksize=4))


_SHARED = {}


def _share(des_path, kps_path, offsets):
    cv2.setNumThreads(1)
    _SHARED.update(des=np.load(des_path, mmap_mode="r"), kps=np.load(kps_path, mmap_mode="r"), off=offsets)


def _match_shared(pair):
    off, des, kps = _SHARED["off"], _SHARED["des"], _SHARED["kps"]
    a, b = (Frame("", np.asarray(kps[off[k]:off[k + 1]]), np.asarray(des[off[k]:off[k + 1]]), None)
            for k in pair)
    return pair, match_pair(a, b)


def match_pairs(frames, pairs, workers=1, scratch=None, log=None):
    """match_pair for each (i, j) in `pairs` -> {(i, j): matches}, in `workers` processes.
    The descriptors and keypoints go to memory-mapped files in `scratch` (a temporary folder
    by default) that every worker opens, instead of being copied for each pair."""
    pairs = list(pairs)
    if workers <= 1 or len(pairs) < 2 * workers:
        return {p: match_pair(frames[p[0]], frames[p[1]]) for p in pairs}
    out = {}
    with tempfile.TemporaryDirectory(dir=scratch) as td:
        offsets = np.concatenate([[0], np.cumsum([len(f.descriptors) for f in frames])])
        total = int(offsets[-1])     # a plain int: the .npy header must not say np.int64(...)
        des = np.lib.format.open_memmap(Path(td) / "des.npy", "w+", np.float32, (total, 128))
        kps = np.lib.format.open_memmap(Path(td) / "kps.npy", "w+", np.float64, (total, 2))
        for f, a, b in zip(frames, offsets[:-1], offsets[1:]):
            des[a:b] = f.descriptors
            kps[a:b] = f.keypoints
        des.flush()
        kps.flush()
        del des, kps
        with ProcessPoolExecutor(workers, initializer=_share,
                                 initargs=(Path(td) / "des.npy", Path(td) / "kps.npy", offsets)) as ex:
            for n, (pair, m) in enumerate(ex.map(_match_shared, pairs, chunksize=8)):
                out[pair] = m
                if log and n % 500 == 0:
                    log(f"  matched {n}/{len(pairs)} pairs")
    return out


def vlad(frames, k=64, sample_frames=200, per_frame=300, seed=0):
    """One global descriptor per frame (VLAD over its RootSIFT descriptors, intra- and
    power-normalised), for finding frames that see the same place: similar frames have a
    large dot product."""
    rng = np.random.default_rng(seed)
    pick = frames[::max(1, len(frames) // sample_frames)]
    sample = np.concatenate([f.descriptors[rng.choice(len(f.descriptors), min(per_frame, len(f.descriptors)),
                                                      replace=False)] for f in pick]).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 1e-4)
    _, _, centres = cv2.kmeans(sample, k, None, crit, 2, cv2.KMEANS_PP_CENTERS)
    half_sq = 0.5 * (centres ** 2).sum(axis=1)
    out = np.zeros((len(frames), k * centres.shape[1]), np.float32)
    for n, f in enumerate(frames):
        word = np.argmax(f.descriptors @ centres.T - half_sq, axis=1)
        v = np.zeros_like(centres)
        np.add.at(v, word, f.descriptors - centres[word])
        v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-12
        v = v.ravel()
        v = np.sign(v) * np.sqrt(np.abs(v))
        out[n] = v / (np.linalg.norm(v) + 1e-12)
    return out


def match_pair(a, b, ratio=0.8, thresh=1.0):
    flann = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 5}, {"checks": 64})
    knn = flann.knnMatch(a.descriptors, b.descriptors, k=2)
    good = [m[0] for m in knn if len(m) == 2 and m[0].distance < ratio * m[1].distance]
    if len(good) < 30:
        return np.zeros((0, 2), int)
    ia = np.array([m.queryIdx for m in good])
    ib = np.array([m.trainIdx for m in good])
    _, mask = cv2.findFundamentalMat(a.keypoints[ia], b.keypoints[ib], cv2.USAC_MAGSAC, thresh, 0.999, 10000)
    if mask is None:
        return np.zeros((0, 2), int)
    keep = mask.ravel().astype(bool)
    return np.column_stack([ia[keep], ib[keep]])


def build_tracks(n_frames, matches):
    """Union-find over (frame, keypoint) nodes.

    Matches are merged nearest frames first, and two partial tracks are joined only if they
    share no frame, so an ambiguous match splits a track instead of discarding it.
    """
    parent = {}
    members = {}  # root -> {frame: keypoint}

    def find(x):
        root = x
        while parent.get(root, root) != root:
            root = parent[root]
        while parent.get(x, x) != root:
            parent[x], x = root, parent[x]
        return root

    for (i, j) in sorted(matches, key=lambda p: (p[1] - p[0], p[0])):
        for a, b in matches[(i, j)]:
            na, nb = (i, int(a)), (j, int(b))
            ra, rb = find(na), find(nb)
            if ra == rb:
                continue
            fa = members.get(ra, {na[0]: na[1]})
            fb = members.get(rb, {nb[0]: nb[1]})
            if fa.keys() & fb.keys():
                continue
            if len(fa) < len(fb):
                ra, rb, fa, fb = rb, ra, fb, fa
            parent[rb] = ra
            fa.update(fb)
            members[ra] = fa
            members.pop(rb, None)
    return [fr for fr in members.values() if len(fr) >= 2]


def triangulate(rec, tid, min_angle_deg=1.5, max_reproj=3.0):
    """Linear triangulation of a track from all its registered views; returns xyz or None."""
    obs = [(fi, kp) for fi, kp in rec.tracks[tid].items() if rec.frames[fi].rvec is not None]
    if len(obs) < 2:
        return None
    rows, centres, rays = [], [], []
    for fi, kp in obs:
        fr = rec.frames[fi]
        r = cv2.Rodrigues(fr.rvec)[0]
        p = np.hstack([r, fr.tvec.reshape(3, 1)])
        u, v = undistort(rec, fr.keypoints[kp])
        rows.append(u * p[2] - p[0])
        rows.append(v * p[2] - p[1])
        c = -r.T @ fr.tvec
        centres.append(c)
        rays.append(r.T @ np.array([u, v, 1.0]))
    _, _, vt = np.linalg.svd(np.array(rows))
    x = vt[-1]
    if abs(x[3]) < 1e-12:
        return None
    x = x[:3] / x[3]
    # Parallax: the widest angle between rays to the point.
    dirs = np.array([(x - c) / np.linalg.norm(x - c) for c in centres])
    cosmin = np.min(dirs @ dirs.T)
    if math.degrees(math.acos(np.clip(cosmin, -1, 1))) < min_angle_deg:
        return None
    for fi, kp in obs:
        fr = rec.frames[fi]
        uv, z = project(x[None], fr.rvec[None], fr.tvec[None], rec.f, rec.k1, rec.cx, rec.cy)
        if z[0] <= 0 or np.hypot(*(uv[0] - fr.keypoints[kp])) > max_reproj:
            return None
    return x


def undistort(rec, uv):
    """Pixel -> normalised camera coordinates, inverting k1 with two fixed-point steps."""
    x = (uv[0] - rec.cx) / rec.f
    y = (uv[1] - rec.cy) / rec.f
    xu, yu = x, y
    for _ in range(3):
        rad = 1 + rec.k1 * (xu * xu + yu * yu)
        xu, yu = x / rad, y / rad
    return xu, yu


def kmatrix(rec):
    return np.array([[rec.f, 0, rec.cx], [0, rec.f, rec.cy], [0, 0, 1]], float)


def register(rec, fi, min_inliers=40):
    """PnP for frame fi from tracks that already have 3D points."""
    p3, p2 = [], []
    for tid, xyz in rec.points.items():
        kp = rec.tracks[tid].get(fi)
        if kp is not None:
            p3.append(xyz)
            p2.append(rec.frames[fi].keypoints[kp])
    if len(p3) < min_inliers:
        return False, len(p3)
    p3 = np.array(p3)
    p2 = np.array(p2)
    ok, rvec, tvec, inl = cv2.solvePnPRansac(p3, p2, kmatrix(rec), np.array([rec.k1, 0, 0, 0], float),
                                             iterationsCount=2000, reprojectionError=3.0,
                                             confidence=0.999, flags=cv2.SOLVEPNP_SQPNP)
    if not ok or inl is None or len(inl) < min_inliers:
        return False, 0 if inl is None else len(inl)
    rvec, tvec = cv2.solvePnPRefineLM(p3[inl[:, 0]], p2[inl[:, 0]], kmatrix(rec),
                                      np.array([rec.k1, 0, 0, 0], float), rvec, tvec)
    rec.frames[fi].rvec = rvec.ravel()
    rec.frames[fi].tvec = tvec.ravel()
    return True, len(inl)


def bundle_adjust(rec, frame_ids=None, fix_frames=(), refine_intrinsics=True, max_nfev=60, loss_scale=2.0):
    """Bundle adjustment over the given frames (default all registered) and their points."""
    regs = [i for i, fr in enumerate(rec.frames) if fr.rvec is not None]
    if frame_ids is not None:
        regs = [i for i in regs if i in set(frame_ids)]
    regset = set(regs)
    tids = [t for t in rec.points if sum(fi in regset for fi in rec.tracks[t]) >= 2]
    if not tids:
        return float("nan")
    cam_index = {fi: k for k, fi in enumerate(regs)}
    pt_index = {t: k for k, t in enumerate(tids)}
    obs_cam, obs_pt, obs_uv = [], [], []
    for t in tids:
        for fi, kp in rec.tracks[t].items():
            if fi in regset:
                obs_cam.append(cam_index[fi])
                obs_pt.append(pt_index[t])
                obs_uv.append(rec.frames[fi].keypoints[kp])
    obs_cam = np.array(obs_cam)
    obs_pt = np.array(obs_pt)
    obs_uv = np.array(obs_uv)
    fixed = np.array([fi in set(fix_frames) for fi in regs])
    ncam, npt = len(regs), len(tids)
    nin = 2 if refine_intrinsics else 0
    x0 = np.concatenate([np.concatenate([np.r_[rec.frames[fi].rvec, rec.frames[fi].tvec] for fi in regs]),
                         np.concatenate([rec.points[t] for t in tids]),
                         [rec.f, rec.k1][:nin]])
    cam_free = ~fixed

    def unpack(x):
        cams = x[:6 * ncam].reshape(ncam, 6).copy()
        cams[fixed] = x0[:6 * ncam].reshape(ncam, 6)[fixed]
        pts = x[6 * ncam:6 * ncam + 3 * npt].reshape(npt, 3)
        f, k1 = (x[-2], x[-1]) if nin else (rec.f, rec.k1)
        return cams, pts, f, k1

    def residuals(x):
        cams, pts, f, k1 = unpack(x)
        uv, z = project(pts[obs_pt], cams[obs_cam, :3], cams[obs_cam, 3:], f, k1, rec.cx, rec.cy)
        res = (uv - obs_uv).ravel()
        res[np.repeat(z <= 0, 2)] = 50.0
        return res

    m = len(obs_uv) * 2
    nobs = len(obs_uv)
    row2 = (2 * np.arange(nobs))[:, None, None] + np.arange(2)[None, :, None]
    free_obs = cam_free[obs_cam]

    def jacobian(x):
        """Analytic derivatives of the reprojection residuals (sparse, one block per view)."""
        cams, pts, f, k1 = unpack(x)
        rv = cams[obs_cam, :3]
        r = rodrigues_many(rv)
        rx = np.einsum("nij,nj->ni", r, pts[obs_pt])
        pc = rx + cams[obs_cam, 3:]
        z = pc[:, 2]
        inv = 1.0 / np.where(np.abs(z) < 1e-9, 1e-9, z)
        xx, yy = pc[:, 0] * inv, pc[:, 1] * inv
        r2 = xx * xx + yy * yy
        d = 1 + k1 * r2
        a11, a12, a22 = f * (d + 2 * k1 * xx * xx), 2 * f * k1 * xx * yy, f * (d + 2 * k1 * yy * yy)
        jp = np.empty((nobs, 2, 3))
        jp[:, 0, 0], jp[:, 0, 1], jp[:, 0, 2] = a11 * inv, a12 * inv, -(a11 * xx + a12 * yy) * inv
        jp[:, 1, 0], jp[:, 1, 1], jp[:, 1, 2] = a12 * inv, a22 * inv, -(a12 * xx + a22 * yy) * inv
        jp[z <= 0] = 0.0
        jx = jp @ r
        jw = jp @ (-_skew(rx) @ _left_jacobian(rv))
        blocks = [(np.concatenate([jw, jp], 2)[free_obs], row2[free_obs],
                   (6 * obs_cam[free_obs])[:, None, None] + np.arange(6)[None, None, :]),
                  (jx, row2, (6 * ncam + 3 * obs_pt)[:, None, None] + np.arange(3)[None, None, :])]
        if nin:
            ji = np.stack([np.stack([d * xx, f * xx * r2], 1), np.stack([d * yy, f * yy * r2], 1)], 1)
            ji[z <= 0] = 0.0
            blocks.append((ji, row2, len(x0) - 2 + np.arange(2)[None, None, :] + 0 * row2))
        data = np.concatenate([b[0].ravel() for b in blocks])
        rows = np.concatenate([np.broadcast_to(b[1], b[0].shape).ravel() for b in blocks])
        cols = np.concatenate([np.broadcast_to(b[2], b[0].shape).ravel() for b in blocks])
        return coo_matrix((data, (rows, cols)), shape=(m, len(x0))).tocsr()

    sol = least_squares(residuals, x0, jac=jacobian, method="trf", x_scale="jac", tr_solver="lsmr",
                        loss="soft_l1", f_scale=loss_scale, max_nfev=max_nfev, verbose=0)
    cams, pts, f, k1 = unpack(sol.x)
    for fi, c in zip(regs, cams):
        rec.frames[fi].rvec = c[:3].copy()
        rec.frames[fi].tvec = c[3:].copy()
    for t, p in zip(tids, pts):
        rec.points[t] = p.copy()
    if nin:
        rec.f, rec.k1 = float(f), float(k1)
    r = residuals(sol.x).reshape(-1, 2)
    return float(np.sqrt(np.median((r ** 2).sum(1))))


def grow(rec, fixed, used=(), min_corr=30, log=print):
    """Register frames best-first: always the unregistered frame that sees the most
    reconstructed points, so a weak link is crossed only once the map around it is built.
    A frame that fails is retried once the points it sees have grown by a fifth. Frames in
    `used` are left alone; cameras in `fixed` stay put in the local adjustments."""
    frames = rec.frames
    frame_tracks = defaultdict(list)
    for tid, tr in enumerate(rec.tracks):
        for fi in tr:
            frame_tracks[fi].append(tid)

    def recount():
        count = np.zeros(len(frames), int)
        for tid in rec.points:
            for fi in rec.tracks[tid]:
                count[fi] += 1
        return count

    count = recount()
    pending = {i for i, f in enumerate(frames) if f.rvec is None and i not in used}
    failed_at = {}
    n_reg = 0
    while pending:
        cands = [fi for fi in pending if count[fi] >= min_corr and count[fi] > 1.2 * failed_at.get(fi, 0)]
        if not cands:
            break
        fi = max(cands, key=lambda k: count[k])
        ok, n_in = register(rec, fi, min_inliers=min_corr)
        if not ok:
            failed_at[fi] = count[fi]
            continue
        pending.discard(fi)
        new = 0
        for tid in frame_tracks[fi]:
            if tid not in rec.points:
                x = triangulate(rec, tid)
                if x is not None:
                    rec.points[tid] = x
                    new += 1
                    for fj in rec.tracks[tid]:
                        count[fj] += 1
        n_reg += 1
        if n_reg % 10 == 0:
            regs = [k for k, f in enumerate(frames) if f.rvec is not None]
            near = [k for k in regs if abs(k - fi) <= 20]
            rms = bundle_adjust(rec, frame_ids=near, fix_frames=fixed, refine_intrinsics=False, max_nfev=20)
            prune(rec)
            count = recount()
            log(f"registered {len(regs)}/{len(frames)} (frame {frames[fi].name}: {n_in} inliers, +{new} pts), "
                f"{len(rec.points)} points, local BA median err {rms:.2f}px")
    return {i for i, f in enumerate(frames) if f.rvec is not None}


def prune(rec, max_reproj=3.0):
    """Drop points with a large reprojection error in any registered view."""
    drop = []
    for t, xyz in rec.points.items():
        for fi, kp in rec.tracks[t].items():
            fr = rec.frames[fi]
            if fr.rvec is None:
                continue
            uv, z = project(xyz[None], fr.rvec[None], fr.tvec[None], rec.f, rec.k1, rec.cx, rec.cy)
            if z[0] <= 0 or np.hypot(*(uv[0] - fr.keypoints[kp])) > max_reproj:
                drop.append(t)
                break
    for t in drop:
        del rec.points[t]
    return len(drop)


def save(rec, out_dir, tag, median_reproj_px):
    """Write <tag>-sfm.npz (cameras, intrinsics, points, observations), a .ply point cloud
    and a .json summary; returns the summary."""
    frames = rec.frames
    tids = list(rec.points)
    xyz = np.array([rec.points[t] for t in tids])
    rgb = np.array([np.median([frames[fi].colours[kp] for fi, kp in rec.tracks[t].items()], axis=0)
                    for t in tids]).astype(np.uint8)
    nview = np.array([sum(frames[fi].rvec is not None for fi in rec.tracks[t]) for t in tids])
    np.savez_compressed(
        out_dir / f"{tag}-sfm.npz", xyz=xyz, rgb=rgb, nview=nview,
        frame_names=np.array([f.name for f in frames]),
        rvecs=np.array([f.rvec if f.rvec is not None else np.full(3, np.nan) for f in frames]),
        tvecs=np.array([f.tvec if f.tvec is not None else np.full(3, np.nan) for f in frames]),
        intrinsics=np.array([rec.f, rec.k1, rec.cx, rec.cy]),
        # observations for later re-adjustment: point index, frame, pixel
        obs=np.array([(k, fi, *frames[fi].keypoints[kp]) for k, t in enumerate(tids)
                      for fi, kp in rec.tracks[t].items() if frames[fi].rvec is not None]),
    )
    with open(out_dir / f"{tag}-sfm.ply", "w") as fh:
        fh.write(f"ply\nformat ascii 1.0\nelement vertex {len(xyz)}\nproperty float x\nproperty float y\n"
                 "property float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
        fh.writelines(f"{p[0]:.5f} {p[1]:.5f} {p[2]:.5f} {c[0]} {c[1]} {c[2]}\n" for p, c in zip(xyz, rgb))
    summary = {"clip": tag, "frames": len(frames), "registered": sum(f.rvec is not None for f in frames),
               "points": len(xyz), "focal_px": rec.f, "k1": rec.k1, "median_reproj_px": median_reproj_px}
    (out_dir / f"{tag}-sfm.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary
