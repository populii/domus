"""The house's parts in house.geojson: each says what it is in `part`, and nothing about one house is built in."""

import numpy as np
import pytest

from domus.errors import DomusError
from domus.model import house

# A 12 x 9 m house with its walls on the grid's axes, on flat ground at 40.
RING = [[1005.0, 2015.0], [1017.0, 2015.0], [1017.0, 2006.0], [1005.0, 2006.0], [1005.0, 2015.0]]
ground = lambda e, n: np.full(np.shape(e), 40.0)


def walls():
    return {"geometry": {"type": "Polygon", "coordinates": [RING]}, "properties": {"id": "house-walls", "soffit": 42.8}}


def part(geom_type, coords, **props):
    return {"geometry": {"type": geom_type, "coordinates": coords}, "properties": props}


def box(x0, y0, x1, y1):
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]


def build(*parts):
    return house.build([walls(), *parts], 40.5, 25.0, 43.0, 0.4, ground)


def bounds(meshes):
    v = np.vstack([m.vertices for m, _ in meshes])
    return v.min(axis=0), v.max(axis=0)


@pytest.mark.parametrize(("rect", "side_walls", "faces"), [
    ((1017.0, 2008.0, 1019.0, 2011.0), ["north", "east"], {"porch-north", "porch-east"}),   # east of the house
    ((1008.0, 2004.0, 1011.0, 2006.0), ["west"], {"porch-west"}),                           # south of it
    ((1003.0, 2010.0, 1005.0, 2013.0), [], set()),                                          # west of it, open
])
def test_a_porch_on_any_side_has_its_walls_where_it_says(rect, side_walls, faces):
    m = build(part("Polygon", box(*rect), id="porch", part="porch", floor=40.6, roof_at_wall=43.0, roof_at_edge=42.7,
                   walls=side_walls))
    assert {f.id for f in m.faces if f.spec["kind"] == "porch"} == faces
    lo, hi = bounds(m.solids["porch"])
    x0, y0, x1, y1 = rect
    # the roof overhangs every side but the house's, and falls away from the house
    assert lo[0] <= x0 + 1e-9 and hi[0] >= x1 - 1e-9 and lo[1] <= y0 + 1e-9 and hi[1] >= y1 - 1e-9
    assert hi[2] == pytest.approx(43.0)


def test_a_porch_wall_on_the_house_side_is_refused():
    with pytest.raises(DomusError, match="is the house"):
        build(part("Polygon", box(1017.0, 2008.0, 1019.0, 2011.0), id="p", part="porch", floor=40.6,
                   roof_at_wall=43.0, roof_at_edge=42.7, walls=["west"]))


def test_steps_run_back_to_their_back_line_whichever_way_they_face():
    # three treads climbing south towards a back line 1 m south of the lowest nosing
    lines = [[[1008.0, 2006.0 - 0.3 * k, 40.2 + 0.17 * k], [1010.0, 2006.0 - 0.3 * k, 40.2 + 0.17 * k]] for k in range(3)]
    m = build(part("MultiLineString", lines, id="s", part="steps", back_line=[[1008.0, 2005.0], [1010.0, 2005.0]],
                   open_sides=["east"]))
    treads = [mesh for mesh, colour in m.solids["steps"] if colour == "step-grey"]
    sides = [mesh for mesh, colour in m.solids["steps"] if colour == "plaster"]
    assert len(treads) == 3 and len(sides) == 3
    lo, hi = bounds([(t, None) for t in treads])
    assert lo[1] == pytest.approx(2005.0) and hi[1] == pytest.approx(2006.0)
    assert all(s.vertices[:, 0].min() > 1010.0 for s in sides)             # the east end


def test_blocks_walls_tanks_and_chimneys_take_their_heights_and_layers():
    m = build(part("Polygon", box(1005.0, 2015.0, 1008.0, 2018.0), id="deck", part="block", top_below_fl_m=0.1,
                   thickness_m=0.2, colour="deck", layer="deck"),
              part("Polygon", box(1012.0, 2015.0, 1013.0, 2015.6), id="unit", part="block", height_m=0.9),
              part("LineString", [[1018.0, 2006.0, 41.0], [1018.0, 2010.0, 41.2]], id="w", part="wall", thickness_m=0.2,
                   colour="#7f868b"),
              part("Point", [1004.0, 2007.0], id="t", part="tank", radius_m=0.5, height_m=2.0),
              part("Polygon", box(1006.0, 2008.0, 1006.6, 2008.6), id="c", part="chimney", stack_top=45.0))
    lo, hi = bounds(m.solids["deck"])
    assert lo[2] == pytest.approx(40.2) and hi[2] == pytest.approx(40.4)
    (unit, colour), (wall, wall_colour) = m.solids["outdoor"]
    assert unit.vertices[:, 2].max() == pytest.approx(40.9) and colour == "concrete"
    assert wall.vertices[:, 2].max() == pytest.approx(41.2) and wall_colour == "#7f868b"
    assert bounds(m.solids["fixtures"])[1][2] == pytest.approx(42.0)
    assert bounds(m.solids["chimney"])[1][2] == pytest.approx(45.0)


def test_an_unknown_part_is_named():
    with pytest.raises(DomusError, match="garage"):
        build(part("Polygon", box(1005.0, 2015.0, 1008.0, 2018.0), id="g", part="garage"))


def test_a_bay_names_its_faces_after_itself():
    m = build(part("Polygon", box(1008.0, 2015.0, 1010.0, 2015.6), id="lounge-bay", part="bay",
                   bottom_above_fl=-0.2, top_above_fl=2.2, hood_top_above_fl=2.35))
    bay = [f for f in m.faces if f.spec["kind"] == "bay"]
    assert [f.id for f in bay] == ["lounge-bay-0", "lounge-bay-1", "lounge-bay-2"]
    assert {f.spec["group"] for f in bay} == {"lounge-bay"}
