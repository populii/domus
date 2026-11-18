"""The LiDAR point cloud over the site, classified: `domus points`.

The 1 m ground and surface models hold one height a square metre. The survey behind them has many points a square metre,
each classed as ground, building, vegetation and so on. This fetches the newest point cloud covering the model's extent,
crops it, and writes data/lidar/points.laz (NZTM2000, heights NZVD2016). The draft house's roof is then fitted to the
building points (domus.create), and `--ground` writes data/lidar/dem-points.tif, a finer ground from the ground points that
a site can choose with `[rasters] dem`.

Sources, both LINZ's surveys (CC BY 4.0):
- LINZ itself, through the LINZ Data Service's export API, with a DOMUS_LINZ_KEY that has the exports:read and exports:write
  scopes (a key's default scopes don't include them). The newest surveys are here first.
- OpenTopography's copies, which need no key but lag LINZ: a survey can be on LINZ a year before it's here.
`data/lidar-points-source.json` records which survey was used, to compare with the ground model's.

    domus points [--ground]
"""

import io
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import numpy as np
from pyproj import Transformer

from ...errors import DomusError
from ...geo import crs
from ...project import rasters
from . import linz, publicdata

LDS = "https://data.linz.govt.nz/services/api/v1.x"
LAZ = "application/vnd.laszip+copc"     # the only LAZ LINZ offers for point clouds: COPC, which is LAZ 1.4 any reader takes
EXPORT_WAIT_S = 900
CATALOG = "https://portal.opentopography.org/API/otCatalog"
BULK = "https://opentopography.s3.sdsc.edu/pc-bulk/"


class NoPointCloud(DomusError):
    """No point cloud covers the site: a site can do without one."""


def laz_path(site):
    """The LiDAR points over the site, data/lidar/points.laz."""
    return site.path / "data" / "lidar" / "points.laz"
def record_path(site):
    """Where the site's LiDAR points came from, data/lidar-points-source.json."""
    return site.path / "data" / "lidar-points-source.json"
def ground_dem_path(site):
    """The site's ground model made from the points, data/lidar/dem-points.tif."""
    return site.path / "data" / "lidar" / "dem-points.tif"
# LINZ's tile names carry the Topo50 sheet, sometimes a year, then the scale and the tile: CL2_BX24_2020_1000_0245.laz.
TILE_RE = re.compile(r"([A-Z]{2})(\d\d)_(?:\d{4}_)?(\d+)_(\d\d)(\d\d)\.la[sz]$", re.IGNORECASE)
TO_LONLAT = Transformer.from_crs(2193, 4326, always_xy=True)
GROUND, BUILDING = 2, 6
VEGETATION = (3, 4, 5)
NOISE = (7, 18)


def _get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": publicdata.USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def tile_bounds(name):
    """NZTM bounds of a tile from its name, or None."""
    m = TILE_RE.search(name)
    if not m:
        return None
    letters, col, scale, r, c = m.groups()
    return linz.tile_bounds(f"{letters}{col}_{scale}_{r}{c}.json")


def surveys(bounds):
    """OpenTopography's point clouds touching NZTM `bounds`, newest first: [(name, short name, end date)]."""
    xs, ys = TO_LONLAT.transform([bounds[0], bounds[2]], [bounds[1], bounds[3]])
    q = urllib.parse.urlencode({"productFormat": "PointCloud", "minx": min(xs), "miny": min(ys), "maxx": max(xs), "maxy": max(ys),
                                "detail": "true", "outputFormat": "json"})
    with _get(f"{CATALOG}?{q}") as r:
        found = json.load(r).get("Datasets", [])
    out = []
    for d in found:
        d = d["Dataset"]
        end = (d.get("temporalCoverage") or "").split("/")[-1].strip()
        out.append((d["name"], d["alternateName"], end))
    return sorted(out, key=lambda s: s[2], reverse=True)


def list_tiles(short_name):
    """The .laz files in a survey's bulk folder."""
    keys, token = [], None
    while True:
        q = {"list-type": "2", "prefix": f"{short_name}/", "max-keys": "1000"}
        if token:
            q["continuation-token"] = token
        with _get(f"{BULK}?{urllib.parse.urlencode(q)}") as r:
            root = ET.fromstring(r.read())
        ns = {"s3": root.tag.split("}")[0].strip("{")}
        keys += [k.text for k in root.iterfind("s3:Contents/s3:Key", ns) if k.text.lower().endswith(".laz")]
        token = root.findtext("s3:NextContinuationToken", namespaces=ns)
        if not token:
            return keys


def covering(keys, bounds):
    """Tile keys touching `bounds`, and whether they cover it."""
    x0, y0, x1, y1 = bounds
    hits = [(k, b) for k in keys if (b := tile_bounds(k)) and b[0] < x1 and b[2] > x0 and b[1] < y1 and b[3] > y0]
    probes = [(x, y) for x in (x0, (x0 + x1) / 2, x1) for y in (y0, (y0 + y1) / 2, y1)]
    covered = bool(hits) and all(any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for _, b in hits) for x, y in probes)
    return [k for k, _ in hits], covered


def find(bounds):
    """(survey name, short name, end date, [tile URLs]) of the newest survey whose tiles cover `bounds`, or None."""
    for name, short, end in surveys(bounds):
        keys, covered = covering(list_tiles(short), bounds)
        if covered:
            return name, short, end, [BULK + urllib.parse.quote(k) for k in keys]
    return None


def _lds(path, key=None, body=None):
    headers = {"User-Agent": publicdata.USER_AGENT}
    if key:
        headers["Authorization"] = f"key {key}"
    data = json.dumps(body).encode() if body is not None else None
    if data:
        headers["Content-Type"] = "application/json"
    url = path if path.startswith("http") else f"{LDS}/{path}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # LINZ says what was wrong in the body: a scope the key lacks, a format it doesn't offer.
        raise OSError(f"LINZ answered {e.code} to {url.split('/v1.x/')[-1]}: {e.read()[:500].decode(errors='replace')}") from e


def linz_survey(bounds):
    """(title, dataset API URL) of LINZ's newest point cloud covering NZTM `bounds`, or None."""
    import shapely

    xs, ys = TO_LONLAT.transform([bounds[0], bounds[2], bounds[0], bounds[2]], [bounds[1], bounds[1], bounds[3], bounds[3]])
    site = shapely.box(min(xs), min(ys), max(xs), max(ys))
    found, page = [], 1
    while True:
        rows = _lds(f"data/?kind=pointcloud&page_size=100&page={page}")
        found += [r for r in rows if r.get("kind") == "pointcloud"]
        if len(rows) < 100:
            break
        page += 1
    for row in sorted(found, key=lambda r: linz._year(r["title"]), reverse=True):
        d = _lds(row["url"])
        extent = d.get("data", {}).get("gridded_extent") or d.get("data", {}).get("extent")
        shape = shapely.from_wkb(bytes.fromhex(extent)) if isinstance(extent, str) else shapely.geometry.shape(extent) if extent else None
        if shape is not None and shape.contains(site):
            return row["title"], row["url"]
    return None


def linz_export(dataset_url, bounds, key, out_dir):
    """Ask LINZ to export the points inside `bounds` as LAZ, wait for it, and unpack it: the LAZ files' paths."""
    xs, ys = TO_LONLAT.transform([bounds[0], bounds[2], bounds[2], bounds[0], bounds[0]], [bounds[1], bounds[1], bounds[3], bounds[3], bounds[1]])
    job = _lds("exports/", key, {"name": "domus-points", "crs": f"EPSG:{crs.NZTM_EPSG}", "formats": {"pointcloud": LAZ},
                                 "items": [{"item": dataset_url}],
                                 "extent": {"type": "Polygon", "coordinates": [[[x, y] for x, y in zip(xs, ys)]]}})
    start = time.time()
    while job.get("state") == "processing":
        if time.time() - start > EXPORT_WAIT_S:
            raise OSError(f"LINZ's export was still going after {EXPORT_WAIT_S // 60} minutes")
        time.sleep(10)
        job = _lds(job["url"], key)
    if job.get("state") != "complete":
        raise OSError(f"LINZ's export ended {job.get('state')}")
    # The download redirects to a signed URL that takes no key, so the key isn't sent on.
    req = urllib.request.Request(job["download_url"], headers={"Authorization": f"key {key}", "User-Agent": publicdata.USER_AGENT})
    opener = urllib.request.build_opener(_DropAuthOnRedirect)
    with opener.open(req, timeout=600) as r:
        blob = r.read()
    out = []
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for name in z.namelist():
            if name.lower().endswith((".laz", ".las")):
                path = Path(out_dir) / Path(name).name
                path.write_bytes(z.read(name))
                out.append(path)
    if not out:
        raise OSError("LINZ's export held no LAZ files")
    return out


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.remove_header("Authorization")
        return new


def crop(urls, bounds, out_path):
    """Download each tile, keep the points inside `bounds`, and write them as one LAZ file. Returns the point count."""
    import laspy

    x0, y0, x1, y1 = bounds
    parts, header = [], None
    with tempfile.TemporaryDirectory() as tmp:
        for url in urls:
            path = Path(tmp) / "tile.laz"
            with _get(url, timeout=600) as r, open(path, "wb") as fh:
                shutil.copyfileobj(r, fh)
            las = laspy.read(path)
            keep = (las.x >= x0) & (las.x <= x1) & (las.y >= y0) & (las.y <= y1)
            header = header or las.header
            part = las.points[keep].copy()
            # Each tile has its own offset (and could have its own scale): put its coordinates on the first tile's.
            if not (np.array_equal(las.header.offsets, header.offsets) and np.array_equal(las.header.scales, header.scales)):
                for dim, real in zip("XYZ", (las.x[keep], las.y[keep], las.z[keep])):
                    i = "XYZ".index(dim)
                    part.array[dim] = np.round((np.asarray(real) - header.offsets[i]) / header.scales[i]).astype(part.array[dim].dtype)
            parts.append(part)
    out = laspy.LasData(laspy.LasHeader(point_format=header.point_format, version=header.version))
    out.header.offsets, out.header.scales = header.offsets, header.scales
    for vlr in header.vlrs:
        if vlr.user_id.strip("\0").lower() != "copc":     # COPC's own index (LINZ's tiles): laspy can't write it, and a crop needs none
            out.header.vlrs.append(vlr)
    out.points = laspy.ScaleAwarePointRecord(np.concatenate([p.array for p in parts]), header.point_format, header.scales,
                                             header.offsets)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part = out_path.with_suffix(".part.laz")       # so a failed write leaves no half a file in its place
    out.write(part)
    part.replace(out_path)
    return len(out.points)


def read(path):
    """The points in `path`, a site's points.laz (laz_path()), as arrays {x, y, z, cls, returns, return_no} on NZTM, or
    None if there's no such file."""
    import laspy

    path = Path(path)
    if not path.exists():
        return None
    las = laspy.read(path)
    return {"x": np.asarray(las.x), "y": np.asarray(las.y), "z": np.asarray(las.z), "cls": np.asarray(las.classification),
            "return_no": np.asarray(las.return_number), "returns": np.asarray(las.number_of_returns)}


def on_site(pts, grid, mask=None):
    """E, N on the site's grid and height of the points (all, or those in `mask`)."""
    sel = slice(None) if mask is None else mask
    e, n = grid.from_nztm(pts["x"][sel], pts["y"][sel])
    return np.asarray(e), np.asarray(n), pts["z"][sel]


def ground_grid(pts, bounds, res=0.5):
    """A ground model on NZTM from the ground points: the lowest in each cell, NaN where there are none. Returns
    (grid, transform)."""
    from rasterio.transform import from_origin

    x0, y0, x1, y1 = bounds
    w, h = int(np.ceil((x1 - x0) / res)), int(np.ceil((y1 - y0) / res))
    g = pts["cls"] == GROUND
    c = ((pts["x"][g] - x0) / res).astype(int)
    r = ((y1 - pts["y"][g]) / res).astype(int)
    ok = (c >= 0) & (c < w) & (r >= 0) & (r < h)
    grid = np.full(h * w, np.inf)
    np.minimum.at(grid, r[ok] * w + c[ok], pts["z"][g][ok])
    grid[~np.isfinite(grid)] = np.nan
    return grid.reshape(h, w), from_origin(x0, y1, res, res)


def write_ground(site, pts, bounds, path=None, res=0.5):
    """The site's dem-points.tif (or `path`): the ground points' grid, its gaps (under buildings, and where none reached
    the ground) filled from the 1 m ground model. Returns the share of cells with a ground point."""
    path = ground_dem_path(site) if path is None else path
    import rasterio

    from ...geo import raster

    grid, transform = ground_grid(pts, bounds, res)
    seen = np.isfinite(grid)
    if not seen.all():
        rows, cols = np.nonzero(~seen)
        xs, ys = rasterio.transform.xy(transform, rows, cols)
        dem = raster.Raster(rasters.path(site, "dem"), site.grid.epsg)
        col, row = dem.inverse @ (np.asarray(xs), np.asarray(ys))
        from scipy.ndimage import map_coordinates

        grid[~seen] = map_coordinates(dem.data[0], [np.asarray(row) - 0.5, np.asarray(col) - 0.5], order=1, cval=np.nan)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=grid.shape[1], height=grid.shape[0], count=1, dtype="float32",
                       crs=f"EPSG:{crs.NZTM_EPSG}", transform=transform, nodata=-9999.0, compress="deflate", predictor=3) as dst:
        dst.write(np.nan_to_num(grid, nan=-9999.0).astype(np.float32), 1)
    return float(seen.mean())


def fetch(site, ground=False, say=print):
    """`domus points`: the classified LiDAR point cloud over the site, from LINZ (with a key that can export) or
    OpenTopography, written to data/lidar/points.laz; with `ground`, a 0.5 m ground model from its ground points too
    (data/lidar/dem-points.tif). Gives {points, path, ground_share}: how many points, where, and the share of the
    ground model's cells a ground point reached (None without `ground`)."""
    bounds = linz.site_bounds(site)
    key = os.environ.get("DOMUS_LINZ_KEY", "").strip()
    count = None
    if key:
        try:
            hit = linz_survey(bounds)
            if not hit:
                say("  LINZ has no point cloud covering this site; trying OpenTopography")
            else:
                name, dataset = hit
                say(f"Point cloud: {name}, exporting from LINZ (usually under a minute)")
                with tempfile.TemporaryDirectory() as tmp:
                    count = crop([p.as_uri() for p in linz_export(dataset, bounds, key, tmp)], bounds, laz_path(site))
                short, end, urls = dataset.rstrip("/").rsplit("/", 1)[-1], "", [dataset]
        except (OSError, ValueError, KeyError) as e:
            say(f"  LINZ's export didn't work ({e}). A key needs the exports:read and exports:write scopes. Trying OpenTopography")
    if count is None:
        found = find(bounds)
        if not found:
            raise NoPointCloud("No point cloud covers this site. The 1 m models are all there is.")
        name, short, end, urls = found
        say(f"Point cloud: {name} (to {end}), {len(urls)} tile(s) from OpenTopography; whole tiles, tens of MB each")
        count = crop(urls, bounds, laz_path(site))
    area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
    say(f"  {count} points, {count / area:.1f} a square metre -> {laz_path(site)}")
    record_path(site).write_text(json.dumps({"title": name, "id": short, "to": end, "tiles": urls}, indent=2) + "\n", encoding="utf-8")
    dem = json.loads((site.path / "data" / "linz-sources.json").read_text(encoding="utf-8")).get("ground model", {}).get("title") \
        if (site.path / "data" / "linz-sources.json").exists() else None
    if dem:
        say(f"  The ground model is {dem}: if the years differ, anything changed between them won't agree.")
    share = None
    if ground:
        share = write_ground(site, read(laz_path(site)), bounds)
        say(f"  ground points reach {share:.0%} of the 0.5 m cells -> {ground_dem_path(site)}. Use it with [rasters] dem = \"data/lidar/dem-points.tif\"")
    return {"points": count, "path": laz_path(site), "ground_share": share}
