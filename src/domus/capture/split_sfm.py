"""Cut a time span out of a video reconstruction as its own model (same frame, cameras and points).

    domus video split VIDEO-m2 VIDEO-m2-202-244 202 244

Keeps the registered frames with t0 <= t <= t1 (the others become unregistered) and the points
observed in two or more of them (nview counts only those frames). Writes data/video/<new>-sfm.npz
and -sfm.json; nothing is re-adjusted. Used where a long model bends or changes scale along the
walk, so that each piece can be placed on the site grid by itself (tools/georef_sfm.py).
"""

import json

import numpy as np


def video_dir(site):
    """The site's video work, data/video/."""
    return site.path / "data" / "video"


def seconds(name):
    """Frame time from a frame name such as t012.500 (or clip/t012.500)."""
    return float(str(name).rsplit("/", 1)[-1][1:])


def split(site, src, new, t0, t1):
    """`domus video split`: the frames from `t0` to `t1` seconds of reconstruction `src` as one of their own, `new`,
    with the points two or more of them see; nothing is adjusted again. Gives its summary."""
    vid = video_dir(site)
    d = np.load(vid / f"{src}-sfm.npz")
    t = np.array([seconds(n) for n in d["frame_names"]])
    rv, tv = d["rvecs"].copy(), d["tvecs"].copy()
    keepf = np.all(np.isfinite(rv), axis=1) & (t >= t0) & (t <= t1)
    rv[~keepf] = np.nan
    tv[~keepf] = np.nan
    obs = d["obs"][keepf[d["obs"][:, 1].astype(int)]]
    cnt = np.bincount(obs[:, 0].astype(int), minlength=len(d["xyz"]))
    keepp = cnt >= 2
    newidx = -np.ones(len(d["xyz"]), int)
    newidx[keepp] = np.arange(keepp.sum())
    obs = obs[keepp[obs[:, 0].astype(int)]]
    obs[:, 0] = newidx[obs[:, 0].astype(int)]
    np.savez_compressed(vid / f"{new}-sfm.npz", xyz=d["xyz"][keepp], rgb=d["rgb"][keepp], nview=cnt[keepp],
                        frame_names=d["frame_names"], rvecs=rv, tvecs=tv, intrinsics=d["intrinsics"], obs=obs)
    src_json = json.loads((vid / f"{src}-sfm.json").read_text(encoding="utf-8"))
    summary = {"clip": new, "frames": len(t), "registered": int(keepf.sum()), "points": int(keepp.sum()),
               "focal_px": src_json["focal_px"], "k1": src_json["k1"],
               "median_reproj_px": src_json["median_reproj_px"], "cut_from": src, "span_s": [t0, t1],
               "note": f"Frames {t0:.1f}-{t1:.1f} s of {src} with the points seen in two or more of them; "
                       "poses and points unchanged (not re-adjusted)."}
    (vid / f"{new}-sfm.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    return summary
