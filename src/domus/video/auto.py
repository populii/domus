"""From a scan to the model, with no hand work: `domus video auto sources/raw/scan-<time>.webm`.

Pulls out the frames and reconstructs them. If the scan's AR positions are beside the video (scan-<time>-poses.json), the
reconstruction is fitted to them, which gives it its scale and its place on the grid, then the whole cloud is moved up or down to sit on
the LiDAR ground. It's saved as data/video/<clip>-scan.npz, then exported and built.

The AR positions start from where the person said stop 1 was, and they drift as you walk, so this placing is rough: expect
something like a metre, and check it against the model. `domus video place` with control points is the way to pin one down. A scan
without AR positions stops after the reconstruction, since nothing says where it is.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation

from .. import crs, terrain
from ..sfm_georef import apply, fit_similarity, load


def video_dir():
    """The site's video work, data/video/."""
    return crs.ROOT / "data" / "video"
MIN_MATCHED = 8
MIN_SPREAD_M = 3.0
EYE_HEIGHT_M = 1.4
WARN_RESIDUAL_M = 1.0
MAX_GROUND_SHIFT_M = 4.0
MAIN_LENS = 0.76     # a phone's main camera: focal length in pixels over the image's width


def step(module, *args):
    """A step's own main, as if run by itself."""
    argv = sys.argv
    try:
        sys.argv = [module.__name__, *map(str, args)]
        module.main()
    finally:
        sys.argv = argv


def camera_centres(rec):
    """{seconds into the clip: centre} of each registered frame, from names like t012.345."""
    out = {}
    for name, rv, tv in zip(rec["names"], rec["rvecs"], rec["tvecs"]):
        m = re.fullmatch(r"t(\d+\.\d+)", str(name))
        if m and np.all(np.isfinite(rv)):
            out[float(m.group(1))] = -Rotation.from_rotvec(rv).as_matrix().T @ tv
    return out


def pose_track(poses, origin, ground):
    """(seconds, site positions) from the scan page's record: viewer metres east and south, and height against the start."""
    frames = poses["frames"]
    t = (np.array([f["t"] for f in frames], float) - poses["started"]) / 1000.0
    e = origin[0] + np.array([f["x"] for f in frames], float)
    n = origin[1] - np.array([f["z"] for f in frames], float)
    h0 = float(ground(e[:1], n[:1])[0]) + EYE_HEIGHT_M
    return t, np.column_stack([e, n, h0 + np.array([f["y"] for f in frames], float)])


def match(centres, t, pos):
    """Reconstruction centres against the positions at the same moments."""
    times = sorted(centres)
    times = [s for s in times if t[0] <= s <= t[-1]]
    if not times:
        return np.empty((0, 3)), np.empty((0, 3))
    site = np.column_stack([np.interp(times, t, pos[:, k]) for k in range(3)])
    return np.array([centres[s] for s in times]), site


def fit_to_track(centres, t, pos):
    """The similarity from the reconstruction to the site, and its residuals (m). None if the walk is too short to fix it."""
    a, b = match(centres, t, pos)
    if len(a) < MIN_MATCHED or np.ptp(b[:, :2], axis=0).max() < MIN_SPREAD_M:
        return None, None
    params, res = fit_similarity(a, b)
    return params, np.linalg.norm(res, axis=1)


def ground_shift(xyz, ground, cell=0.5):
    """How far to raise a cloud so its lowest points per cell lie on the ground: the median over cells, or 0."""
    key = np.floor(xyz[:, :2] / cell).astype(np.int64)
    order = np.lexsort((xyz[:, 2], key[:, 1], key[:, 0]))
    key, pts = key[order], xyz[order]
    starts = np.r_[0, np.flatnonzero(np.any(key[1:] != key[:-1], axis=1)) + 1]
    low = pts[starts]
    g = ground(low[:, 0], low[:, 1])
    d = g - low[:, 2]
    d = d[np.isfinite(d)]
    return float(np.median(d)) if len(d) >= 10 else 0.0


def missing_clouds(cfg):
    """Clouds the site lists that aren't here, which `export` would need to rebuild the point cloud."""
    return [t for t in cfg.get("video", {}).get("clouds", []) if not (video_dir() / f"{t}.npz").exists()]


def main():
    from . import colmap_sfm, export_cloud, extract_frames

    ap = argparse.ArgumentParser(prog="domus video auto", description="From a scan to the model, with no hand work.")
    ap.add_argument("video")
    args = ap.parse_args()
    video = Path(args.video)
    if not video.is_absolute():
        video = crs.ROOT / video
    if not video.exists():
        raise SystemExit(f"No such video: {video}")
    clip = video.stem
    poses_file = video.with_name(f"{clip}-poses.json")
    poses = json.loads(poses_file.read_text(encoding="utf-8")) if poses_file.exists() else None

    print("Pulling out the frames", flush=True)
    step(extract_frames, video)
    first = next(iter(sorted((crs.ROOT / "sources" / "raw" / "frames6" / clip).glob("*.jpg"))), None)
    if first is None:
        raise SystemExit("No frames came out of that video.")
    width = Image.open(first).size[0]
    focal = (poses or {}).get("focal_px") or MAIN_LENS * width
    print(f"Reconstructing, from a focal length of {focal:.0f} px. This is the slow part.", flush=True)
    step(colmap_sfm, clip, "--focal", f"{focal:.1f}", "--tag", clip)
    if not poses or not poses.get("frames"):
        print("Reconstructed. This scan has no AR positions, so nothing says where it is: place it with `domus video place`, "
              "as docs/video.md explains.")
        return

    cfg = crs.settings()
    g = terrain.ground()
    ground = lambda e, n: g.sample(np.atleast_1d(e), np.atleast_1d(n))
    from ..build import site_origin

    t, pos = pose_track(poses, site_origin(cfg, g), ground)
    rec = load(video_dir() / f"{clip}-sfm.npz")
    params, res = fit_to_track(camera_centres(rec), t, pos)
    if params is None:
        print("The walk was too short, or too few frames were reconstructed, to fix its place. Place it with `domus video place`.")
        return
    print(f"Fitted to the AR track: scale {np.exp(params[0]):.3f}, camera positions off by {np.median(res):.2f} m median, {res.max():.2f} m at most")
    if np.median(res) > WARN_RESIDUAL_M:
        print("warning: the AR track and the reconstruction disagree. Check this cloud against the model before relying on it.")
    xyz = apply(params, rec["xyz"])
    dz = ground_shift(xyz, ground)
    if abs(dz) > MAX_GROUND_SHIFT_M:
        print(f"warning: the cloud would need moving {dz:+.1f} m to meet the ground, so it's left where it is. It may not be where the AR track says.")
        dz = 0.0
    xyz[:, 2] += dz
    print(f"Moved {dz:+.2f} m to sit on the ground")
    np.savez_compressed(video_dir() / f"{clip}-scan.npz", xyz=xyz, rgb=rec["rgb"], nview=rec["nview"])
    (video_dir() / f"{clip}-scan.json").write_text(json.dumps({"params": params.tolist(), "dz_m": dz, "camera_residual_median_m": float(np.median(res)),
                                                       "source": "AR track, rough"}, indent=1), encoding="utf-8")

    gone = missing_clouds(cfg)
    if gone:
        print(f"Saved {clip}-scan.npz, but this site lists clouds that aren't here ({gone[0]} and others), so the model's point cloud wasn't "
              "rebuilt. Run `domus video export` and `domus build` where they are.")
        return
    print("Adding it to the model", flush=True)
    step(export_cloud)
    from .. import build

    crs.settings.cache_clear()
    build.main()
    print("Done. The scan is in the model's point cloud layer.")
