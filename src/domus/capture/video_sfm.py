"""Reconstruct a walk round the site in 3D from its video.

    domus video reconstruct-chain VIDEO [--step 1] [--window 6] [--workers 8]

Reads frames from sources/raw/frames6/<clip>/ (the sharpest frame per 1/6 s, from
tools/extract_frames.py) and writes data/video/<clip>-sfm.npz (camera poses, intrinsics,
points) and a .ply point cloud. The reconstruction is in its own frame and scale;
tools/georef_sfm.py places it on the site grid. For a slow walk, --step 3 (2 frames per
second) is enough. Features and matching run in --workers processes; finding the places the
walk revisits uses a global descriptor per frame, so it grows only gently with clip length.
"""

import json
import os
import time

import cv2
import numpy as np

from . import safe_npz, sfm

T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)


def choose_initial_pair(rec, matches, lo=4, hi=15, want=300, exclude=()):
    """Try candidate pairs (most matches with real parallax first); keep the first that
    triangulates at least `want` points."""
    cands = []
    for (i, j), m in matches.items():
        if not (lo <= j - i <= hi) or len(m) < 150 or i in exclude or j in exclude:
            continue
        a = rec.frames[i].keypoints[m[:, 0]]
        b = rec.frames[j].keypoints[m[:, 1]]
        flow = float(np.median(np.hypot(*(a - b).T)))
        if flow < 25:
            continue
        cands.append((len(m) * min(flow, 150), i, j))
    best = None
    for _, i, j in sorted(cands, reverse=True)[:40]:
        m = matches[(i, j)]
        a = rec.frames[i].keypoints[m[:, 0]]
        b = rec.frames[j].keypoints[m[:, 1]]
        e, mask = cv2.findEssentialMat(a, b, sfm.kmatrix(rec), cv2.USAC_MAGSAC, 0.999, 1.0)
        if e is None or e.shape != (3, 3):
            continue
        _, r, t, _ = cv2.recoverPose(e, a, b, sfm.kmatrix(rec), mask=mask.copy())
        rec.frames[i].rvec, rec.frames[i].tvec = np.zeros(3), np.zeros(3)
        rec.frames[j].rvec, rec.frames[j].tvec = cv2.Rodrigues(r)[0].ravel(), t.ravel()
        shared = [tid for tid, tr in enumerate(rec.tracks) if i in tr and j in tr]
        pts = {tid: x for tid in shared if (x := sfm.triangulate(rec, tid, min_angle_deg=2.0)) is not None}
        log(f"  candidate {rec.frames[i].name}/{rec.frames[j].name}: {len(m)} matches, "
            f"{len(shared)} shared tracks, {len(pts)} points")
        if best is None or len(pts) > len(best[2]):
            best = (i, j, pts, rec.frames[j].rvec.copy(), rec.frames[j].tvec.copy())
        rec.frames[i].rvec = rec.frames[i].tvec = rec.frames[j].rvec = rec.frames[j].tvec = None
        if len(pts) >= want:
            break
    return best


def reconstruct(site, clip, step=1, window=6, focal=1100.0, k1=0.0, fix_intrinsics=False, extra=(), extra_step=3, range_="",
                tag="", loops=True, loop_candidates=12, workers=None):
    """`domus video reconstruct-chain`: the clip reconstructed with the built-in chain of views, as
    data/video/<tag>-sfm.npz and .ply (and <tag>-m2 and on); the module's docstring says how. `range_` is
    "first:last", the main clip's frames to use; the tag is the clip, and the range, unless given. `workers` is
    the processes for features and matching, all but four of the machine's unless given."""
    workers = workers or max(1, (os.cpu_count() or 2) - 4)
    extra = [c for c in extra if c]

    src = site.path / "sources" / "raw" / "frames6" / clip
    paths = sorted(src.glob("*.jpg"))[::step]
    log(f"{len(paths)} frames from {src.relative_to(site.path)}")
    cache = site.path / "data" / "video" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    cfile = cache / f"{clip}-step{step}-w{window}.npz"
    if cfile.exists():
        c = safe_npz.load(cfile)
        frames = [sfm.Frame(n, k, d, col) for n, k, d, col in zip(c["names"], c["kps"], c["des"], c["cols"])]
        matches = c["matches"].item()
        log(f"loaded features and matches from {cfile.name}")
    else:
        frames = sfm.load_features(paths, workers=workers)
        matches = None
    log(f"features: median {int(np.median([len(f.keypoints) for f in frames]))} per frame")
    h, w = cv2.imread(str(paths[0])).shape[:2]
    rec = sfm.Reconstruction(frames, f=focal, k1=k1, cx=w / 2, cy=h / 2)

    if matches is None:
        matches = compute_matches(frames, window, workers, cache)
        np.savez(cfile, names=np.array([f.name for f in frames]),
                 kps=np.array([f.keypoints for f in frames], dtype=object),
                 des=np.array([f.descriptors for f in frames], dtype=object),
                 cols=np.array([f.colours for f in frames], dtype=object),
                 matches=np.array(matches, dtype=object))
    name = clip
    if range_:
        a, b = (int(v) for v in range_.split(":"))
        frames = frames[a:b + 1]
        matches = {(i - a, j - a): m for (i, j), m in matches.items() if a <= i <= b and a <= j <= b}
        rec.frames = frames
        name = f"{clip}-{a}-{b}"
    tag = tag or name
    n_main = len(frames)
    for other in extra:
        add_extra_clip(site, frames, matches, other, extra_step, n_main, cache)
    if loops:
        key = "+".join([clip, *extra])
        matches.update(loop_matches(frames, matches, window, cache / f"{key}-step{step}-loops.npz",
                                    loop_candidates, workers))
    rec.tracks = sfm.build_tracks(len(frames), matches)
    _continue_after_tracks(site, rec, frames, matches, fix_intrinsics, tag, n_main)


def loop_matches(frames, matches, window, cfile, candidates=12, workers=1):
    """Pairs of frames that see the same place at different times (a return to the wall,
    a pan back): each frame's most similar frames outside its window, by a global descriptor
    (domus.capture.sfm.vlad), get the full match. Cached in `cfile`."""
    if cfile.exists():
        found = safe_npz.load(cfile)["matches"].item()
        log(f"loaded {len(found)} revisit pairs from {cfile.name}")
        return found
    desc = sfm.vlad(frames)
    sim = desc @ desc.T
    clip = np.array([f.name.split("/")[0] if "/" in f.name else "" for f in frames])
    idx = np.arange(len(frames))
    near = (clip[:, None] == clip[None, :]) & (np.abs(idx[:, None] - idx[None, :]) <= window)
    sim[near] = -np.inf
    pairs = set()
    for i in range(len(frames)):
        for j in np.argsort(-sim[i])[:candidates]:
            a, b = min(i, int(j)), max(i, int(j))
            if np.isfinite(sim[i, j]) and (a, b) not in matches:
                pairs.add((a, b))
    log(f"revisit search: {len(pairs)} candidate pairs from {len(frames)} frames")
    found = {p: m for p, m in sfm.match_pairs(frames, sorted(pairs), workers, cfile.parent, log).items()
             if len(m) >= 30}
    np.savez(cfile, matches=np.array(found, dtype=object))
    log(f"{len(found)} revisit pairs")
    return found


def add_extra_clip(site, frames, matches, clip, step, n_main, cache):
    """Append another clip's frames: matched within the clip, and each against the most
    similar frames of the main clip (found by a quick match of their strongest features)."""
    paths = sorted((site.path / "sources" / "raw" / "frames6" / clip).glob("*.jpg"))[::step]
    cfile = cache / f"{clip}-extra-step{step}.npz"
    if cfile.exists():
        c = safe_npz.load(cfile)
        extra = [sfm.Frame(n, k, d, col) for n, k, d, col in zip(c["names"], c["kps"], c["des"], c["cols"])]
    else:
        extra = sfm.load_features(paths)
        for f in extra:
            f.name = f"{clip}/{f.name}"
        np.savez(cfile, names=np.array([f.name for f in extra]),
                 kps=np.array([f.keypoints for f in extra], dtype=object),
                 des=np.array([f.descriptors for f in extra], dtype=object),
                 cols=np.array([f.colours for f in extra], dtype=object))
    base = len(frames)
    frames.extend(extra)
    flann = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 4}, {"checks": 32})
    for k, f in enumerate(extra):
        i = base + k
        for d in (1, 2, 3):
            if k + d < len(extra):
                m = sfm.match_pair(f, extra[k + d])
                if len(m) >= 30:
                    matches[(i, i + d)] = m
        scores = []
        q = f.descriptors[:1500]
        for j in range(0, n_main, 2):
            knn = flann.knnMatch(q, frames[j].descriptors[:1500], k=2)
            scores.append((sum(1 for mm in knn if len(mm) == 2 and mm[0].distance < 0.75 * mm[1].distance), j))
        for _, j in sorted(scores, reverse=True)[:4]:
            m = sfm.match_pair(frames[j], f)
            if len(m) >= 30:
                matches[(j, i)] = m
        log(f"extra {f.name}: best main frames {[frames[j].name for _, j in sorted(scores, reverse=True)[:4]]}")


def compute_matches(frames, window, workers=1, scratch=None):
    pairs = sorted({(i, j) for i in range(len(frames))
                    for j in [*range(i + 1, i + 1 + window), i + 10, i + 15] if j < len(frames)})
    log(f"matching {len(pairs)} pairs in {workers} processes")
    matches = {p: m for p, m in sfm.match_pairs(frames, pairs, workers, scratch, log).items() if len(m) >= 30}
    log(f"{len(matches)} matched pairs")
    return matches


def _continue_after_tracks(site, rec, frames, matches, fix_intrinsics, base_tag, n_main):
    lens = np.array([len(t) for t in rec.tracks])
    log(f"{len(rec.tracks)} tracks, median length {np.median(lens):.0f}, >=3 views: {(lens >= 3).sum()}")
    out = site.path / "data" / "video"
    out.mkdir(parents=True, exist_ok=True)

    # Model 1 from the best start pair; if the clip's chain of views breaks, the frames left
    # over start further models (<tag>-m2, -m3 ...), each placed on the site separately.
    used = set()
    for part in range(1, 6):
        for f in frames:
            f.rvec = f.tvec = None
        rec.points = {}
        start = choose_initial_pair(rec, matches, exclude=used)
        if start is None:
            break
        i0, j0, pts, rv, tv = start
        frames[i0].rvec, frames[i0].tvec = np.zeros(3), np.zeros(3)
        frames[j0].rvec, frames[j0].tvec = rv, tv
        rec.points.update(pts)
        log(f"model {part}: initial pair {frames[i0].name} / {frames[j0].name}: {len(rec.points)} points")
        sfm.bundle_adjust(rec, refine_intrinsics=False)
        regs = sfm.grow(rec, [i0], used, log=log)
        if len(regs) < 8:
            log(f"model {part}: only {len(regs)} frames, discarded")
            used |= regs
            continue
        log(f"model {part}: global bundle adjustment of {len(regs)} frames")
        for it in range(3):
            rms = sfm.bundle_adjust(rec, fix_frames=[i0], refine_intrinsics=not fix_intrinsics, max_nfev=80)
            dropped = sfm.prune(rec, max_reproj=2.5)
            log(f"  pass {it + 1}: median err {rms:.2f}px, f={rec.f:.1f}, k1={rec.k1:.4f}, dropped {dropped}")
        tag = base_tag if part == 1 else f"{base_tag}-m{part}"
        log(json.dumps(sfm.save(rec, out, tag, rms)))
        used |= regs
        if len(frames) - len(used) < 8:
            break
