"""Dense point cloud from a placed video reconstruction, by two-view stereo on frame pairs.

    domus video dense VIDEO [--every 2] [--scale 0.5]

For each registered frame (every --every-th), the partner is the frame whose camera moved
sideways by 6-20 % of the median distance to the scene, looking the same way within 25
degrees. The pair is rectified with the reconstructed poses and intrinsics (OpenCV
stereoRectify), matched with semi-global block matching on images scaled by --scale (with
OpenCV's left-right check), and triangulated. Points are taken to site coordinates with the
model's placement (data/video/<tag>-georef.json) and merged on a 4 cm grid; a cell is kept
only if two or more pairs put points in it, at the mean of those points. Writes data/video/<tag>-dense.npz (xyz in
site coordinates on the site's grid and height datum, rgb, number of pairs per cell).
"""

import argparse
import json
import time

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

from ..crs import ROOT
from ..sfm_georef import apply, load

VID = ROOT / "data" / "video"


T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)


def frame_path(tag, name):
    clip, stem = (name.split("/", 1) if "/" in name else (tag.split("-")[0], name))
    return ROOT / "sources" / "raw" / "frames6" / clip / f"{stem}.jpg"


def stereo_pair(img_a, img_b, k, dist, ra, ta, rb, tb, scale, z_near, z_far):
    """Points (camera-a frame, model units) and colours from one rectified, sideways pair."""
    h, w = img_a.shape[:2]
    r = rb @ ra.T                     # camera a -> camera b
    t = tb - r @ ta
    r1, r2, *_ = cv2.stereoRectify(k, dist, k, dist, (w, h), r, t.reshape(3, 1))
    # Both rectified views keep the original focal length, centred (stereoRectify's own choice
    # zooms far in when the views are rotated against each other).
    fx = k[0, 0] * scale
    size = (int(w * scale), int(h * scale))
    k_new = np.array([[fx, 0, size[0] / 2], [0, fx, size[1] / 2], [0, 0, 1]])
    ma = cv2.initUndistortRectifyMap(k, dist, r1, k_new, size, cv2.CV_32FC1)
    mb = cv2.initUndistortRectifyMap(k, dist, r2, k_new, size, cv2.CV_32FC1)
    a = cv2.remap(img_a, *ma, cv2.INTER_LINEAR)
    b = cv2.remap(img_b, *mb, cv2.INTER_LINEAR)
    c2 = r1 @ (-r.T @ t)                 # camera b's centre in rectified camera a's frame
    base = float(np.linalg.norm(c2))
    dmax = int(min(256, max(32, np.ceil(fx * base / z_near / 16) * 16)))
    ga, gb = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    sgbm = cv2.StereoSGBM_create(minDisparity=0, numDisparities=dmax, blockSize=5, P1=8 * 25, P2=32 * 25,
                                 disp12MaxDiff=1, uniquenessRatio=12, speckleWindowSize=80, speckleRange=2,
                                 mode=cv2.STEREO_SGBM_MODE_SGBM)
    if c2[0] > 0:         # b to the right of a: the usual left/right order
        disp = sgbm.compute(ga, gb).astype(np.float32) / 16
    else:                 # b to the left: mirror both so a is still the left image
        disp = cv2.flip(sgbm.compute(cv2.flip(ga, 1), cv2.flip(gb, 1)), 1).astype(np.float32) / 16
    v, u = np.nonzero(disp > 0.5)
    z = fx * base / disp[v, u]
    keep = (z > z_near) & (z < z_far)
    v, u, z = v[keep], u[keep], z[keep]
    x = (u - k_new[0, 2]) * z / fx
    y = (v - k_new[1, 2]) * z / fx
    pts_a = np.column_stack([x, y, z]) @ r1        # rectified camera a -> camera a
    return pts_a, a[v, u][:, ::-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--every", type=int, default=2)
    ap.add_argument("--scale", type=float, default=0.5)
    args = ap.parse_args()
    rec = load(VID / f"{args.tag}-sfm.npz")
    params = np.array(json.loads((VID / f"{args.tag}-georef.json").read_text())["params"])
    s_site = float(np.exp(params[0]))
    f, k1, cx, cy = rec["intr"]
    k = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1]])
    dist = np.array([k1, 0, 0, 0, 0], float)
    names = rec["names"]
    reg = [i for i, rv in enumerate(rec["rvecs"]) if np.all(np.isfinite(rv))]
    rots = {i: Rotation.from_rotvec(rec["rvecs"][i]).as_matrix() for i in reg}
    centres = {i: -rots[i].T @ rec["tvecs"][i] for i in reg}
    xyz = rec["xyz"]
    near, median = {}, {}
    for i in reg:
        pc = xyz @ rots[i].T + rec["tvecs"][i]
        z = pc[:, 2]
        uv = pc[:, :2] / np.maximum(z, 1e-9)[:, None] * f + [cx, cy]
        vis = (z > 0) & (uv[:, 0] > 0) & (uv[:, 0] < 2 * cx) & (uv[:, 1] > 0) & (uv[:, 1] < 2 * cy)
        if vis.sum() > 50:
            near[i], median[i] = float(np.percentile(z[vis], 3)), float(np.median(z[vis]))
    cell = 0.04
    parts = []          # per pair: its cells, and the sums of position and colour in each
    for i in reg[::args.every]:
        if i not in median:
            continue
        best = None
        for j in reg:
            if j == i:
                continue
            d = rots[i] @ (centres[j] - centres[i])          # baseline in camera a's axes
            blen = np.linalg.norm(d)
            ang = np.degrees(np.arccos(np.clip(rots[i][2] @ rots[j][2], -1, 1)))
            if not (0.06 * median[i] <= blen <= 0.2 * median[i]) or ang > 25:
                continue
            if abs(d[0]) < 2 * abs(d[1]) or abs(d[0]) < 2 * abs(d[2]):   # sideways, not up or forward
                continue
            score = -abs(blen - 0.12 * median[i]) / median[i] - 0.005 * ang
            if best is None or score > best[0]:
                best = (score, j)
        if best is None:
            continue
        j = best[1]
        img_a = cv2.imread(str(frame_path(args.tag, names[i])))
        img_b = cv2.imread(str(frame_path(args.tag, names[j])))
        pts, cols = stereo_pair(img_a, img_b, k, dist, rots[i], rec["tvecs"][i], rots[j], rec["tvecs"][j],
                                args.scale, 0.7 * near[i], 4 * median[i])
        site = apply(params, (pts - rec["tvecs"][i]) @ rots[i])     # camera a -> model -> site
        keys, inv = np.unique(np.floor(site / cell).astype(np.int64), axis=0, return_inverse=True)
        inv = inv.ravel()
        n = np.bincount(inv)
        sums = np.column_stack([np.bincount(inv, weights=site[:, c] - site[0, c]) for c in range(3)])
        csum = np.column_stack([np.bincount(inv, weights=cols[:, c].astype(float)) for c in range(3)])
        parts.append((keys, n, sums + n[:, None] * site[0], csum))
        log(f"pair {names[i]} + {names[j]}: baseline {np.linalg.norm(centres[j] - centres[i]) * s_site:.2f} m, "
            f"{len(site)} points in {len(keys)} cells")
    # A cell counts when two or more pairs put points in it; its point is their mean position.
    keys = np.vstack([p[0] for p in parts])
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    inv = inv.ravel()
    n_pairs = np.bincount(inv)
    n_pts = np.bincount(inv, weights=np.concatenate([p[1] for p in parts]))
    pos = np.column_stack([np.bincount(inv, weights=np.concatenate([p[2][:, c] for p in parts])) for c in range(3)])
    col = np.column_stack([np.bincount(inv, weights=np.concatenate([p[3][:, c] for p in parts])) for c in range(3)])
    keep = n_pairs >= 2
    xyz_out = pos[keep] / n_pts[keep, None]
    rgb_out = (col[keep] / n_pts[keep, None]).clip(0, 255).astype(np.uint8)
    np.savez_compressed(VID / f"{args.tag}-dense.npz", xyz=xyz_out, rgb=rgb_out, pairs=n_pairs[keep])
    log(f"wrote {args.tag}-dense.npz: {keep.sum()} of {len(uniq)} {cell * 100:.0f} cm cells seen by 2+ "
        f"of {len(parts)} pairs")


if __name__ == "__main__":
    main()
