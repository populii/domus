"""The site's canopy and plants: candidate crowns mapped from its LiDAR and aerial photo (`domus canopy`, written to
data/sources/canopy.geojson for curating), its curated plants (data/site/trees.geojson), and what the build draws
them from. The arithmetic and the drawing are domus.vegetation's, which reads no site.
"""

import numpy as np
import shapely
from shapely.geometry import Polygon

from . import crs, geojson, raster, terrain
from .crs import settings, site_epsg
from .vegetation import (
    STEP,
    aerial_rgb,
    as_json,
    canopy_height,
    crowns,
    grid,
    guess_kind,
    removed_areas,
    segment,
    vegetation_mask,
)


def trees_path():
    """The site's curated plants, data/site/trees.geojson."""
    return crs.ROOT / "data" / "site" / "trees.geojson"


def lidar_name():
    """What the viewer calls the site's LiDAR: [labels] lidar."""
    return settings().get("labels", {}).get("lidar", "LiDAR")


def read(path=None):
    """The site's curated plants, data/site/trees.geojson's features (none without the file)."""
    return geojson.read_optional(trees_path() if path is None else path)


def aerial_ndvi(E, N, step=STEP):
    """Mean NDVI, (NIR - red) / (NIR + red), over each cell from the near-infrared photo; NaN where there is none.
    Leaves about 0.3-0.5; roofs, paint, paving and bare ground below 0.1, green or not."""
    if not raster.NIR.exists():
        return np.full(E.shape, np.nan)
    a = raster.nir()
    red, nir = np.zeros(E.shape), np.zeros(E.shape)
    for dx in (-step / 3, 0.0, step / 3):
        for dy in (-step / 3, 0.0, step / 3):
            red += a.sample(E + dx, N + dy, band=0)
            nir += a.sample(E + dx, N + dy, band=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(red + nir > 0, (nir - red) / (nir + red), np.nan)


def house_mask(E, N, buffer=1.0):
    """Cells under the house (its walls, bay windows and porches from data/site/house.geojson), widened by
    `buffer`: the eaves (0.35 m) and the half-diagonal of a 1 m LiDAR cell that takes the roof's
    height where it overlaps the eaves."""
    feats = geojson.read_optional(crs.ROOT / "data" / "site" / "house.geojson")
    polys = [Polygon(f["geometry"]["coordinates"][0]) for f in feats
             if f["geometry"]["type"] == "Polygon" and (f["properties"]["id"] == "house-walls"
                                                         or f["properties"].get("part") in ("bay", "porch"))]
    return shapely.contains_xy(shapely.union_all(polys).buffer(buffer), E, N)


def map_canopy(extent, removed=(), ground=None):
    """Crown candidates over `extent` from the LiDAR and the aerial.

    Canopy inside the `removed` polygons (gone since the survey) is cut out before the crowns
    are found, and reported as crowns of its own with `removed` set. Returns the candidates
    (dicts from crowns()) and the grids {E, N, chm, rgb, veg, labels}."""
    E, N = grid(extent)
    ground = ground or terrain.ground()
    gh = ground.sample(E, N)
    chm = canopy_height(E, N, ground, raster.dsm())
    rgb = aerial_rgb(E, N, raster.aerial())
    veg = vegetation_mask(chm, rgb, exclude=house_mask(E, N), ndvi=aerial_ndvi(E, N))
    gone = [shapely.contains_xy(poly, E, N) for poly in removed]
    lab = segment(chm, veg & ~np.any(gone, axis=0) if gone else veg)
    cands = crowns(lab, chm, rgb, E, N, gh)
    for m in gone:
        m = m & veg & (lab == 0)
        for c in crowns(m.astype(int), chm, rgb, E, N, gh) if m.any() else []:
            c["segment"] = int(lab.max() + 1)
            c["removed"] = True
            lab[m] = c["segment"]
            cands.append(c)
    return cands, {"E": E, "N": N, "chm": chm, "rgb": rgb, "veg": veg, "labels": lab}


class OnDemand:
    """One of the site's rasters, read the first time something samples it: only some plants read the surface model,
    and a site may have none."""

    def __init__(self, read):
        self.read = read

    def sample(self, *args, **kwargs):
        return self.read().sample(*args, **kwargs)


def drawn_from():
    """What the build draws the site's plants from, as vegetation.layers takes it: the lot (the boundary's first
    feature), the surface model and the aerial photo (read when a plant needs them), and the viewer's words."""
    lot = Polygon(geojson.read(crs.ROOT / "data" / "site" / "boundary.geojson")["features"][0]["geometry"]["coordinates"][0])
    return {"lot": lot, "surface": OnDemand(raster.dsm), "aerial": OnDemand(raster.aerial), "lidar": lidar_name(),
            "looks_from_videos": bool(settings().get("video", {}).get("clouds")),
            "bed_floor_source": settings().get("labels", {}).get("bed_floor_source",
                                                                "Outlines as the beds in data/site/trees.geojson")}


def map_site():
    """`domus canopy`: candidate crowns from the LiDAR, written for curating into data/site/trees.geojson."""
    extent = settings()["model"]["extent"]
    cands, grids = map_canopy(extent, removed_areas(read()))
    fc = geojson.collection([as_json(c) for c in cands], "canopy",
                            "Candidate crowns from the LiDAR canopy (surface less ground) and the aerial; "
                            "data/site/trees.geojson holds the curated features", epsg=site_epsg())
    out = crs.ROOT / settings().get("rasters", {}).get("canopy", "data/sources/canopy.geojson")
    geojson.write(out, fc)
    kinds = {}
    for c in cands:
        k = "removed" if c.get("removed") else guess_kind(c)
        kinds[k] = kinds.get(k, 0) + 1
    print(f"{len(cands)} crowns over {grids['veg'].sum() * STEP ** 2:.0f} m2 of canopy: {kinds}; wrote {out}")
