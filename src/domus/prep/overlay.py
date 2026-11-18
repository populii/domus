"""The model's parts drawn over frames of a placed clip, through the clip's own cameras, to check each one's place.

    domus prep overlay TAG OUT_DIR FRAME [FRAME ...] [--frames DIR] [--georef FILE.json] [--only GROUP,...]
                       [--option NAME] [--see-through] [--suffix TEXT]

TAG is a placed reconstruction (data/video/<TAG>-sfm.npz, placed by <TAG>-georef.json or --georef); its frames are
read from --frames, or sources/raw/frames6/<clip>/, the clip being TAG up to its first "-". Each FRAME (such as
t012.500) is written to OUT_DIR as <TAG>-<FRAME><suffix>.jpg with, unless --only names some, every group drawn:

  house      the walls' feet on the ground and their corners up to the soffit (white), a face's plaster top
             (`plinth_top`, white, dashed), windows and doors with a sill and a head (light blue), and the house's
             other parts outlined on the ground (pink); only the faces turned to the camera, unless --see-through
  walls      each existing wall's top and its foot, a colour for each wall, named
  features   the small features by their geometry: a post or pole from its base to its top, a mark as a cross on the
             ground, a fence or gate along the ground and along its top, a shed, box or pad round its foot and its top
  ground     the ground fixes' lines that have heights (thin grey)
  boundary   the lot's boundary on the ground and the survey marks (red)
  plants     lawns, beds and patches outlined on the ground, and stumps (light green)
--option NAME also draws that wall option's walls (their tops, white and dashed over black). The house hides what it
stands in front of: everything but the house itself is drawn only where the camera sees it past the house, unless
--see-through.
"""

import itertools
from pathlib import Path

import numpy as np
import shapely
from PIL import Image, ImageDraw

from ..errors import DomusError
from ..geo import geojson
from ..model import house_source, terrain
from .fitting import Placed

GROUPS = ("house", "walls", "features", "ground", "boundary", "plants")
WALL_COLOURS = ((255, 230, 0), (0, 230, 255), (0, 220, 60), (255, 140, 0), (200, 120, 255), (255, 90, 90))


def dense(line, step=0.05):
    """Points about every `step` metres along a 3D polyline, so its projection bends as the lens does."""
    line = np.asarray(line, float)
    out = []
    for a, b in itertools.pairwise(line):
        n = max(2, int(np.linalg.norm(b - a) / step))
        out.extend(a + (b - a) * t for t in np.linspace(0, 1, n, endpoint=False))
    out.append(line[-1])
    return np.array(out)


class Canvas:
    """A frame to draw on, through its camera."""

    def __init__(self, img, placed, index):
        self.d, self.size, self.placed, self.index = ImageDraw.Draw(img), img.size, placed, index
        self.hide = None            # points -> where something stands between them and the camera

    def line(self, line3, colour, width=2, label=None, dash=False):
        if len(line3) < 2:
            return
        pts = dense(line3)
        px, z = self.placed.project(pts, self.index)
        w, h = self.size
        ok = (z > 0.15) & (px[:, 0] > -300) & (px[:, 0] < w + 300) & (px[:, 1] > -300) & (px[:, 1] < h + 300)
        if self.hide is not None:
            ok &= ~self.hide(pts)
        run = []
        for k, (p, good) in enumerate(zip(px, ok, strict=True), start=1):
            if good and not (dash and (k // 6) % 2):
                run.append((float(p[0]), float(p[1])))
            else:
                if len(run) > 1:
                    self.d.line(run, fill=colour, width=width)
                run = []
        if len(run) > 1:
            self.d.line(run, fill=colour, width=width)
        if label is not None and ok.any():
            j = np.flatnonzero(ok)
            j = j[len(j) // 2]
            if 0 < px[j, 0] < w and 0 < px[j, 1] < h:
                self.d.text((px[j, 0] + 5, px[j, 1] - 14), label, fill=colour)

    def upright(self, e, n, z0, z1, colour, width=3, label=None):
        self.line([[e, n, z0], [e, n, z1]], colour, width, label)

    def cross(self, e, n, z, colour, label=None, r=0.08):
        self.line([[e - r, n, z], [e + r, n, z]], colour, 2)
        self.line([[e, n - r, z], [e, n + r, z]], colour, 2, label)


class Scene:
    """What the site's layers put where, for drawing: the ground under it and each group's lines."""

    def __init__(self, site, option=None):
        self.site = site
        self.g = terrain.ground(site)
        layers = site.path / "data" / "site"
        self.layer = lambda name: geojson.read_optional(layers / name)
        the_house = house_source.house(site)        # house.geojson and [house], or the house's IFC
        self.house = {f["properties"].get("id"): f for f in the_house.features}
        self.fl = the_house.settings.get("fl_upper")
        self.option_walls = []
        if option:
            from .. import options
            from ..options import design

            path = site.path / "options" / (option if option.endswith(".toml") else f"{option}.toml")
            if not path.exists():
                raise DomusError(f"There's no wall option {path}")
            for w in design.Design(options.load(path, site), site=site).wall_levels():
                pts, _, _, _, top = terrain.wall_levels(w, site=site)
                self.option_walls.append((w["id"], np.column_stack([pts, top])))

    def z(self, e, n):
        return float(self.g.sample(np.array([e]), np.array([n]))[0])

    def on_ground(self, xy, lift=0.02):
        xy = np.asarray(xy, float)[:, :2]
        return np.column_stack([xy, self.g.sample(xy[:, 0], xy[:, 1]) + lift])

    def foot(self, xy, out, step=0.25, lift=0.02):
        """A line on the ground sampled every `step`, the ground read a little `out` from it (outside a wall)."""
        xy = np.asarray(xy, float)[:, :2]
        pts = []
        for a, b in itertools.pairwise(xy):
            n = max(2, int(np.linalg.norm(b - a) / step) + 1)
            pts.extend(a + (b - a) * t for t in np.linspace(0, 1, n))
        pts = np.array(pts)
        probe = pts + np.asarray(out, float)
        return np.column_stack([pts, self.g.sample(probe[:, 0], probe[:, 1]) + lift])

    def blocked(self, camera, see_through):
        """points -> where the house stands between them and the camera (in plan), or None."""
        if "house-walls" not in self.house or see_through:
            return None
        ring = np.asarray(self.house["house-walls"]["geometry"]["coordinates"][0], float)[:, :2]
        house = shapely.Polygon(ring).buffer(-0.3)          # its walls, less a margin: what stands at its foot shows
        if house.is_empty or shapely.contains_xy(house, camera[0], camera[1]):
            return None
        shapely.prepare(house)

        def hide(pts):
            pts = np.asarray(pts, float)[:, :2]
            sight = shapely.linestrings(np.stack([np.broadcast_to(camera[:2], pts.shape), pts], axis=1))
            return shapely.intersects(sight, house)

        return hide

    def draw(self, c, camera, groups, see_through=False):
        hide = self.blocked(camera, see_through)
        if "house" in groups and "house-walls" in self.house:
            self.draw_house(c, camera, see_through, hide)
        c.hide = hide
        if "boundary" in groups:
            lots = [f for f in self.layer("boundary.geojson") if f["geometry"]["type"] == "Polygon"]
            if lots:
                c.line(self.on_ground(lots[0]["geometry"]["coordinates"][0]), (255, 40, 40), 2, "boundary")
            for f in self.features():
                if f["properties"].get("kind") == "mark" and f["geometry"]["type"] == "Point":
                    e, n = f["geometry"]["coordinates"][:2]
                    if np.isfinite(self.z(e, n)):
                        c.cross(e, n, self.z(e, n) + 0.02, (255, 40, 40), f["properties"].get("name", "")[:12])
        if "walls" in groups:
            for k, f in enumerate(self.layer("walls-existing.geojson")):
                line = np.asarray(f["geometry"]["coordinates"], float)
                p = f["properties"]
                colour = WALL_COLOURS[k % len(WALL_COLOURS)]
                top = line if line.shape[1] >= 3 else self.on_ground(line, 0.0) + [0, 0, float(p.get("height_m", 1.0))]
                c.line(top, colour, 3, f"{p.get('id', '')} top")
                toe = p.get("toe_m")
                foot = np.column_stack([line[:, :2], np.broadcast_to(toe, len(line))]) if toe is not None else self.on_ground(line)
                c.line(foot, colour, 1, f"{p.get('id', '')} foot")
        if "features" in groups:
            for f in self.features():
                self.draw_feature(c, f)
        if "ground" in groups:
            for f in self.layer("ground-fixes.geojson"):
                g = f["geometry"]
                if g["type"] == "LineString" and len(g["coordinates"][0]) >= 3:
                    c.line(np.asarray(g["coordinates"], float)[:, :3], (190, 190, 190), 1, f["properties"].get("id"))
        if "plants" in groups:
            for f in self.layer("trees.geojson"):
                p, g = f["properties"], f["geometry"]
                if p.get("kind") in ("lawn", "bed", "patch") and g["type"] == "Polygon":
                    c.line(self.on_ground(g["coordinates"][0], 0.03), (90, 255, 90), 1, p.get("id"))
                elif p.get("kind") == "stump" and g["type"] == "Point":
                    e, n = g["coordinates"][:2]
                    if np.isfinite(self.z(e, n)):
                        c.cross(e, n, self.z(e, n) + 0.05, (90, 255, 90), p.get("id"), 0.12)
        for wid, top in self.option_walls:
            c.line(top, (0, 0, 0), 4)
            c.line(top, (255, 255, 255), 2, wid, dash=True)

    def features(self):
        return [f for path in sorted((self.site.path / "data" / "site").glob("features-*.geojson")) for f in geojson.read_optional(path)]

    def heights(self, p, ground):
        """A feature's base and top: its `base_m` or the ground, and its `top_m` or `height_m` over the base."""
        base = float(p["base_m"]) if "base_m" in p else ground
        top = float(p["top_m"]) if "top_m" in p else base + float(p.get("height_m", 0.0))
        return base, top

    def draw_feature(self, c, f):
        p, g = f["properties"], f["geometry"]
        kind, label = p.get("kind"), p.get("name", p.get("id", ""))[:24]
        if g["type"] == "Point":
            e, n = g["coordinates"][:2]
            gz = self.z(e, n)
            if not np.isfinite(gz) or kind == "mark":
                return
            base, top = self.heights(p, gz)
            if top > base:
                c.upright(e, n, base, top, (255, 255, 255), 3, label)
            else:
                c.cross(e, n, base + 0.02, (255, 255, 255), label)
        elif g["type"] == "LineString":
            line = self.on_ground(g["coordinates"])
            colour = (255, 140, 0) if kind in ("fence", "gate", "clothesline") else (220, 220, 220)
            c.line(line, colour, 2, label)
            if p.get("height_m"):
                c.line(line + [0, 0, float(p["height_m"])], colour, 2)
        elif g["type"] == "Polygon":
            ring = self.on_ground(g["coordinates"][0])
            colour = (160, 160, 160) if kind == "pad" else (255, 150, 200)
            c.line(ring, colour, 2, label)
            if kind != "pad":
                base, top = self.heights(p, float(np.nanmax(ring[:, 2])))
                if top > base:
                    c.line(np.column_stack([ring[:, :2], np.full(len(ring), top)]), colour, 1)

    def draw_house(self, c, camera, see_through, hide=None):
        hw = self.house["house-walls"]
        ring = np.asarray(hw["geometry"]["coordinates"][0], float)[:, :2]
        area2 = float(np.sum(ring[:-1, 0] * ring[1:, 1] - ring[1:, 0] * ring[:-1, 1]))
        edge = ring[1:] - ring[:-1]
        outward = np.column_stack([edge[:, 1], -edge[:, 0]]) * (1 if area2 > 0 else -1)
        mid = (ring[1:] + ring[:-1]) / 2
        seen = np.ones(len(edge), bool) if see_through else np.einsum("ij,ij->i", camera[:2] - mid, outward) > 0
        soffit = hw["properties"].get("soffit")
        gz = self.g.sample(ring[:, 0], ring[:, 1])
        for k in range(len(edge)):
            if seen[k]:
                c.line(self.foot(ring[k:k + 2], outward[k] / np.linalg.norm(outward[k]) * 0.05), (255, 255, 255), 2)
                if soffit is not None:
                    for j in (k, k + 1):
                        c.upright(ring[j, 0], ring[j, 1], gz[j], soffit, (255, 255, 255), 1)
        plinth = hw["properties"].get("plinth_top", {})
        for k, face in enumerate(hw["properties"].get("faces", [])):
            if face in plinth and seen[k]:
                c.line(np.column_stack([ring[k:k + 2], np.full(2, plinth[face])]), (255, 255, 255), 2, None, dash=True)
        for x in self.house.values():
            p, g = x["properties"], x["geometry"]
            if p.get("kind") in ("window", "door") and "sill_above_fl" in p and "head_above_fl" in p and self.fl is not None:
                seg = np.asarray(g["coordinates"], float)[:, :2]
                m = seg.mean(0)
                along = [(m - ring[k]) @ edge[k] for k in range(len(edge))]
                dist = [abs(edge[k][0] * (m - ring[k])[1] - edge[k][1] * (m - ring[k])[0]) / np.linalg.norm(edge[k])
                        + (0 if 0 <= along[k] <= edge[k] @ edge[k] else 9) for k in range(len(edge))]
                if not seen[int(np.argmin(dist))]:
                    continue
                lo, hi = self.fl + p["sill_above_fl"], self.fl + p["head_above_fl"]
                box = np.vstack([np.column_stack([seg, np.full(len(seg), lo)]), np.column_stack([seg[::-1], np.full(len(seg), hi)]),
                                 np.column_stack([seg[:1], [lo]])])
                c.line(box, (120, 200, 255), 1, p.get("id"))
            elif p.get("part") and p.get("id") != "house-walls" and g["type"] == "Polygon":
                c.hide = hide                       # a porch or a deck is outside the walls: the house can hide it
                c.line(self.on_ground(g["coordinates"][0]), (255, 150, 200), 2, p.get("id"))
                c.hide = None


def draw_frames(site, tag, out, frames, frames_dir=None, georef=None, only=None, option=None, see_through=False,
                suffix=""):
    """`domus prep overlay`: the model's parts drawn over `frames` of placed reconstruction `tag`, into the folder
    `out`; the module's docstring says how. `only` is some of the groups, a list or comma-separated."""
    groups = set(GROUPS) if not only else set(only.split(",") if isinstance(only, str) else only)
    if groups - set(GROUPS):
        raise DomusError(f"--only takes {', '.join(GROUPS)}")
    placed = Placed(site, tag, georef)
    frames_dir = Path(frames_dir) if frames_dir else site.path / "sources" / "raw" / "frames6" / tag.split("-")[0]
    scene = Scene(site, option)
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for frame in frames:
        cam = placed.camera(frame)
        if cam is None:
            print(frame, "is not a registered frame of", tag)
            continue
        src = frames_dir / f"{frame}.jpg"
        if not src.exists():
            raise DomusError(f"There's no frame {src}: give the clip's frames folder with --frames")
        img = Image.open(src).convert("RGB")
        scene.draw(Canvas(img, placed, placed.names.index(frame)), cam[1], groups, see_through)
        picture = out_dir / f"{tag}-{frame}{suffix}.jpg"
        img.save(picture, quality=88)
        print(picture, "camera at E {:.2f} N {:.2f} H {:.2f}".format(*cam[1]))
