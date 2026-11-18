# Exporting a model

`domus export` writes a site's built model for other software: glTF for Blender and most 3D viewers, phones among
them, and IFC4 for architects' and builders' software.

```sh
domus build
domus export site.glb                            # the site as it is
domus export option-a.glb --option a-one-wall    # with a wall option in place of what it replaces
domus export option-a.ifc --option a-one-wall
```

- **The format** comes from the file's suffix, `.glb` or `.ifc`, or from `--format glb` or `--format ifc`.
- **`--option ID`** shows that wall option, as the viewer's options panel does: its designed ground, walls, steps,
  lawns, beds and contours come in, and out go the existing walls it doesn't keep, the beds, their plants and the
  stumps, the site features it takes out, the lawns it draws again on its own ground, and the ground under its
  designed ground. The id is the option's file name in `options/`, without `.toml`. Without it, you get the site as
  it is.
- **It exports the model as last built,** from the site's `viewer/data/` (or `DOMUS_OUTPUT`), so build the site after
  changing it. Nothing leaves your machine.

A site's tools and agents do the same with `site.export(path, format=None, option=None)` in the public API
([api.md](api.md)), which gives the file's path.

An export is the model as Domus draws it: a planning aid, not a survey or an engineering design. Its ground is the
LiDAR's, and an option's walls and levels are what Domus works out from it.

## What both hold

What the viewer shows when it opens, or with the option shown. Layers it hides until they're switched on are left
out: the surface model, the walk route, easements, a designer's IFC model, the point clouds and the like. So are
labels, as neither format holds text.

## glTF (.glb)

One binary file holding a single scene, its pictures inside it.

- **Nodes:** one for the site, one for each group in the viewer's layers panel (Ground, House, Existing walls,
  Vegetation, an option's group and so on), and one for each layer in it, named as in the panel. A layer of plants
  has a node for each plant.
- **What's drawn, as the viewer draws it:** the ground under the aerial photo (the viewer's first drape), the house
  in its pictures, walls in theirs, fences and other features, trees and shrubs as leaf cards with their trunks and
  limbs, stumps, lawns, an option's lawns and beds, and drains at their depths below the ground. Contours, the
  boundary and drains on the ground are lines.
- **Colours and pictures:** each colour or picture is a material. The leaf cards are cut out where their picture is
  clear, and both sides of them show.
- **Coordinates:** metres, y up, about the model's origin: x east and z south of it.
- **Placing it again:** `asset.extras` holds the site's grid (`crs`: its EPSG code, name and height datum), the
  model's `origin` on it, `[E0, N0, H0]`, and which `option` it shows. A point `(x, y, z)` is at
  `E = E0 + x`, `N = N0 - z`, height `H0 + y`. The site's node carries the same, and each layer's node its layer's
  `id` and what the viewer shows when it's picked: a wall's levels and heights, a plant's source. Blender shows these
  as custom properties.
- **Credit:** the site's credit line, such as LINZ's for the aerial photo, is the file's `asset.copyright`.
- **Lawns and beds are raised 20 mm** off the ground they're drawn on. The viewer draws them in front of the ground,
  which glTF has no way to say; at the same height the two would flicker.
- **Left out:** the grass's close-up detail, which the viewer adds as you come near.

Open it in Blender with File > Import > glTF 2.0. Blender turns y up into z up, so x stays east and y becomes north,
with the origin at the model's origin. Most 3D viewers open a `.glb` as it is.

## IFC (.ifc)

IFC4, written with IfcOpenShell, Domus's `ifc` extra: `uv sync --extra ifc` in a copy of Domus, or
`pip install 'domus[ifc]'`. The container image has it. Without it, `domus export` says so and writes nothing.

- **The project:** an IfcProject in metres, with one IfcSite. The house is an IfcBuilding on it, with one
  IfcBuildingStorey at its floor level holding its parts. Everything else stands on the site.
- **A house kept as an IFC file:** a site whose house is drawn from `data/house.ifc` (`[house] source = "ifc"`,
  [site-format.md](site-format.md#the-house-as-an-ifc-file)) has that file's building, storeys and elements copied
  in as they are, in place of the house's layers: each element with its GlobalId, its own geometry and placement, its
  styles and its property sets, where it stands on the grid.
- **Georeferenced:** the site's IfcMapConversion takes the model's coordinates to an IfcProjectedCRS named
  `EPSG:<code>` (the site's grid), with its name, height datum and metres as its unit. Its Eastings, Northings and
  OrthogonalHeight are the model's origin, with no rotation and a scale of 1. The model's x is east, y north and z up
  of that origin. The site's reference latitude, longitude and elevation are the origin's too.
- **Geometry:** each element is an IfcTriangulatedFaceSet, in the colour of its swatch in the viewer's layers panel.
  They're shapes to see, measure and set out against, not solids a designer can edit as walls.
- **Ids:** each element's GlobalId stays the same from one export of a site to the next, so software that compares
  versions sees the same elements. An element the model gives an id, such as a wall's `A1`, has it as its Tag.
- **Property set `Domus`:** every element has one, with `layer`, the model's layer it comes from, and what the
  model says about it, under the model's own names (below). Lengths and levels are metres, areas square metres and
  volumes cubic metres. Levels are heights on the site's datum, which `levels_datum` names. The model's id for a
  thing is `ref`, because IfcOpenShell's own reading of a property set puts the set's number under `id`.
- **Materials:** a wall's or a flight's material, such as `block` or `timber`, is its IfcMaterial too.
- **Left out:** plants (trees, shrubs and the plants in beds), lines (contours, the boundary) and pictures.

| What | IFC | In its property set |
|---|---|---|
| The ground | IfcGeographicElement, TERRAIN | `layer` |
| An option's designed ground | IfcGeographicElement, TERRAIN | The option: `ref`, `name`, `short`, `description`, `cut_m3`, `fill_m3`, `net_m3`, `regraded_area_m2`, `cut_max_m`, `fill_max_m`, `tolerance_m3`, `flat_before_m2`, `flat_after_m2`, `flat_gain_m2`, `removed_existing_m`, `keep_existing`, `steps` and `checks` |
| An option's walls | IfcWall, user-defined RETAININGWALL | `ref`, `name`, `material`, `length_m`, `height_min_m` and `height_max_m` (exposed), `retained_max_m`, `face_area_m2`, `top_min`, `top_max`, `toe_min`, `toe_max`, `checks` |
| An option's steps | IfcStair, STRAIGHT_RUN_STAIR | `ref`, `name`, `material`, `risers`, `riser_m`, `going_m`, `rise_m`, `width_m`, `length_m`, `foot` and `head` (levels), `checks` |
| An option's terraces, lawns and beds | IfcGeographicElement, user-defined as their kind, such as LAWN or PLANTING | `ref`, `name`, `kind`, `area_m2`, `slope` |
| Existing walls | IfcWall | `ref`, `name`, `material`, `confidence`, `length_m`, `height_min_m`, `height_max_m`, `top_min`, `top_max`, `toe_min`, `toe_max`, `face_area_m2`, `note` |
| Lawns, and the soil under beds | IfcGeographicElement, user-defined LAWN or PLANTING | `plant`, and its `note`, `source` and `basis` |
| The house's walls, roof, chimney and steps | IfcWall, IfcRoof, IfcChimney, IfcStair | `source` |
| The house's other parts: eaves, porches, decks, tanks | IfcBuildingElementProxy | `source` |
| Fences, sheds and other features | IfcBuildingElementProxy | `features`, `note`, `source` |
| Drains | IfcPipeSegment | `note`, `confidence`, `source`, `depth_m` |

Open it in an IFC viewer, or import it into design software that reads IFC4.
