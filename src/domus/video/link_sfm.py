"""Find the similarity that carries one video reconstruction into another's frame.

    domus video link VIDEO VIDEO

The two reconstructions come from different clips of the same place (possibly shot on
different lenses). Each registered frame of the second is quick-matched against every other
registered frame of the first; its three most similar get the full match (ratio test and
fundamental-matrix check). A keypoint used by a 3D point in each model turns a 2D match into
a 3D-3D correspondence. A similarity is fitted to those by RANSAC, then least squares on the
inliers, and written to data/video/<second>-to-<first>.json.
"""

import json
import sys
from collections import Counter

import cv2
import numpy as np

from .. import crs, sfm
from ..sfm_georef import apply, fit_similarity, horn_initial
from . import safe_npz


def video_dir():
    """The site's video work, data/video/."""
    return crs.ROOT / "data" / "video"


def pixel_key(uv):
    return round(float(uv[0]), 2), round(float(uv[1]), 2)


def load_model(tag):
    """Model arrays, plus each registered frame's features and a pixel -> point lookup."""
    d = np.load(video_dir() / f"{tag}-sfm.npz")
    clip = tag.split("-")[0]
    caches = {}

    def cache(name):
        key = name.split("/")[0] if "/" in name else clip
        if key not in caches:
            path = video_dir() / "cache" / (f"{key}-extra-step1.npz" if "/" in name else f"{key}-step1-w6.npz")
            c = safe_npz.load(path)
            caches[key] = {str(n): (k, des) for n, k, des in zip(c["names"], c["kps"], c["des"])}
        return caches[key][name]

    names = [str(n) for n in d["frame_names"]]
    lookup = [{} for _ in names]
    for k, fi, u, v in d["obs"]:
        lookup[int(fi)][pixel_key((u, v))] = int(k)
    frames = []
    for i, name in enumerate(names):
        if np.all(np.isfinite(d["rvecs"][i])):
            kps, des = cache(name)
            frames.append({"name": name, "kps": kps, "des": des, "points": lookup[i]})
    return d, frames


def main():
    a_tag, b_tag = sys.argv[1], sys.argv[2]
    a, fa = load_model(a_tag)
    b, fb = load_model(b_tag)
    print(f"{a_tag}: {len(fa)} registered frames; {b_tag}: {len(fb)}", flush=True)
    flann = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 4}, {"checks": 32})
    votes = Counter()
    pair_count = 0
    for fr in fb[::2]:
        q = fr["des"][:2000]
        scores = []
        for j, other in enumerate(fa[::2]):
            knn = flann.knnMatch(q, other["des"][:2000], k=2)
            scores.append((sum(1 for m in knn if len(m) == 2 and m[0].distance < 0.75 * m[1].distance), 2 * j))
        for score, j in sorted(scores, reverse=True)[:3]:
            if score < 25:
                continue
            m = sfm.match_pair(sfm.Frame(fr["name"], fr["kps"], fr["des"], None),
                               sfm.Frame(fa[j]["name"], fa[j]["kps"], fa[j]["des"], None))
            n3d = 0
            for ib, ia in m:
                kb = fr["points"].get(pixel_key(fr["kps"][ib]))
                ka = fa[j]["points"].get(pixel_key(fa[j]["kps"][ia]))
                if ka is not None and kb is not None:
                    votes[(ka, kb)] += 1
                    n3d += 1
            if n3d:
                pair_count += 1
                print(f"  {fr['name']} ~ {fa[j]['name']}: {len(m)} matches, {n3d} with 3D points", flush=True)
    pairs = np.array(list(votes))
    print(f"{pair_count} frame pairs, {len(pairs)} distinct 3D-3D correspondences")
    if len(pairs) < 10:
        print("too few correspondences to link the models")
        return
    pa, pb = a["xyz"][pairs[:, 0]], b["xyz"][pairs[:, 1]]
    tol = 0.03 * np.median(np.linalg.norm(pa - np.median(pa, 0), axis=1))
    rng = np.random.default_rng(0)
    best_in = None
    for _ in range(5000):
        idx = rng.choice(len(pa), 3, replace=False)
        try:
            p = horn_initial(pb[idx], pa[idx])
        except np.linalg.LinAlgError:
            continue
        if not np.all(np.isfinite(p)):
            continue
        inl = np.linalg.norm(apply(p, pb) - pa, axis=1) < tol
        if best_in is None or inl.sum() > best_in.sum():
            best_in = inl
    p, _ = fit_similarity(pb[best_in], pa[best_in], initial=horn_initial(pb[best_in], pa[best_in]))
    err = np.linalg.norm(apply(p, pb) - pa, axis=1)
    inl = err < tol
    out = {"from": b_tag, "to": a_tag, "params": p.tolist(), "scale": float(np.exp(p[0])),
           "correspondences": len(pairs), "inliers": int(inl.sum()), "tolerance": float(tol),
           "median_error": float(np.median(err[inl])), "frame_pairs": pair_count}
    (video_dir() / f"{b_tag}-to-{a_tag}.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out))


if __name__ == "__main__":
    main()
