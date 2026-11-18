"""Reconstruct a site video with COLMAP (pycolmap) instead of domus.sfm.

    domus video reconstruct VIDEO --extra VIDEO,VIDEO,VIDEO \
        --extra-step 1 --focal 630
    domus video reconstruct VIDEO --focal 650 [--step 2] [--mapper incremental]

The frames are those tools/video_sfm.py takes (sources/raw/frames6/<clip>/, every --step-th,
then every --extra-step-th frame of the --extra clips), and the output is the same:
data/video/<tag>-sfm.npz, .ply and .json, written by domus.sfm.save, and further
disconnected models as <tag>-m2, -m3 ... So georef_sfm.py, triangulate_picks.py,
dense_sfm.py and link_picks.py read it unchanged. The tag defaults to <clip>-colmap, so a
model from video_sfm.py is never overwritten.

Stages (each logs its wall time):
1. features: COLMAP's SIFT; one SIMPLE_RADIAL camera shared by all frames, started from
   --focal with the principal point at the image centre;
2. sequential matching: each frame with the next --overlap frames, and with the frames 2^k
   further on beyond those (COLMAP's quadratic overlap: with 12, the 16th, 32nd, 64th ...),
   pairs from COLMAP's sequential pair generator, matched and geometrically verified as one
   list with pycolmap.match_image_pairs;
3. revisits: a VLAD descriptor per frame (its SIFT descriptors against 64 words clustered
   from all frames, no vocabulary tree needed); each frame's --retrieve most similar frames
   among the pairs not matched yet, from different moments of the walk, are matched and
   verified the same way;
4. mapping: COLMAP's global mapper (GLOMAP, the default) or its incremental mapper, on the
   pairs with at least --min-matches verified matches;
5. conversion and a check: the saved model reprojected with domus.sfm.project must give
   the median reprojection error COLMAP's own camera model gives, or the tool stops.

Conventions: COLMAP puts the centre of the top-left pixel at (0.5, 0.5), OpenCV and our
files at (0, 0), so keypoints and the principal point move by -0.5 px. COLMAP's
SIMPLE_RADIAL camera (f, cx, cy, k) is the model domus.sfm.project uses (normalised
x, y scaled by 1 + k (x^2 + y^2), then f and the principal point), so f and k carry over
unchanged. Poses are COLMAP's cam_from_world (world to camera), as Rodrigues vectors.

Workspace: data/video/cache/colmap/<tag>/ (database, pair lists, COLMAP's own models). A
re-run with the same frames, focal and matching settings keeps the database's features and
matches, so trying the other mapper costs only the mapping; --fresh starts over.
"""

import json
import shutil
import time
from contextlib import contextmanager

import cv2
import numpy as np
import pycolmap
from PIL import Image
from scipy.spatial.transform import Rotation

from ..errors import DomusError
from . import sfm


def frames_dir(site):
    """The clips' frames, sources/raw/frames6/."""
    return site.path / "sources" / "raw" / "frames6"
def video_dir(site):
    """The site's video work, data/video/."""
    return site.path / "data" / "video"
MIN_MODEL = 8          # frames; smaller models are dropped, as in video_sfm.py
MAX_MODELS = 5         # <tag>, <tag>-m2 ... <tag>-m5, as in video_sfm.py
VLAD_WORDS = 64
QUADRATIC_STEPS = 16   # sequential matching also pairs frames 2^k apart, k < 16
FEATURE_SETTINGS = ("images", "focal", "max_features", "first_octave")   # a change: new features

T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)


@contextmanager
def stage(name, times):
    log(f"{name} ...")
    t = time.time()
    yield
    times[name] = time.time() - t
    log(f"{name}: {times[name]:.1f}s")


# --------------------------------------------------------------------------- conventions

def colmap_intrinsics(params):
    """COLMAP SIMPLE_RADIAL params (f, cx, cy, k) -> our intrinsics [f, k1, cx, cy]."""
    f, cx, cy, k = (float(v) for v in params)
    return np.array([f, k, cx - 0.5, cy - 0.5])


def colmap_pixels(xy):
    """COLMAP pixel coordinates (top-left pixel centre at 0.5, 0.5) -> ours (at 0, 0)."""
    return np.asarray(xy, float) - 0.5


def colmap_pose(cam_from_world):
    """pycolmap.Rigid3d (world to camera) -> (rvec, tvec) for domus.sfm.project."""
    rvec = Rotation.from_matrix(cam_from_world.rotation.matrix()).as_rotvec()
    return rvec, np.array(cam_from_world.translation, float)


def colmap_views(rec):
    """For each registered image of a pycolmap.Reconstruction: (image, its points2D that observe
    a 3D point, their reprojection errors in px by COLMAP's own pose and camera code). A track
    can hold two nearby keypoints of one frame (GLOMAP allows that); only the one COLMAP
    reprojects closer is kept, since our tracks see each frame once."""
    xyz = {pid: np.array(p.xyz) for pid, p in rec.points3D.items()}
    views = []
    for image_id in rec.reg_image_ids():
        image = rec.image(image_id)
        pts = list(image.get_observation_points2D())
        if not pts:
            views.append((image, [], np.zeros(0)))
            continue
        uv = image.camera.img_from_cam(image.cam_from_world() * np.array([xyz[p.point3D_id] for p in pts]))
        err = np.hypot(*(uv - np.array([p.xy for p in pts])).T)
        best = {}
        for k, p in enumerate(pts):
            if p.point3D_id not in best or err[k] < err[best[p.point3D_id]]:
                best[p.point3D_id] = k
        keep = sorted(best.values())
        views.append((image, [pts[k] for k in keep], err[keep]))
    return views


def to_ours(rec, names, frame_of):
    """A pycolmap.Reconstruction as a domus.sfm.Reconstruction that sfm.save writes.

    `names` are our frame names in our order, `frame_of` maps a COLMAP image name to its
    index in `names`. Each frame keeps only its keypoints that observe a 3D point (see
    colmap_views), coloured with that point's colour (COLMAP's mean over its views). Returns
    the reconstruction and the number of observations left out as second views of a point
    in one frame.
    """
    cams = list(rec.cameras.values())
    if len(cams) != 1 or cams[0].model_name != "SIMPLE_RADIAL":
        raise DomusError(f"expected one SIMPLE_RADIAL camera, got {[c.model_name for c in cams]}")
    f, k1, cx, cy = colmap_intrinsics(cams[0].params)
    frames = [sfm.Frame(n, np.zeros((0, 2)), None, np.zeros((0, 3), np.uint8)) for n in names]
    out = sfm.Reconstruction(frames, f=f, k1=k1, cx=cx, cy=cy)
    pids = sorted(rec.points3D.keys())
    track_of = {pid: t for t, pid in enumerate(pids)}
    colour = {}
    for pid in pids:
        p = rec.point3D(pid)
        out.points[track_of[pid]] = np.array(p.xyz, float)
        colour[pid] = np.array(p.color, np.uint8)
    out.tracks = [{} for _ in pids]
    left_out = 0
    for image, pts, _ in colmap_views(rec):
        fr = frames[frame_of[image.name]]
        fr.rvec, fr.tvec = colmap_pose(image.cam_from_world())
        fr.keypoints = colmap_pixels([p.xy for p in pts]).reshape(-1, 2)
        fr.colours = np.array([colour[p.point3D_id] for p in pts], np.uint8).reshape(-1, 3)
        for kp, p in enumerate(pts):
            out.tracks[track_of[p.point3D_id]][frame_of[image.name]] = kp
        left_out += image.num_points3D - len(pts)
    return out, left_out


def median_reproj(rec):
    """Median reprojection error (px) of a domus.sfm.Reconstruction over all its views."""
    pi, fi, uv = [], [], []
    for t, tr in enumerate(rec.tracks):
        for f_i, kp in tr.items():
            pi.append(t)
            fi.append(f_i)
            uv.append(rec.frames[f_i].keypoints[kp])
    xyz = np.array([rec.points[t] for t in range(len(rec.tracks))])
    rv = np.array([rec.frames[i].rvec for i in fi])
    tv = np.array([rec.frames[i].tvec for i in fi])
    proj, _ = sfm.project(xyz[pi], rv, tv, rec.f, rec.k1, rec.cx, rec.cy)
    return float(np.median(np.hypot(*(proj - np.array(uv)).T)))


def saved_median_reproj(npz_path):
    """The same median, read back from a saved <tag>-sfm.npz (obs rows: point, frame, u, v)."""
    d = np.load(npz_path)
    obs = d["obs"]
    pi, fi = obs[:, 0].astype(int), obs[:, 1].astype(int)
    f, k1, cx, cy = d["intrinsics"]
    proj, _ = sfm.project(d["xyz"][pi], d["rvecs"][fi], d["tvecs"][fi], f, k1, cx, cy)
    return float(np.median(np.hypot(*(proj - obs[:, 2:]).T)))


def colmap_median_reproj(rec):
    """Median reprojection error (px) by COLMAP's own pose and camera code, in its own pixels,
    over the views to_ours keeps."""
    return float(np.median(np.concatenate([err for _, _, err in colmap_views(rec)])))


# --------------------------------------------------------------------------- inputs

def frame_list(site, clip, step, extra, extra_step):
    """COLMAP image names (<clip>/<file>, under sources/raw/frames6) and our frame names, in
    our order: the main clip's frame stems, then <clip>/<stem> for each extra clip."""
    images, names = [], []
    for c, s, main in [(clip, step, True)] + [(e, extra_step, False) for e in extra]:
        paths = sorted((frames_dir(site) / c).glob("*.jpg"))[::s]
        if not paths:
            raise DomusError(f"no frames in {frames_dir(site) / c}")
        images += [f"{c}/{p.name}" for p in paths]
        names += [p.stem if main else f"{c}/{p.stem}" for p in paths]
    return images, names


def prepare_workspace(site, work, settings, fresh):
    """Keep the database's features when they were made from the same frames, focal and
    feature count, and its matches too when the matching settings are also the same.
    COLMAP skips frames and pairs already in the database, so an interrupted run resumes."""
    db = work / "database.db"
    stamp = work / "settings.json"
    old = json.loads(stamp.read_text(encoding="utf-8")) if stamp.exists() and db.exists() and not fresh else None
    if old is None or any(old.get(k) != settings[k] for k in FEATURE_SETTINGS):
        if work.exists():
            shutil.rmtree(work)
        log(f"new workspace {work.relative_to(site.path)}")
    elif old != settings:
        with pycolmap.Database.open(db) as d:
            d.clear_matches()
            d.clear_two_view_geometries()
        (work / "revisit-pairs.txt").unlink(missing_ok=True)
        log(f"reusing the features in {db.relative_to(site.path)}; matching settings changed, matches cleared")
    else:
        log(f"reusing {db.relative_to(site.path)}: features and matches made with the same settings")
    work.mkdir(parents=True, exist_ok=True)
    stamp.write_text(json.dumps(settings, indent=1) + "\n", encoding="utf-8")
    return db


# --------------------------------------------------------------------------- revisit pairs

def unit_rows(desc):
    d = desc.astype(np.float32)
    return d / (np.linalg.norm(d, axis=1, keepdims=True) + 1e-9)


def vlad_descriptors(d, ids, rng):
    """One VLAD vector per image (rows in `ids` order) from its SIFT descriptors in database `d`,
    normalised as domus.sfm.vlad does (per word, square root, whole vector); 64 words by
    k-means on a sample of every frame's descriptors. Descriptors are read one frame at a time:
    a long clip's would not fit in memory at once."""
    sample = []
    for i in ids:
        desc = d.read_descriptors(i).data
        take = rng.choice(len(desc), min(150, len(desc)), replace=False)
        sample.append(unit_rows(desc[take]))
    sample = np.concatenate(sample)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 25, 1e-4)
    cv2.setRNGSeed(0)
    _, _, words = cv2.kmeans(sample, VLAD_WORDS, None, criteria, 1, cv2.KMEANS_PP_CENTERS)
    wsq = (words ** 2).sum(1)
    out = np.zeros((len(ids), VLAD_WORDS * 128), np.float32)
    for row, i in enumerate(ids):
        x = unit_rows(d.read_descriptors(i).data)
        nearest = np.argmax(2 * x @ words.T - wsq, axis=1)
        member = np.zeros((VLAD_WORDS, len(x)), np.float32)
        member[nearest, np.arange(len(x))] = 1.0
        v = member @ x - member.sum(1, keepdims=True) * words      # residuals summed per word
        v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
        v = np.sign(v) * np.sqrt(np.abs(v))
        out[row] = v.ravel() / (np.linalg.norm(v) + 1e-9)
    return out


def revisit_pairs(db, per_frame, gap, rng):
    """Image-name pairs to match for revisits: for each frame, its `per_frame` most similar
    frames (VLAD) among the pairs not matched yet, each at least `gap` frames (in COLMAP's
    name order) from the others picked for it, so the picks come from different moments."""
    with pycolmap.Database.open(db) as d:
        images = sorted(d.read_all_images(), key=lambda im: im.name)
        ids = [im.image_id for im in images]
        v = vlad_descriptors(d, ids, rng)
        sim = v @ v.T
        pairs = set()
        for a in range(len(ids)):
            picked = []
            for b in np.argsort(-sim[a]):
                b = int(b)
                # exists_matches: the pair was matched already, also if that found no matches
                if b == a or any(abs(b - p) < gap for p in picked) or d.exists_matches(ids[a], ids[b]):
                    continue
                picked.append(b)
                pairs.add((min(a, b), max(a, b)))
                if len(picked) == per_frame:
                    break
    sims = [float(sim[a, b]) for a, b in pairs]
    log(f"{len(pairs)} revisit candidate pairs (VLAD similarity median "
        f"{np.median(sims) if sims else float('nan'):.3f}; sequential neighbours "
        f"{float(np.median([sim[i, i + 1] for i in range(len(ids) - 1)])):.3f})")
    return [(images[a].name, images[b].name) for a, b in sorted(pairs)]


def sequential_pairs(db, overlap):
    """COLMAP's sequential pairs (its own pair generator, frames in name order) as image-name
    pairs: each frame with the next `overlap`, and with the frames 2^k on. COLMAP's quadratic
    overlap replaces the window (it pairs i with i + 2^k only), so both are asked for."""
    with pycolmap.Database.open(db) as d:
        name = {im.image_id: im.name for im in d.read_all_images()}
        pairs = set()
        for quadratic, n in ((False, overlap), (True, QUADRATIC_STEPS)):
            opts = pycolmap.SequentialPairingOptions(overlap=n, quadratic_overlap=quadratic, loop_detection=False)
            generated = pycolmap.SequentialPairGenerator(opts, d).all_pairs()
            pairs |= {tuple(sorted((name[a], name[b]))) for a, b in generated}
    return sorted(pairs)


def match_pairs(db, pairs, path, matching):
    """Match and verify image-name pairs, written to `path`, as one list (pairs already in the
    database are skipped). On this PC that is twice as fast as pycolmap.match_sequential on the
    same pairs (blocks of 1225 pairs keep all threads busy; it goes a frame at a time)."""
    path.write_text("".join(f"{a} {b}\n" for a, b in pairs), encoding="utf-8")
    if pairs:
        pycolmap.match_image_pairs(db, matching_options=matching,
                                   pairing_options=pycolmap.ImportedPairingOptions(match_list_path=path),
                                   device=pycolmap.Device.cpu)


def verified_pairs(db, among=None):
    """(pairs matched, pairs verified with 15 or more inliers); with `among`, a list of image-name
    pairs, only those are counted."""
    with pycolmap.Database.open(db) as d:
        ids, inliers = d.read_two_view_geometry_num_inliers()
        ok = {pid for pid, n in zip(ids, inliers) if n >= 15}
        if among is None:
            return d.num_matched_image_pairs(), len(ok)
        image_id = {im.name: im.image_id for im in d.read_all_images()}
        pids = {pycolmap.image_pair_to_pair_id(image_id[a], image_id[b]) for a, b in among}
        return len(pids), len(pids & ok)


# --------------------------------------------------------------------------- mapping

def run_mapper(site, kind, db, sparse, threads, seed, min_matches):
    """COLMAP's global (GLOMAP) or incremental mapper; pairs with fewer than `min_matches`
    verified matches are left out of the view graph."""
    if sparse.exists():
        shutil.rmtree(sparse)
    sparse.mkdir(parents=True)
    if kind == "global":
        opts = pycolmap.GlobalPipelineOptions(num_threads=threads, random_seed=seed,
                                              min_model_size=MIN_MODEL, min_num_matches=min_matches)
        opts.mapper.num_threads = threads
        opts.mapper.random_seed = seed
        return pycolmap.global_mapping(db, frames_dir(site), sparse, opts)
    opts = pycolmap.IncrementalPipelineOptions(num_threads=threads, random_seed=seed,
                                               min_model_size=MIN_MODEL, min_num_matches=min_matches)
    opts.mapper.num_threads = threads
    opts.mapper.random_seed = seed
    return pycolmap.incremental_mapping(db, frames_dir(site), sparse, opts)


# --------------------------------------------------------------------------- the step

def reconstruct(site, clip, step=1, extra=(), extra_step=3, focal=1100.0, tag="", threads=8, overlap=12, retrieve=5,
                max_features=8192, first_octave=0, mapper="global", min_matches=30, seed=0, fresh=False):
    """`domus video reconstruct`: the clip's frames (every `step`-th, and every `extra_step`-th of the `extra` clips)
    reconstructed with COLMAP, as data/video/<tag>-sfm.npz, .ply and .json (and <tag>-m2 and on for further models);
    the module's docstring says how. The tag is <clip>-colmap unless given."""
    tag = tag or f"{clip}-colmap"
    extra = [c for c in extra if c]
    images, names = frame_list(site, clip, step, extra, extra_step)
    sizes = set()
    for im in images:
        with Image.open(frames_dir(site) / im) as pic:
            sizes.add(pic.size)
    if len(sizes) > 1:
        raise DomusError(f"frames differ in size {sizes}: one shared camera needs one size")
    w, h = sizes.pop()
    log(f"{len(images)} frames ({clip} every {step}"
        + (f", {','.join(extra)} every {extra_step}" if extra else "") + f"), {w} x {h}")
    work = site.path / "data" / "video" / "cache" / "colmap" / tag
    settings = {"images": images, "focal": focal, "max_features": max_features,
                "first_octave": first_octave, "overlap": overlap, "quadratic_steps": QUADRATIC_STEPS,
                "retrieve": retrieve}
    db = prepare_workspace(site, work, settings, fresh)
    times = {}

    with stage("features", times):
        ext = pycolmap.FeatureExtractionOptions(num_threads=threads)
        ext.sift.max_num_features = max_features
        ext.sift.first_octave = first_octave
        reader = pycolmap.ImageReaderOptions(camera_model="SIMPLE_RADIAL",
                                             camera_params=f"{focal},{w / 2},{h / 2},0")
        pycolmap.extract_features(db, frames_dir(site), image_names=images, camera_mode=pycolmap.CameraMode.SINGLE,
                                  reader_options=reader, extraction_options=ext, device=pycolmap.Device.cpu)
        with pycolmap.Database.open(db) as d:
            cams = d.read_all_cameras()
            n_kp = [d.num_keypoints_for_image(im.image_id) for im in d.read_all_images()]
        log(f"{len(n_kp)} frames, median {int(np.median(n_kp))} keypoints; camera {cams[0].model_name} "
            f"{np.round(cams[0].params, 2).tolist()} (prior focal: {cams[0].has_prior_focal_length})")

    matching = pycolmap.FeatureMatchingOptions(num_threads=threads)
    with stage("sequential matching", times):
        pairs = sequential_pairs(db, overlap)
        match_pairs(db, pairs, work / "sequential-pairs.txt", matching)
        log("{} sequential pairs; pairs matched {}, verified {}".format(len(pairs), *verified_pairs(db)))

    if retrieve > 0:
        pairs_file = work / "revisit-pairs.txt"
        with stage("revisit search", times):
            if pairs_file.exists():          # a re-run: the same pairs, not the next best ones
                pairs = [tuple(line.split()) for line in pairs_file.read_text(encoding="utf-8").splitlines()]
                log(f"reusing {len(pairs)} revisit pairs from {pairs_file.relative_to(site.path)}")
            else:
                pairs = revisit_pairs(db, retrieve, max(2, overlap // 2), np.random.default_rng(seed))
        with stage("revisit matching", times):
            match_pairs(db, pairs, pairs_file, matching)
            n, v = verified_pairs(db)
            log(f"pairs matched {n}, verified {v} ({verified_pairs(db, pairs)[1]} of {len(pairs)} "
                "revisit pairs verified)")

    with stage(f"mapping ({mapper})", times):
        recs = run_mapper(site, mapper, db, work / f"sparse-{mapper}", threads, seed,
                          min_matches)
        models = sorted(recs.values(), key=lambda r: -r.num_reg_images())
        for r in models:
            log(f"  model: {r.num_reg_images()} frames, {r.num_points3D()} points, "
                f"mean reprojection {r.compute_mean_reprojection_error():.3f}px")

    with stage("conversion and check", times):
        frame_of = {im: k for k, im in enumerate(images)}
        kept = [r for r in models if r.num_reg_images() >= MIN_MODEL][:MAX_MODELS]
        for part, r in enumerate(kept, start=1):
            out_tag = tag if part == 1 else f"{tag}-m{part}"
            r.update_point_3d_errors()
            ours, dup = to_ours(r, names, frame_of)
            med = median_reproj(ours)
            summary = sfm.save(ours, video_dir(site), out_tag, med)
            saved = saved_median_reproj(video_dir(site) / f"{out_tag}-sfm.npz")
            own = colmap_median_reproj(r)
            log(json.dumps(summary))
            log(f"  {out_tag}: median reprojection {saved:.4f}px from the saved file with sfm.project, "
                f"{own:.4f}px by COLMAP's camera model (COLMAP mean {r.compute_mean_reprojection_error():.4f}px)"
                + (f"; {dup} second views of a point in one frame left out" if dup else ""))
            if not abs(saved - own) <= 1e-3:          # also stops on NaN
                raise DomusError(f"{out_tag}: conversion check failed ({saved:.6f} vs {own:.6f} px)")
        for r in models[len(kept):]:
            log(f"  model of {r.num_reg_images()} frames not written")
        for part in range(len(kept) + 1, MAX_MODELS + 1):
            if (video_dir(site) / f"{tag}-m{part}-sfm.npz").exists():
                log(f"  note: data/video/{tag}-m{part}-sfm.* is from an earlier run, not this one")

    log("stage times: " + ", ".join(f"{k} {v:.0f}s" for k, v in times.items())
        + f"; total {sum(times.values()):.0f}s")
