"""Triangulate points marked in video frames and place them on the site grid.

    domus video pick data/video/picks/VIDEO.json VIDEO [more tags]

Marks are {"id", "desc", "obs": [{"frame", "px"}]} (see data/video/picks/). Each feature is
triangulated with the cameras of the first listed model that has two or more of its frames
registered. A mark that reprojects more than 6 px from the triangulated point is dropped and
the point re-solved, so one mis-marked frame shows up instead of skewing the result. Points
go to site coordinates (the site's grid and height datum) with that model's placement (data/video/<tag>-georef.json).
Writes data/video/picks/<name>-site.csv.
"""

import csv
import json
import math
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from . import sfm
from .sfm_georef import apply, load, triangulate_pixels


def video_dir(site):
    """The site's video work, data/video/."""
    return site.path / "data" / "video"


def reproject(rec, x, obs):
    f, k1, cx, cy = rec["intr"]
    errs, centres = [], []
    for o in obs:
        i = rec["names"].index(o["frame"])
        rv, tv = rec["rvecs"][i], rec["tvecs"][i]
        uv, z = sfm.project(x[None], rv[None], tv[None], f, k1, cx, cy)
        errs.append(float(np.hypot(*(uv[0] - o["px"]))) if z[0] > 0 else 1e9)
        centres.append(-Rotation.from_rotvec(rv).as_matrix().T @ tv)
    dirs = [(x - c) / np.linalg.norm(x - c) for c in centres]
    cosmin = min(float(a @ b) for a in dirs for b in dirs)
    return np.array(errs), math.degrees(math.acos(max(-1.0, min(1.0, cosmin))))


def pick(site, picks, tags=()):
    """`domus video pick`: the points marked in the picks file `picks`, each triangulated with the first of the
    placed reconstructions `tags` that registered two of its frames, onto the site's grid, as <picks>-site.csv.
    Gives that file."""
    picks_path = Path(picks)
    tags = list(tags)
    picks = json.loads(picks_path.read_text(encoding="utf-8"))
    models = []
    for tag in tags:
        geo = video_dir(site) / f"{tag}-georef.json"
        if not geo.exists():
            print(f"{tag}: not placed on the site yet, skipped")
            continue
        rec = load(video_dir(site) / f"{tag}-sfm.npz")
        main_clip = tag.split("-")[0]
        reg = {n for n, rv in zip(rec["names"], rec["rvecs"]) if np.all(np.isfinite(rv))}
        models.append((tag, main_clip, rec, reg, np.array(json.loads(geo.read_text(encoding="utf-8"))["params"])))

    rows = []
    for feat in picks["features"]:
        row = {"id": feat["id"], "desc": feat.get("desc", ""), "marks": len(feat["obs"])}
        for tag, main_clip, rec, reg, params in models:
            obs = []
            for o in feat["obs"]:
                name = o["frame"]
                if name.startswith(main_clip + "/"):
                    name = name.split("/", 1)[1]
                if name in reg:
                    obs.append({"frame": name, "px": np.asarray(o["px"], float)})
            if len(obs) < 2:
                continue
            dropped = []
            while True:
                x = triangulate_pixels(rec, obs)
                errs, angle = reproject(rec, x, obs)
                worst = int(np.argmax(errs))
                if errs[worst] <= 6.0 or len(obs) <= 2:
                    break
                dropped.append(obs.pop(worst)["frame"])
            e, n, h = apply(params, x[None])[0]
            row.update({"model": tag, "used": len(obs), "E": round(float(e), 3), "N": round(float(n), 3),
                        "H": round(float(h), 3), "reproj_max_px": round(float(errs.max()), 2),
                        "angle_deg": round(angle, 1), "dropped": " ".join(dropped)})
            break
        rows.append(row)
        print(f"{row['id']:<16} " + (f"{row['model']:<22} {row['used']}/{row['marks']} views  "
                                     f"E {row['E']:.2f} N {row['N']:.2f} H {row['H']:.2f}  "
                                     f"err {row['reproj_max_px']:.1f}px  angle {row['angle_deg']:.0f}deg"
                                     + (f"  dropped {row['dropped']}" if row['dropped'] else "")
                                     if "model" in row else "not triangulated"))
    out = picks_path.resolve().with_name(picks_path.stem + "-site.csv")
    fields = ["id", "E", "N", "H", "model", "used", "marks", "reproj_max_px", "angle_deg", "dropped", "desc"]
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    print(f"wrote {out.relative_to(site.path)}")
    return out
