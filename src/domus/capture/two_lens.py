"""Map a clip filmed on two lenses, from the workspace `domus video reconstruct` left behind.

    domus video two-lens CLIP WORK_TAG T_LAST_MAIN T_FIRST_WIDE F_MAIN F_WIDE [--stage copy,cameras,verify,map,convert]
                         [--mapper global|incremental] [--threads 8] [--min-matches 30] [--every N [--keep FRAMES.txt]]
                         [--ranges T0-T1,T0-T1] [--name PART] [--model K --as NAME]

`domus video reconstruct` gives every frame one shared camera. Where the phone switched lenses part way through a take
(frames up to T_LAST_MAIN seconds on the main camera, from T_FIRST_WIDE on the wide one, and the frames between them
in the zoom from one to the other left out), this keeps the features and matches of that run and maps them again with
a camera for each lens. The stages, all of them unless --stage names some:

  copy      data/video/cache/colmap/<WORK_TAG>/database.db is copied (SQLite's own backup, so a write-ahead log is
            included) to database-2lens.db beside it; nothing else touches the original;
  cameras   in the copy: camera 1 takes F_MAIN, a second SIMPLE_RADIAL camera with F_WIDE is added with a rig of its
            own, and the wide frames' images and frames are moved to it;
  verify    the two-view geometries are cleared and estimated again from the stored matches, now with the right camera
            for each frame (the pairs of sequential-pairs.txt and revisit-pairs.txt);
  map       COLMAP's global mapper (or the incremental one) on every frame but those in the zoom; with --every N only
            every N-th frame and the frames named in --keep (the ones that carry marks), which is quicker, and with
            --ranges only the frames in those stretches of seconds;
  convert   the largest model (or --model K, written as --as NAME) is written as two reconstructions in one set of
            coordinates, as domus.capture.sfm.save writes them: data/video/<CLIP>[-PART]-main-sfm.npz and -wide-sfm.npz, with
            a .json beside each. Each holds its own lens's frames and intrinsics and the points two or more of those
            frames see, so a placement found for one is the other's too. The saved files are reprojected with
            domus.capture.sfm.project and must give the median error COLMAP's own camera model gives.
"""

import json
import shutil
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy.spatial.transform import Rotation

from ..errors import DomusError
from . import sfm


def frames_dir(site):
    """The clips' frames, sources/raw/frames6/."""
    return site.path / "sources" / "raw" / "frames6"
def video_dir(site):
    """The site's video work, data/video/."""
    return site.path / "data" / "video"


def stamp(name):
    return float(Path(name).stem[1:])


def map_lenses(site, clip, work_tag, t_main, t_wide, f_main, f_wide, stage="all", mapper="global", threads=8,
               min_matches=30, every=None, keep=None, ranges=None, name="", model=None, as_=None):
    """`domus video two-lens`: the clip mapped again from `work_tag`'s workspace with a camera for each lens; the
    module's docstring says how, and what each argument does."""
    run(site, SimpleNamespace(clip=clip, work_tag=work_tag, t_main=t_main, t_wide=t_wide, f_main=f_main, f_wide=f_wide,
                              stage=stage, mapper=mapper, threads=threads, min_matches=min_matches, every=every,
                              keep=keep, ranges=ranges, name=name, model=model, as_=as_))


def run(site, args):
    import pycolmap

    clip, t_main, t_wide, f_main, f_wide = args.clip, args.t_main, args.t_wide, args.f_main, args.f_wide
    work = video_dir(site) / "cache" / "colmap" / args.work_tag
    src, db = work / "database.db", work / "database-2lens.db"
    t0 = time.time()

    def log(msg):
        print(f"[{time.time() - t0:7.1f}s] {msg}", flush=True)

    def want(s):
        return args.stage == "all" or s in args.stage.split(",")

    if want("copy"):
        if not src.exists():
            raise DomusError(f"There's no {src}: run domus video reconstruct first")
        if db.exists():
            db.unlink()
        a = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
        b = sqlite3.connect(db.as_posix())
        with b:
            a.backup(b)
        a.close()
        b.close()
        with pycolmap.Database.open(db) as d:
            log(f"copied to {db.name}: {d.num_images()} images, {d.num_matched_image_pairs()} matched pairs, "
                f"{d.num_verified_image_pairs()} verified")

    if want("cameras"):
        with pycolmap.Database.open(db) as d:
            cams = d.read_all_cameras()
            if len(cams) != 1:
                raise DomusError(f"expected the one shared camera, found {len(cams)}: start again from --stage copy")
            main = cams[0]
            w, h = main.width, main.height
            main.params = [f_main, w / 2, h / 2, 0.0]
            main.has_prior_focal_length = True
            d.update_camera(main)
            wide = pycolmap.Camera(model="SIMPLE_RADIAL", width=w, height=h, params=[f_wide, w / 2, h / 2, 0.0])
            wide.has_prior_focal_length = True
            wide_id = d.write_camera(wide)
            rig = pycolmap.Rig()
            rig.add_ref_sensor(pycolmap.sensor_t(pycolmap.SensorType.CAMERA, wide_id))
            rig_id = d.write_rig(rig)
            moved = between = 0
            frames = {f.frame_id: f for f in d.read_all_frames()}
            for im in d.read_all_images():
                t = stamp(im.name)
                if t <= t_main:
                    continue
                if t < t_wide:
                    between += 1
                    continue
                fr = frames[im.frame_id]                           # the frame first: an image must be in its frame's data
                fr.rig_id = rig_id
                fr.clear_data_ids()
                fr.add_data_id(pycolmap.data_t(pycolmap.sensor_t(pycolmap.SensorType.CAMERA, wide_id), im.image_id))
                d.update_frame(fr)
                im.camera_id = wide_id
                d.update_image(im)
                moved += 1
            log(f"camera {main.camera_id} f {f_main:.0f}; camera {wide_id} f {f_wide:.0f} with rig {rig_id}: {moved} frames moved to it, "
                f"{between} between the two left on camera {main.camera_id} and out of the mapping")
        with pycolmap.Database.open(db) as d:                      # read back
            by_cam = {}
            for im in d.read_all_images():
                by_cam.setdefault(im.camera_id, []).append(stamp(im.name))
            for cid, ts in sorted(by_cam.items()):
                log(f"   camera {cid}: {len(ts)} frames, {min(ts):.3f}-{max(ts):.3f} s; {d.read_camera(cid)}")
            bad = [f.frame_id for f in d.read_all_frames() if f.num_data_ids() != 1]
            if bad:
                raise DomusError(f"{len(bad)} frames without exactly one image")

    if want("verify"):
        pairs = work / "all-pairs.txt"
        lines = []
        for name in ("sequential-pairs.txt", "revisit-pairs.txt"):
            if (work / name).exists():
                lines += (work / name).read_text(encoding="utf-8").splitlines()
        pairs.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pycolmap.Database.open(db) as d:
            before = d.num_verified_image_pairs()
            d.clear_two_view_geometries()
        log(f"verifying {len(lines)} pairs again ({before} were verified with the one camera) ...")
        pycolmap.verify_matches(db, pairs, options=pycolmap.TwoViewGeometryOptions())
        with pycolmap.Database.open(db) as d:
            _, inl = d.read_two_view_geometry_num_inliers()
            log(f"verified again: {d.num_verified_image_pairs()} pairs, {int(np.sum(np.asarray(inl) >= 15))} with 15 or more inliers, "
                f"{int(np.sum(np.asarray(inl) >= args.min_matches))} with {args.min_matches} or more")

    part = args.name
    sparse = work / f"sparse-2lens-{args.mapper}{'-' + part if part else ''}"
    if want("map"):
        with pycolmap.Database.open(db) as d:
            names = sorted(im.name for im in d.read_all_images() if not (t_main < stamp(im.name) < t_wide))
        if args.every:
            keep = set(Path(args.keep).read_text(encoding="utf-8").split()) if args.keep else set()
            names = [n for k, n in enumerate(names) if k % args.every == 0 or Path(n).stem in keep]
        if args.ranges:
            spans = [tuple(float(v) for v in r.split("-")) for r in args.ranges.split(",")]
            names = [n for n in names if any(a <= stamp(n) <= b for a, b in spans)]
        if sparse.exists():
            shutil.rmtree(sparse)
        sparse.mkdir(parents=True)
        log(f"mapping ({args.mapper}) {len(names)} frames ...")
        cls = pycolmap.GlobalPipelineOptions if args.mapper == "global" else pycolmap.IncrementalPipelineOptions
        o = cls(num_threads=args.threads, random_seed=0, min_model_size=8, min_num_matches=args.min_matches)
        o.image_names = names
        o.mapper.num_threads = args.threads
        o.mapper.random_seed = 0
        mapping = pycolmap.global_mapping if args.mapper == "global" else pycolmap.incremental_mapping
        recs = mapping(db, frames_dir(site), sparse, o)
        for k, r in sorted(recs.items(), key=lambda kv: -kv[1].num_reg_images()):
            log(f"   model {k}: {r.num_reg_images()} frames, {r.num_points3D()} points, mean reprojection {r.compute_mean_reprojection_error():.3f} px; "
                + "; ".join(f"camera {c.camera_id} {np.round(c.params, 2).tolist()}" for c in r.cameras.values()))

    if want("convert"):
        convert(site, pycolmap, clip, t_main, sparse, db, part, args.model, args.as_, log)
    log("done")


def convert(site, pycolmap, clip, t_main, sparse, db, part, model, as_, log):
    """The mapped model as two reconstructions, one for each lens's frames, in one set of coordinates."""
    if not sparse.exists():
        raise DomusError(f"There's no {sparse}: map first (--stage map)")
    folders = sorted((p for p in sparse.iterdir() if p.is_dir()), key=lambda p: -pycolmap.Reconstruction(p).num_reg_images())
    if model is not None:
        folders = [sparse / model]
    rec = pycolmap.Reconstruction(folders[0])
    log(f"converting {folders[0].relative_to(site.path)}: {rec.num_reg_images()} frames, {rec.num_points3D()} points")
    with pycolmap.Database.open(db) as d:
        cam_of = {Path(im.name).stem: im.camera_id for im in d.read_all_images()}
        main_id = min(cam_of.values())
    xyz_all = {pid: np.array(p.xyz, float) for pid, p in rec.points3D.items()}
    rgb_all = {pid: np.array(p.color, np.uint8) for pid, p in rec.points3D.items()}
    for label, pick in (("main", lambda t, c: c == main_id and t <= t_main), ("wide", lambda t, c: c != main_id)):
        names = [n for n in sorted(cam_of) if pick(float(n[1:]), cam_of[n])]
        index = {n: i for i, n in enumerate(names)}
        rvecs = np.full((len(names), 3), np.nan)
        tvecs = np.full((len(names), 3), np.nan)
        rows, own_err, cam = [], [], None
        for image_id in rec.reg_image_ids():
            image = rec.image(image_id)
            stem = Path(image.name).stem
            if stem not in index:
                continue
            cam = image.camera
            pose = image.cam_from_world()
            rvecs[index[stem]] = Rotation.from_matrix(pose.rotation.matrix()).as_rotvec()
            tvecs[index[stem]] = np.array(pose.translation, float)
            pts = list(image.get_observation_points2D())
            if not pts:
                continue
            uv = cam.img_from_cam(pose * np.array([xyz_all[p.point3D_id] for p in pts]))
            err = np.hypot(*(uv - np.array([p.xy for p in pts])).T)
            best = {}
            for k, p in enumerate(pts):                      # a track can hold two keypoints of one frame: keep the closer
                if p.point3D_id not in best or err[k] < err[best[p.point3D_id]]:
                    best[p.point3D_id] = k
            for pid, k in best.items():
                rows.append((pid, index[stem], pts[k].xy[0] - 0.5, pts[k].xy[1] - 0.5))
                own_err.append(err[k])
        if cam is None:
            log(f"   {label}: none of its frames is in the model")
            continue
        rows = np.array(rows, float)
        pid, counts = np.unique(rows[:, 0].astype(np.int64), return_counts=True)
        keep = set(pid[counts >= 2].tolist())
        sel = np.array([int(r) in keep for r in rows[:, 0]])
        rows, own = rows[sel], np.array(own_err)[sel]
        order = sorted(keep)
        new = {p: k for k, p in enumerate(order)}
        obs = np.column_stack([[new[int(r)] for r in rows[:, 0]], rows[:, 1], rows[:, 2], rows[:, 3]])
        xyz = np.array([xyz_all[p] for p in order])
        rgb = np.array([rgb_all[p] for p in order], np.uint8)
        nview = np.bincount(obs[:, 0].astype(int), minlength=len(order))
        f, cx, cy, k1 = (float(v) for v in cam.params)
        intr = np.array([f, k1, cx - 0.5, cy - 0.5])
        out_part = as_ or part
        tag = f"{clip}-{out_part + '-' if out_part else ''}{label}"
        np.savez_compressed(video_dir(site) / f"{tag}-sfm.npz", xyz=xyz, rgb=rgb, nview=nview, frame_names=np.array(names),
                            rvecs=rvecs, tvecs=tvecs, intrinsics=intr, obs=obs)
        d = np.load(video_dir(site) / f"{tag}-sfm.npz")
        pi, fi = d["obs"][:, 0].astype(int), d["obs"][:, 1].astype(int)
        proj, _ = sfm.project(d["xyz"][pi], d["rvecs"][fi], d["tvecs"][fi], *d["intrinsics"][[0, 1, 2, 3]])
        saved = float(np.median(np.hypot(*(proj - d["obs"][:, 2:]).T)))
        registered = int(np.isfinite(rvecs).all(axis=1).sum())
        summary = {"clip": tag, "frames": len(names), "registered": registered, "points": int(len(xyz)), "focal_px": f, "k1": k1,
                   "median_reproj_px": saved,
                   "note": f"One of two lenses of {clip}, mapped together (domus video two-lens): the same coordinates as the other."}
        (video_dir(site) / f"{tag}-sfm.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        log(json.dumps(summary))
        log(f"   {tag}: median reprojection {saved:.4f} px from the saved file with sfm.project, {float(np.median(own)):.4f} px by COLMAP's camera model")
        if not abs(saved - float(np.median(own))) <= 1e-3:
            raise DomusError(f"{tag}: conversion check failed")
