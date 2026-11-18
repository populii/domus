"""Textures cut from a site's photos and video frames: quadrilaterals rectified to rectangles, evened out and
made to tile, and house walls rectified from photos or placed video frames.

    domus prep feature-textures [--check DIR]
    domus prep wall-textures [--check DIR]
    domus prep house-textures [group ...]

What to cut comes from data/prep.json:

    "feature_textures": {"textures": {"fence.jpg": {"source": "photo.jpg", "quad": [[x, y], ... top left, top right,
        bottom right, bottom left], "size": [w, h], "seams": "x", "straighten": true}}, "turned": {"post.jpg": "rail.jpg"}}
    "wall_textures": {"gain": 1.5, "pieces": {"block": {"source": ..., "quad": ..., "size": ...}},
        "tiles": {"block.jpg": {"piece": "block", "seams": "x"}}, "bays": {"boards.jpg": {"piece": "boards", "post": "post"}}}
    "house_textures": {"sources": {"north": {"photo": ..., "faces": {"north": [[[u, height], [x, y]], ...]},
        "keep": [[u0, u1, z0, z1]], "drop": [...], "note": ...}, "west": {"frames": ["tag", "all"]}}}

Sources are under sources/raw/ in the site project. domus.model.features.MATERIALS and domus.model.walls give the size in metres
one copy of a texture covers.
"""

import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial.transform import Rotation

from ..model import house_source, terrain
from . import config


def raw_dir(site):
    """The site's source documents and photos, sources/raw/."""
    return site.path / "sources" / "raw"
PPM = 100


def rectify(img, quad, size):
    w, h = size
    m = cv2.getPerspectiveTransform(np.float32(quad), np.float32([(0, 0), (w, 0), (w, h), (0, h)]))
    return cv2.warpPerspective(img, m, (w, h), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REFLECT)


def deskew(a):
    """Shear so the dominant edges (paling joints, slats) stand vertical: the structure tensor's
    main direction gives their slant."""
    g = cv2.cvtColor(a.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1)
    theta = 0.5 * np.arctan2(2 * np.sum(gx * gy), np.sum(gx * gx - gy * gy))   # gradient direction
    h, w = g.shape
    k = np.tan(theta)                     # x' = x + k (y - h/2) makes the gradient horizontal
    m = np.float32([[1, k, -k * h / 2], [0, 1, 0]])
    return cv2.warpAffine(a, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT), np.degrees(theta)


def even_out(a):
    """Divide out the slow brightness trend (vignetting, shade gradients), keep the mean."""
    f = a.astype(np.float32)
    k = max(a.shape[:2]) // 2 * 2 + 1
    low = cv2.GaussianBlur(f, (k, k), 0)
    return np.clip(f / np.maximum(low, 1) * low.mean(axis=(0, 1)), 0, 255)


def seamless(a, axes, frac=0.18):
    """Cross-fade the image with itself shifted by half, near the seams, so it tiles."""
    out = a.astype(np.float32)
    for ax in axes:
        n = out.shape[0 if ax == "y" else 1]
        rolled = np.roll(out, n // 2, axis=0 if ax == "y" else 1)
        t = np.abs(np.arange(n) - (n - 1) / 2) / ((n - 1) / 2)          # 0 in the middle, 1 at the seams
        wgt = np.clip((t - (1 - 2 * frac)) / (2 * frac), 0, 1)
        wgt = wgt[:, None, None] if ax == "y" else wgt[None, :, None]
        out = out * (1 - wgt) + rolled * wgt
    return out


def feature_textures(site, check=None):
    """`domus prep feature-textures`: the textures "feature_textures" in data/prep.json names, cut from
    its photos into data/features/; with `check`, a folder for the sources with their quads drawn on."""
    cfg = config(site, "feature_textures")
    out = site.path / "data" / "features"
    out.mkdir(parents=True, exist_ok=True)
    for name, t in cfg["textures"].items():
        img = np.asarray(Image.open(raw_dir(site) / t["source"]).convert("RGB"))
        tex = rectify(img, t["quad"], t["size"])
        note = ""
        if t.get("straighten"):
            tex, ang = deskew(tex)
            note = f", straightened {ang:+.1f} deg"
        tex = even_out(tex)
        if t.get("seams"):
            tex = seamless(tex, t["seams"])
        Image.fromarray(np.clip(tex, 0, 255).astype(np.uint8)).save(out / name, quality=82)
        med = np.median(tex.reshape(-1, 3), axis=0).astype(int)
        print(f"{name}: {t['size'][0]}x{t['size'][1]} from {t['source']}{note}, median #{med[0]:02x}{med[1]:02x}{med[2]:02x}")
        if check:
            im = Image.fromarray(img)
            ImageDraw.Draw(im).line([*map(tuple, t["quad"]), tuple(t["quad"][0])], fill=(255, 0, 0), width=3)
            Path(check).mkdir(parents=True, exist_ok=True)
            im.save(Path(check) / f"src-{name}", quality=80)
    for name, base in cfg.get("turned", {}).items():
        Image.open(out / base).transpose(Image.Transpose.ROTATE_90).save(out / name, quality=82)
        print(f"{name}: {base} turned on its side")


def wall_textures(site, check=None):
    """`domus prep wall-textures`: the wall textures "wall_textures" in data/prep.json names, into
    data/walls/; with `check`, a folder for the frames with their quads drawn on."""
    cfg = config(site, "wall_textures")
    out = site.path / "data" / "walls"
    out.mkdir(parents=True, exist_ok=True)

    def piece(name):
        p = cfg["pieces"][name]
        return even_out(rectify(np.asarray(Image.open(raw_dir(site) / p["source"]).convert("RGB")), p["quad"], p["size"]))

    def save(a, name):
        a = a * cfg.get("gain", 1.0)
        Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(out / name, quality=82)
        med = np.median(a.reshape(-1, 3), axis=0).astype(int)
        print(f"{name}: {a.shape[1]}x{a.shape[0]}, median #{med[0]:02x}{med[1]:02x}{med[2]:02x}")

    for name, t in cfg.get("tiles", {}).items():
        save(seamless(piece(t["piece"]), t["seams"]), name)
    # Boards between posts: one bay with half a post at each side, so a post stands at every seam
    # (the build puts the seams at the wall's vertices). The post is brightened to the boards' level.
    for name, b in cfg.get("bays", {}).items():
        boards, post = piece(b["piece"]), piece(b["post"])
        post = post * (boards.mean(axis=(0, 1)) / post.mean(axis=(0, 1)))
        h = post.shape[1] // 2
        save(np.hstack([post[:, h:], boards, post[:, :h]]), name)
    if check:
        Path(check).mkdir(parents=True, exist_ok=True)
        for name, p in cfg["pieces"].items():
            im = Image.open(raw_dir(site) / p["source"]).convert("RGB")
            ImageDraw.Draw(im).line([*map(tuple, p["quad"]), tuple(p["quad"][0])], fill=(255, 0, 0), width=3)
            im.save(Path(check) / f"src-{name}.jpg", quality=80)


def faces_by_group(site):
    """The house's faces (as the house's source gives the house: domus.model.house_source), by their group."""
    g = terrain.ground(site)
    hm = house_source.house(site).model(lambda e, n: g.sample(np.atleast_1d(e), np.atleast_1d(n)))
    groups = {}
    for f in hm.faces:
        groups.setdefault(f.spec.get("group", f.id), []).append(f)
    return groups


def face_frames(faces):
    """Per face: (u range, site point at u0, unit direction per metre of u)."""
    out = []
    for f in faces:
        v, uv = f.mesh.vertices, f.mesh.uv
        i0 = int(np.argmin(uv[:, 0] + 1e-3 * uv[:, 1]))
        i1 = int(np.argmax(uv[:, 0] - 1e-3 * uv[:, 1]))
        u0, u1 = uv[i0, 0], uv[i1, 0]
        d = (v[i1, :2] - v[i0, :2]) / (u1 - u0)
        out.append((f.id, u0, u1, v[i0, :2], d))
    return out


def texel_grid(faces):
    fr = face_frames(faces)
    u_max = max(u1 for _, _, u1, _, _ in fr)
    z0, z1 = min(f.z0 for f in faces), max(f.z1 for f in faces)
    W, H = int(np.ceil(u_max * PPM)), int(np.ceil((z1 - z0) * PPM))
    U, Z = np.meshgrid((np.arange(W) + 0.5) / PPM, z1 - (np.arange(H) + 0.5) / PPM)
    E = np.full(U.shape, np.nan)
    N = np.full(U.shape, np.nan)
    nrm = np.zeros(U.shape + (2,))
    for _, u0, u1, p0, d in fr:
        sel = (U >= u0 - 1e-9) & (U <= u1 + 1e-9)
        E[sel] = p0[0] + d[0] * (U[sel] - u0)
        N[sel] = p0[1] + d[1] * (U[sel] - u0)
        nrm[sel] = [d[1], -d[0]]                 # outward: right of the left-to-right direction
    return (W, H), (u_max, z0, z1), np.stack([E, N, Z], -1), nrm


def from_photo(site, spec, faces):
    (W, H), ext, _, _ = texel_grid(faces)
    z1 = ext[2]
    img = cv2.imread(str(raw_dir(site) / spec["photo"]))
    tex = np.zeros((H, W, 3), np.uint8)
    mask = np.zeros((H, W), np.uint8)
    for pairs in spec["faces"].values():
        src = np.float32([((u) * PPM, (z1 - z) * PPM) for (u, z), _ in pairs])
        dst = np.float32([p for _, p in pairs])
        Hm = cv2.getPerspectiveTransform(src, dst)       # texture px -> photo px
        warped = cv2.warpPerspective(img, Hm, (W, H), flags=cv2.WARP_INVERSE_MAP | cv2.INTER_AREA)
        us = [u for (u, _), _ in pairs]
        c0, c1 = int(round(min(us) * PPM)), int(round(max(us) * PPM))
        tex[:, c0:c1] = warped[:, c0:c1]
        mask[:, c0:c1] = 255
    return tex, mask, ext


class Frames:
    """A placed reconstruction of the site's: its frames' cameras and their placement."""

    def __init__(self, site, tag):
        d = np.load(site.path / "data" / "video" / f"{tag}-sfm.npz")
        self.site, self.tag, self.main = site, tag, tag.split("-")[0]
        self.names = [str(n) for n in d["frame_names"]]
        self.rvecs, self.tvecs = d["rvecs"], d["tvecs"]
        self.f, self.k1, self.cx, self.cy = d["intrinsics"]
        p = np.array(json.loads((site.path / "data" / "video" / f"{tag}-georef.json").read_text(encoding="utf-8"))["params"])
        self.s, self.R, self.t = np.exp(p[0]), Rotation.from_rotvec(p[1:4]).as_matrix(), p[4:7]

    def registered(self):
        return [n for n, rv in zip(self.names, self.rvecs) if np.all(np.isfinite(rv))]

    def image(self, name):
        clip, stem = name.split("/", 1) if "/" in name else (self.main, name)
        return cv2.imread(str(raw_dir(self.site) / "frames6" / clip / f"{stem}.jpg"))

    def project(self, name, X):
        i = self.names.index(name)
        rc = Rotation.from_rotvec(self.rvecs[i]).as_matrix()
        pc = ((X - self.t) @ self.R / self.s) @ rc.T + self.tvecs[i]
        z = pc[:, 2]
        x, y = pc[:, 0] / z, pc[:, 1] / z
        rad = 1 + self.k1 * (x * x + y * y)
        return np.column_stack([self.f * rad * x + self.cx, self.f * rad * y + self.cy]), z

    def centre(self, name):
        i = self.names.index(name)
        rc = Rotation.from_rotvec(self.rvecs[i]).as_matrix()
        return self.s * self.R @ (-rc.T @ self.tvecs[i]) + self.t


def from_frames(site, spec, faces, margin=30, sharp=8.0):
    tag, sel = spec["frames"]
    cams = Frames(site, tag)
    names = cams.registered() if sel == "all" else sel
    (W, H), ext, X, nrm = texel_grid(faces)
    pts = X.reshape(-1, 3)
    n2 = nrm.reshape(-1, 2)
    ok_pt = np.isfinite(pts).all(1)
    scores, maps, used = [], [], []
    for name in names:
        uv, z = cams.project(name, np.where(ok_pt[:, None], pts, 0))
        v = cams.centre(name) - pts
        dist = np.linalg.norm(v, axis=1)
        facing = (v[:, 0] * n2[:, 0] + v[:, 1] * n2[:, 1]) / dist
        ok = ok_pt & (z > 0) & (uv[:, 0] > margin) & (uv[:, 0] < 2 * cams.cx - margin) & \
            (uv[:, 1] > margin) & (uv[:, 1] < 2 * cams.cy - margin) & (facing > 0.25)
        score = np.where(ok, facing / dist, 0.0)
        if score.max() <= 0:
            continue
        scores.append(score)
        maps.append(uv.astype(np.float32))
        used.append(name)
    best = np.max(scores, axis=0)
    acc = np.zeros((len(pts), 3))
    wsum = np.zeros(len(pts))
    for name, score, uv in zip(used, scores, maps):
        w = np.where(best > 0, (score / np.where(best > 0, best, 1)) ** sharp, 0.0)
        w[score <= 0] = 0
        if w.max() < 1e-3:
            continue
        img = cams.image(name)
        samp = cv2.remap(img, uv[:, 0].reshape(H, W), uv[:, 1].reshape(H, W), cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_REPLICATE).reshape(-1, 3)
        acc += samp * w[:, None]
        wsum += w
    tex = np.where(wsum[:, None] > 0, acc / np.maximum(wsum, 1e-9)[:, None], 255).reshape(H, W, 3)
    mask = (wsum > 0).reshape(H, W).astype(np.uint8) * 255
    return tex.astype(np.uint8), mask, ext


def apply_rects(mask, ext, spec):
    z1 = ext[2]
    H, W = mask.shape

    def rect(u0, u1, za, zb):
        c0, c1 = int(max(0, round(u0 * PPM))), int(min(W, round(u1 * PPM)))
        r0, r1 = int(max(0, round((z1 - zb) * PPM))), int(min(H, round((z1 - za) * PPM)))
        return slice(r0, r1), slice(c0, c1)
    if spec.get("keep"):
        keep = np.zeros_like(mask)
        for r in spec["keep"]:
            keep[rect(*r)] = 255
        mask = np.minimum(mask, keep)
    for r in spec.get("drop", []):
        mask[rect(*r)] = 0
    return mask


def house_textures(site, groups=()):
    """`domus prep house-textures`: the house's walls rectified from photos and placed video, as
    "house_textures" in data/prep.json says, into data/house/: the sources named in `groups`, else all of them."""
    out = site.path / "data" / "house"
    out.mkdir(parents=True, exist_ok=True)
    sources = config(site, "house_textures")["sources"]
    wanted = list(groups) or list(sources)
    groups = faces_by_group(site)
    index_path = out / "faces.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    for key in wanted:
        spec = sources[key]
        faces = groups[key]
        tex, mask, ext = (from_photo if "photo" in spec else from_frames)(site, spec, faces)
        mask = apply_rects(mask, ext, spec)
        cv2.imwrite(str(out / f"{key}.jpg"), tex, [cv2.IMWRITE_JPEG_QUALITY, 85])
        cv2.imwrite(str(out / f"{key}-mask.png"), mask)
        u_max, z0, z1 = ext
        index[key] = {"file": f"{key}.jpg", "mask": f"{key}-mask.png", "u0": 0.0, "u1": round(u_max, 3),
                      "z0": round(z0, 3), "z1": round(z1, 3), "ppm": PPM,
                      "source": spec.get("photo") or f"video {spec['frames'][0]} (registered frames)",
                      "note": spec.get("note", "")}
        print(f"{key}: {tex.shape[1]}x{tex.shape[0]} px, covers {100 * (mask > 0).mean():.0f}%")
    index_path.write_bytes((json.dumps(index, indent=1) + "\n").encode())
