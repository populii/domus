"""The site's ground: its ground model (the DEM) with data/site/ground-fixes.geojson applied, and the ground's
triangulated surface, with walls cut in as breaklines, built from it about the model's origin.

The geometry is domus.tin's, which reads no site; this module reads the site's and passes it on. Its names are
kept here for the site tools that use them.
"""


from . import crs, raster, tin
from . import walls as walls_mod
from .crs import settings
from .mesh import resample_line  # noqa: F401 (as it was, here)
from .project.site import cached
from .tin import Ground, contours, cut_cells, distance_to_polyline, grid, outline_points  # noqa: F401
from .walls import bay_levels, line_normals  # noqa: F401 (as they were, here)


def fixes_path():
    """The site's ground fixes, data/site/ground-fixes.geojson."""
    return crs.ROOT / "data" / "site" / "ground-fixes.geojson"


def read_fixes(path=None):
    """tin.read_fixes: the fix areas and points, from the site's data/site/ground-fixes.geojson unless given."""
    return tin.read_fixes(fixes_path() if path is None else path)


@cached
def ground():
    """Ground model without walls: the LiDAR DEM with data/site/ground-fixes.geojson applied."""
    return Ground(raster.dem(), *read_fixes())


def wall_levels(wall, spacing=0.25, probe=0.6):
    """walls.wall_levels on the site's ground: a wall's points, normals, chainage, toe and top."""
    return walls_mod.wall_levels(wall, ground(), spacing, probe)


def build(extent, step, walls=(), surface=None, edges=()):
    """tin.build on the site's ground, about the model's origin: the triangulated ground over `extent`."""
    return tin.build(extent, step, ground(), settings()["model"]["origin"], walls, surface, edges)
