"""Link two video reconstructions through points marked in both clips' frames.

    domus video link-picks VIDEO data/video/picks/VIDEO.json \
        VIDEO data/video/picks/VIDEO.json

Features with the same id in the two picks files are the same physical point. Each is
triangulated in its own reconstruction (from the frames registered there), and a similarity
carrying the second reconstruction into the first is fitted to the pairs: all subsets of
three are tried and the one with most pairs within 5 % of the scene size wins, then least
squares on those. Writes data/video/<second>-to-<first>.json, which
`domus video place <second> --via <first>` reads.
"""

import argparse
import itertools
import json
from pathlib import Path

import numpy as np

from .sfm_georef import apply, fit_similarity, horn_initial, load, triangulate_pixels


def video_dir(site):
    """The site's video work, data/video/."""
    return site.path / "data" / "video"


def triangulated(site, tag, picks_path):
    """Feature id -> xyz in the reconstruction, for features seen in two or more registered frames."""
    rec = load(video_dir(site) / f"{tag}-sfm.npz")
    clip = tag.split("-")[0]
    registered = {n for n, rv in zip(rec["names"], rec["rvecs"]) if np.all(np.isfinite(rv))}
    out = {}
    for f in json.loads(Path(picks_path).read_text(encoding="utf-8"))["features"]:
        obs = []
        for o in f["obs"]:
            name = o["frame"].split("/", 1)[1] if o["frame"].startswith(clip + "/") else o["frame"]
            if name in registered:
                obs.append({"frame": name, "px": o["px"]})
        if len(obs) >= 2:
            out[f["id"]] = triangulate_pixels(rec, obs)
    return out


def main(site):
    ap = argparse.ArgumentParser(prog="domus video link-picks", description=__doc__.split("\n\n")[0])
    ap.add_argument("a_tag", metavar="TAG_A", help="the reconstruction whose frame the other is carried into")
    ap.add_argument("a_picks", metavar="PICKS_A", help="points marked in its clip's frames, a picks file")
    ap.add_argument("b_tag", metavar="TAG_B", help="the reconstruction carried into it")
    ap.add_argument("b_picks", metavar="PICKS_B", help="the same points marked in its clip's frames")
    args = ap.parse_args()
    a_tag, a_picks, b_tag, b_picks = args.a_tag, args.a_picks, args.b_tag, args.b_picks
    pa, pb = triangulated(site, a_tag, a_picks), triangulated(site, b_tag, b_picks)
    ids = sorted(pa.keys() & pb.keys())
    print(f"{len(ids)} shared features: {ids}")
    if len(ids) < 3:
        print("need at least three shared features")
        return
    A = np.array([pa[i] for i in ids])
    B = np.array([pb[i] for i in ids])
    tol = 0.05 * np.median(np.linalg.norm(A - A.mean(0), axis=1))
    best = None
    for tri in itertools.combinations(range(len(ids)), 3):
        try:
            p = horn_initial(B[list(tri)], A[list(tri)])
        except np.linalg.LinAlgError:
            continue
        if not np.all(np.isfinite(p)):
            continue
        err = np.linalg.norm(apply(p, B) - A, axis=1)
        n_in = int((err < tol).sum())
        if best is None or n_in > best[0] or (n_in == best[0] and err[err < tol].sum() < best[2]):
            best = (n_in, err < tol, err[err < tol].sum())
    inl = best[1]
    p, _ = fit_similarity(B[inl], A[inl], initial=horn_initial(B[inl], A[inl]))
    err = np.linalg.norm(apply(p, B) - A, axis=1)
    for i, e, k in zip(ids, err, inl):
        print(f"  {i:<16} error {e:.4f} {'' if k else '(left out)'}")
    out = {"from": b_tag, "to": a_tag, "params": p.tolist(), "scale": float(np.exp(p[0])),
           "features": ids, "inliers": int(inl.sum()), "tolerance": float(tol),
           "median_error": float(np.median(err[inl])), "method": "shared marked points"}
    (video_dir(site) / f"{b_tag}-to-{a_tag}.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "params"}))


if __name__ == "__main__":
    from ..project.site import started

    main(started())       # run by itself, on the site the environment names
