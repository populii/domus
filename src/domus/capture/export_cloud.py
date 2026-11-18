"""Combine the georeferenced video point clouds into one file for the model and the viewer.

    domus video export

Reads the clouds listed under [video] clouds in data/site.toml (data/video/<name>.npz: a
placed reconstruction <tag>-site, or a dense stereo cloud <tag>-dense) and every LiDAR scan
(<tag>-scan.npz, from `domus scan`), keeps the points
inside the model extent and within 8 m (in plan) of the cloud's cameras (and inside the box
under [video.keep], where a cloud has one; from a reconstruction, only those seen in three or
more frames), thins
them to one point per 2 cm cell (the best supported), and writes
data/video/points.npz: millimetre offsets from the model origin (int32 east, north, height),
colours (uint8) and the index of the source reconstruction.

The open questions under [[video.compare]] go to data/video/compare.npz the same way, one set per
entry (`set` indexes `ids`): its clouds moved by `dz_m` and turned by `turn_deg` (counter-clockwise
seen from above) about `pivot` (E, N), so the viewer can show two placements of one clip side by side.
"""

import numpy as np
from scipy.spatial import cKDTree

REACH_M = 8.0      # keep points within this plan distance of one of the cloud's cameras


def load(site, tag, cfg):
    """A cloud's points inside the model extent, its keep box and reach: (xyz, rgb, support)."""
    e0, n0, e1, n1 = cfg["model"]["extent"]
    d = np.load(site.path / "data" / "video" / f"{tag}.npz")
    xyz = d["xyz"]
    support = d["nview"] if "nview" in d else d["pairs"]
    keep = (xyz[:, 0] > e0) & (xyz[:, 0] < e1) & (xyz[:, 1] > n0) & (xyz[:, 1] < n1)
    box = cfg.get("video", {}).get("keep", {}).get(tag)   # [E min, N min, E max, N max]: only this part is trusted
    if box:
        keep &= (xyz[:, 0] > box[0]) & (xyz[:, 1] > box[1]) & (xyz[:, 0] < box[2]) & (xyz[:, 1] < box[3])
    # Depth from a video gets worse with distance: keep points within reach of the cameras.
    if not tag.endswith("-scan"):   # a LiDAR scan measures depth directly and has no cameras
        cams = np.load(site.path / "data" / "video" / f"{tag.rsplit('-', 1)[0]}-site.npz")["cameras"]
        near = cKDTree(cams[:, :2]).query(xyz[:, :2], distance_upper_bound=REACH_M)[0]
        keep &= np.isfinite(near)
    if "nview" in d:
        keep &= d["nview"] >= 3
    print(f"{tag}: {keep.sum()} points")
    return xyz[keep], d["rgb"][keep], support[keep]


def thin(pts, views, origin):
    """Indices of the best supported point in each 2 cm cell."""
    cell = np.floor((pts - origin) / 0.02).astype(np.int64)
    order = np.lexsort((-views, cell[:, 2], cell[:, 1], cell[:, 0]))
    cs = cell[order]
    first = np.r_[True, np.any(cs[1:] != cs[:-1], axis=1)]
    return order[first]


def moved(xyz, entry):
    """The points turned about the entry's pivot and moved up by its dz_m."""
    out = np.array(xyz, float)
    a = np.radians(entry.get("turn_deg", 0.0))
    if a:
        c = np.asarray(entry["pivot"], float)
        rot = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        out[:, :2] = (out[:, :2] - c) @ rot.T + c
    out[:, 2] += entry.get("dz_m", 0.0)
    return out


def export(site):
    """The clouds in [video] clouds and the LiDAR scans, as data/video/points.npz, and [[video.compare]]'s sets as
    data/video/compare.npz."""
    cfg = site.settings()
    tags = [*cfg.get("video", {}).get("clouds", []), *sorted(p.stem for p in (site.path / "data" / "video").glob("*-scan.npz"))]
    origin = np.array([*cfg["model"]["origin"], 0.0][:3], float)   # a site may give no height: any origin does here
    pts, cols, views, src = [], [], [], []
    for k, tag in enumerate(tags):
        xyz, rgb, support = load(site, tag, cfg)
        pts.append(xyz)
        cols.append(rgb)
        views.append(support)
        src.append(np.full(len(xyz), k, np.uint8))
    pts, cols, views, src = (np.concatenate(a) for a in (pts, cols, views, src))
    sel = thin(pts, views, origin)
    mm = np.round((pts[sel] - origin) * 1000).astype(np.int32)
    np.savez_compressed(site.path / "data" / "video" / "points.npz", mm=mm, rgb=cols[sel].astype(np.uint8),
                        source=src[sel], tags=np.array(tags), origin=origin)
    print(f"wrote data/video/points.npz: {len(sel)} points from {len(tags)} reconstructions")

    entries = cfg.get("video", {}).get("compare", [])
    if not entries:
        return
    mms, rgbs, sets = [], [], []
    for k, entry in enumerate(entries):
        parts = [load(site, tag, cfg) for tag in entry["clouds"]]
        xyz = moved(np.concatenate([p[0] for p in parts]), entry)
        rgb = np.concatenate([p[1] for p in parts])
        support = np.concatenate([p[2] for p in parts])
        sel = thin(xyz, support, origin)
        mms.append(np.round((xyz[sel] - origin) * 1000).astype(np.int32))
        rgbs.append(rgb[sel].astype(np.uint8))
        sets.append(np.full(len(sel), k, np.uint8))
        print(f"{entry['id']}: {len(sel)} points")
    np.savez_compressed(site.path / "data" / "video" / "compare.npz", mm=np.concatenate(mms), rgb=np.concatenate(rgbs),
                        set=np.concatenate(sets), ids=np.array([e["id"] for e in entries]), origin=origin)
    print(f"wrote data/video/compare.npz: {len(entries)} sets")
