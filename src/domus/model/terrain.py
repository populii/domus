"""The site's ground: its ground model (the DEM) with data/site/ground-fixes.geojson applied, and the ground's
triangulated surface, with walls cut in as breaklines, built from it about the model's origin. The ground as the build
draws it, with the site's existing walls cut in, is the ground its wall options are worked out on (existing()).

The geometry is domus.tin's, which reads no site; this module reads the site it's handed and passes it on. Its names
are kept here for the site tools that use them, which call ground(), wall_levels() and build() without a site
(deprecated, and they say so): they then read the current one.
"""


from ..geo import geojson
from ..geo.mesh import resample_line  # noqa: F401 (as it was, here)
from ..project import rasters
from ..project.site import cached, given
from . import tin
from . import walls as walls_mod
from .tin import Ground, contours, cut_cells, distance_to_polyline, grid, outline_points  # noqa: F401
from .walls import bay_levels, line_normals  # noqa: F401 (as they were, here)


def fixes_path(site):
    """The site's ground fixes, data/site/ground-fixes.geojson."""
    return site.path / "data" / "site" / "ground-fixes.geojson"


def read_fixes(site, path=None):
    """tin.read_fixes: the fix areas and points, from the site's data/site/ground-fixes.geojson unless given."""
    return tin.read_fixes(fixes_path(site) if path is None else path)


@cached
def ground(site):
    """Ground model without walls: the LiDAR DEM with data/site/ground-fixes.geojson applied."""
    return Ground(rasters.dem(site), *read_fixes(site))


@cached
def drawn(site):
    """The ground as the build draws it: ground() with the site's existing walls (data/site/walls-existing.geojson)
    cut in, triangulated over the model's extent at its grid step."""
    model = site.settings()["model"]
    walls = [walls_mod.from_feature(f) for f in geojson.read_optional(site.path / "data" / "site" / "walls-existing.geojson")]
    return build(model["extent"], model["terrain_step_m"], walls, site=site)


@cached
def existing(site):
    """Today's best ground, as the wall options are designed on it and measured from it: the ground as the build draws
    it (drawn()), read in its own triangles (tin.DrawnGround). That's the LiDAR only where nothing better is known, the
    ground fixes where there are some, and the existing walls' faces and tops."""
    return tin.DrawnGround(drawn(site), ground(site))


def wall_levels(wall, spacing=0.25, probe=0.6, site=None):
    """walls.wall_levels on the site's ground: a wall's points, normals, chainage, toe and top."""
    return walls_mod.wall_levels(wall, ground(given(site)), spacing, probe)


def build(extent, step, walls=(), surface=None, edges=(), site=None):
    """tin.build on the site's ground, about the model's origin: the triangulated ground over `extent`."""
    site = given(site)
    return tin.build(extent, step, ground(site), site.settings()["model"]["origin"], walls, surface, edges)
