"""The house kept as an IFC file (domus.model.house_ifc): written from the drawn house, read back by IfcOpenShell, and
drawn by the build in its place when [house] source = "ifc", on a made-up site."""

import contextlib
import hashlib
import io
import json
import math
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from domus import api
from domus.errors import DomusError

ifcopenshell = pytest.importorskip("ifcopenshell", reason="the house's IFC needs the ifc extra")

TURN = math.radians(25)


def quiet(line):
    pass


def turned(root):
    """The richer made-up site's house with a bay window and a garden wall, turned 25 degrees about its middle, so
    nothing lies on the grid's axes, under a gable roof."""
    from domus.project import demo

    x0, y0 = demo.X0, demo.Y0
    cx, cy = x0 + 11, y0 + 10.5

    def turn(c):
        if isinstance(c[0], (int, float)):
            x, y = c[0] - cx, c[1] - cy
            return [round(cx + x * math.cos(TURN) - y * math.sin(TURN), 3),
                    round(cy + x * math.sin(TURN) + y * math.cos(TURN), 3), *c[2:]]
        return [turn(v) for v in c]

    path = root / "data" / "site" / "house.geojson"
    fc = json.loads(path.read_text(encoding="utf-8"))
    fc["features"] += [
        {"type": "Feature", "properties": {"id": "bay", "part": "bay", "bottom_above_fl": 0.5, "top_above_fl": 2.1,
                                           "hood_top_above_fl": 2.25, "source": "made up"},
         "geometry": {"type": "Polygon", "coordinates": [[[x0 + 7, y0 + 6], [x0 + 7.3, y0 + 5.4], [x0 + 8.7, y0 + 5.4],
                                                          [x0 + 9, y0 + 6], [x0 + 7, y0 + 6]]]}},
        {"type": "Feature", "properties": {"id": "garden-wall", "part": "wall", "thickness_m": 0.2, "colour": "#7f868b"},
         "geometry": {"type": "LineString", "coordinates": [[x0 + 18, y0 + 6, 43.0], [x0 + 18, y0 + 2, 42.8]]}}]
    for f in fc["features"]:
        f["geometry"]["coordinates"] = turn(f["geometry"]["coordinates"])
        if "back_line" in f["properties"]:
            f["properties"]["back_line"] = turn(f["properties"]["back_line"])
    path.write_text(json.dumps(fc, indent=1) + "\n", encoding="utf-8", newline="\n")
    toml = root / "data" / "site.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace("[house]\n", '[house]\nroof_form = "gable"\nroof_azimuth_deg = '
                                                             '65.0\n', 1), encoding="utf-8", newline="\n")


def model(folder):
    return json.loads((folder / "model.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    """The turned site: built with its house drawn (`drawn`), its house written to IFC and built again with the file
    there but unused (`unused`), then [house] source = "ifc" set through the change route and built from the file."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import made_up_sites

    out = tmp_path_factory.mktemp("house-ifc")
    with contextlib.redirect_stdout(io.StringIO()):
        made_up_sites.write(out / "sites")
    root = out / "sites" / "richer"
    turned(root)
    s = api.open_site(root)
    s.build(say=quiet)
    shutil.copytree(s.output, out / "drawn")
    said = []
    written = s.write_house_ifc(say=said.append)
    s.build(say=quiet)
    shutil.copytree(s.output, out / "unused")
    s.edit({"kind": "setting", "key": "house.source", "value": "ifc"}, why="The house's IFC is its master now.", say=quiet)
    s.build(say=quiet)
    return SimpleNamespace(site=s, root=root, file=written, said=said, drawn=out / "drawn", unused=out / "unused",
                           built=s.output, out=out)


def psets(element):
    import ifcopenshell.util.element as ue

    return ue.get_psets(element).get("Domus", {})


def test_the_house_is_written_as_ifc_and_read_back(made):
    from domus.model import house_ifc, house_texture

    assert made.file == made.root / "data" / "house.ifc"
    assert "   data/house.ifc: the house as house.geojson and [house] draw it, 18 elements on 1 storey" in made.said
    first = made.site.edits()[0]          # an edit on the change route, as every change to the site's data is
    assert first["kind"] == "ifc-write" and first["files"][0]["before"] is None and first["files"][0]["path"] == "data/house.ifc"
    assert first["inverse"] == [{"kind": "ifc-restore", "sha": None}]
    f = ifcopenshell.open(str(made.file))
    assert f.schema == "IFC4" and len(f.by_type("IfcProject")) == len(f.by_type("IfcSite")) == len(f.by_type("IfcBuilding")) == 1
    (conversion,) = f.by_type("IfcMapConversion")
    m = model(made.built)
    assert conversion.TargetCRS.Name == "EPSG:2193" and [conversion.Eastings, conversion.Northings,
                                                         conversion.OrthogonalHeight] == m["origin"]
    settings = made.site.settings()["house"]
    (storey,) = f.by_type("IfcBuildingStorey")
    assert storey.Elevation == pytest.approx(settings["fl_upper"] - m["origin"][2]) and psets(storey)["ref"] == "floor"
    by_ref = {e.Tag: e for e in f.by_type("IfcElement")}
    kinds = {ref: (e.is_a(), e.PredefinedType if e.PredefinedType != "USERDEFINED" else e.ObjectType) for ref, e in by_ref.items()}
    assert kinds == {"north": ("IfcWall", None), "east": ("IfcWall", None), "south": ("IfcWall", None),
                     "west": ("IfcWall", None), "eaves": ("IfcCovering", "EAVES"), "roof": ("IfcRoof", "GABLE_ROOF"),
                     "porch": ("IfcBuildingElementProxy", "PORCH"), "porch-west": ("IfcWall", None),
                     "porch-steps": ("IfcStair", "STRAIGHT_RUN_STAIR"), "deck": ("IfcSlab", None),
                     "deck-balustrade": ("IfcRailing", "BALUSTRADE"), "chimney": ("IfcChimney", None),
                     "tank": ("IfcTank", "STORAGE"), "bay": ("IfcBuildingElementProxy", "BAY"), "bay-0": ("IfcWall", None),
                     "bay-1": ("IfcWall", None), "bay-2": ("IfcWall", None), "garden-wall": ("IfcWall", None)}
    import ifcopenshell.util.element as ue

    assert ue.get_aggregate(by_ref["porch-west"]) == by_ref["porch"] and ue.get_container(by_ref["chimney"]) == storey
    assert psets(by_ref["north"]) | {"id": None} == {"id": None, "ref": "north", "layer": "walls", "part": "face",
                                                     "feature": "house-walls", "face": "north", "source": "made up"}
    assert psets(by_ref["deck"])["layer"] == "deck" and psets(by_ref["garden-wall"])["layer"] == "outdoor"
    # Each piece in its colour, as a style named for it; and the same GlobalId as its Domus id gives.
    chimney = by_ref["chimney"].Representation.Representations[0].Items[0]
    (style,) = chimney.StyledByItem[0].Styles
    colour = style.Styles[0].SurfaceColour
    assert style.Name == "chimney" and [round(c * 255) for c in (colour.Red, colour.Green, colour.Blue)] == list(
        house_texture.rgb("chimney"))
    import uuid

    assert by_ref["tank"].GlobalId == ifcopenshell.guid.compress(uuid.uuid5(house_ifc.NAMESPACE, "element:tank").hex)
    # Read back: the elements in the file's order, each in its layer, on the site's grid.
    found = {e.ref: e for e in house_ifc.read(made.site._project)}
    assert set(found) == set(by_ref) and found["deck"].layer == "deck" and found["bay-1"].face == "bay-1"
    lowest = np.vstack([p[1] for p in found["roof"].pieces])[:, 2].min()
    roof = np.reshape(next(x for x in m["layers"] if x["id"] == "house-roof")["positions"], (-1, 3))
    assert lowest == pytest.approx(roof[:, 1].min() + m["origin"][2], abs=0.001)       # the viewer's y is up


def triangles(layer, key, width):
    return np.reshape(layer[key], (-1, width))[np.reshape(layer["indices"], (-1, 3))]


def test_the_house_built_from_the_ifc_is_the_drawn_house(made):
    drawn = {x["id"]: x for x in model(made.drawn)["layers"] if x["id"].startswith("house-")}
    built = {x["id"]: x for x in model(made.built)["layers"] if x["id"].startswith("house-")}
    assert list(built) == list(drawn) == ["house-walls", "house-roof", "house-eaves", "house-porch", "house-bay",
                                          "house-steps", "house-deck", "house-outdoor", "house-chimney", "house-fixtures"]
    for k, a in drawn.items():
        b = built[k]
        assert {x: b[x] for x in ("name", "colour", "info", "texture")} == {x: a[x] for x in ("name", "colour", "info", "texture")}
        pa, pb = triangles(a, "positions", 3), triangles(b, "positions", 3)
        assert pa.shape == pb.shape, k
        # Each triangle where the drawn house has it, within 1 mm, its corners in the same turn: IfcOpenShell may
        # start a triangle at another of its corners.
        off = np.stack([np.abs(np.roll(pb, r, axis=1) - pa).max(axis=(1, 2)) for r in range(3)])
        turn = off.argmin(axis=0)
        assert off.min(axis=0).max() <= 0.001, k
        ua = triangles(a, "uv", 2)              # the pictures, faces', colours' and the roof's tiles alike
        ub = np.stack([np.roll(t, s, axis=0) for t, s in zip(triangles(b, "uv", 2), turn, strict=True)])
        assert np.abs(ua - ub).max() <= 1e-4, k
    for name in ("house-atlas.jpg", "house-roof.jpg"):
        assert (made.drawn / name).read_bytes() == (made.built / name).read_bytes()
    a, b = model(made.drawn), model(made.built)
    assert [x for x in a["layers"] if not x["id"].startswith("house-")] == [x for x in b["layers"] if not x["id"].startswith("house-")]
    assert b["summary"]["house"] == {**a["summary"]["house"], "source": "data/house.ifc"}


@pytest.mark.parametrize(("form", "azimuth"), [("hip", None), ("skillion", 70.0), ("gable", 160.0)])
def test_the_roof_lays_its_tiles_as_the_drawn_roof_does(form, azimuth):
    """tile_uv in the roof's own coordinates, the house's frame as house_ifc writes it, gives the drawn roof's tile
    coordinates, whichever its form: a turned house's, 25 degrees off the grid."""
    from domus import build
    from domus.model import house

    a = math.radians(25)
    corners = [(0.0, 0.0), (12.0, 0.0), (12.0, 9.0), (0.0, 9.0)]
    ring = [[1000 + x * math.cos(a) - y * math.sin(a), 2000 + x * math.sin(a) + y * math.cos(a)] for x, y in corners]
    walls = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[*ring, ring[0]]]},
             "properties": {"id": "house-walls", "soffit": 42.8}}
    ground = lambda e, n: np.full(np.shape(e), 40.0)
    hm = house.build([walls], 40.5, 25.0, 43.0, 0.4, ground, form=form, azimuth_deg=azimuth)
    shaped = None if form == "hip" else house.shaped_roof(hm.frame, hm.frame.local(np.asarray(ring)), 43.0,
                                                          math.radians(25.0), form, azimuth, 0.0)
    uv = build.tile_uv(hm.frame.local(hm.roof.vertices), hm.roof.faces, shaped)
    assert np.allclose(uv, hm.roof.uv, atol=1e-6)


def test_a_site_without_the_setting_is_unchanged_by_the_file(made):
    a, b = model(made.drawn), model(made.unused)
    assert {k: v for k, v in a.items() if k != "built"} == {k: v for k, v in b.items() if k != "built"}
    assert sorted(p.name for p in made.drawn.iterdir()) == sorted(p.name for p in made.unused.iterdir())


def test_writing_it_again_gives_the_same_globalids_as_an_edit_undo_puts_back(made, tmp_path):
    root = tmp_path / "site"
    shutil.copytree(made.root, root, ignore=shutil.ignore_patterns("viewer", ".domus"))
    s = api.open_site(root)
    ifc = root / "data" / "house.ifc"
    raw = ifc.read_bytes()
    before = {e.GlobalId: e.is_a() for e in ifcopenshell.open(str(ifc)).by_type("IfcRoot")}
    with pytest.raises(DomusError, match="--force"):
        s.write_house_ifc(say=quiet)
    s.write_house_ifc(force=True, say=quiet, why="Written again.", who="sam")
    after = {e.GlobalId: e.is_a() for e in ifcopenshell.open(str(ifc)).by_type("IfcRoot")}
    assert before == after and len(before) > 40
    (record,) = s.edits()
    (changed,) = record["files"]
    assert record["kind"] == "ifc-write" and (record["why"], record["who"]) == ("Written again.", "sam")
    assert changed["before"] == hashlib.sha256(raw).hexdigest() and changed["after"] == hashlib.sha256(ifc.read_bytes()).hexdigest()
    assert (root / changed["copy"]).read_bytes() == raw
    s.undo(record["id"], say=quiet)
    assert ifc.read_bytes() == raw


def test_undoing_the_first_write_takes_the_file_away(made, tmp_path):
    root = tmp_path / "site"
    shutil.copytree(made.root, root, ignore=shutil.ignore_patterns("viewer", ".domus"))
    ifc = root / "data" / "house.ifc"
    ifc.unlink()
    s = api.open_site(root)
    s.write_house_ifc(say=quiet)
    written = ifc.read_bytes()
    (record,) = s.edits()
    undo = s.undo(record["id"], say=quiet)
    assert not ifc.exists() and undo["files"][0]["after"] is None
    s.undo(undo["id"], say=quiet)                       # and undoing that writes it again, byte for byte
    assert ifc.read_bytes() == written


def test_the_build_cache_keys_on_the_ifc_file(made):
    from domus.build import cache as buildcache
    from domus.model import house_ifc

    p = made.root / "data" / "house.ifc"
    assert house_ifc.digest(made.site._project) == {"data/house.ifc": hashlib.sha256(p.read_bytes()).hexdigest()}
    key = buildcache.Cache(made.root, made.built, also=house_ifc.digest(made.site._project)).site
    raw, stat, files = p.read_bytes(), p.stat(), buildcache.site_files(made.root)
    try:            # another file of the same size and time: only its SHA-256 tells it apart
        p.write_bytes(raw.replace(b"'Wall north'", b"'Wall nortH'"))
        os.utime(p, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert buildcache.site_files(made.root) == files
        assert buildcache.Cache(made.root, made.built, also=house_ifc.digest(made.site._project)).site != key
    finally:
        p.write_bytes(raw)
        os.utime(p, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_a_missing_or_unreadable_file_stops_the_build_saying_how_to_make_it(made, tmp_path):
    from domus.model import house_ifc

    root = tmp_path / "site"
    shutil.copytree(made.root, root, ignore=shutil.ignore_patterns("viewer", ".domus"))
    s = api.open_site(root)
    (root / "data" / "house.ifc").unlink()
    with pytest.raises(DomusError, match=r"there's no data/house.ifc: domus house ifc writes it"):
        house_ifc.read(s._project)
    (root / "data" / "house.ifc").write_text("not IFC\n", encoding="utf-8")
    with pytest.raises(DomusError, match=r"data/house.ifc can't be read as IFC .*domus house ifc --force"):
        s.build(say=quiet)
    toml = root / "data" / "site.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace('source = "ifc"', 'source = "drawing"'), encoding="utf-8")
    s._project.forget_settings()
    with pytest.raises(DomusError, match=r"source .* 'drawing': give drawn"):
        house_ifc.source(s._project)


def test_the_export_copies_the_ifc_houses_elements_as_they_are(made, tmp_path):
    import ifcopenshell.geom
    import ifcopenshell.util.element as ue

    house = ifcopenshell.open(str(made.file))
    f = ifcopenshell.open(str(made.site.export(tmp_path / "site.ifc")))
    ids = {e.GlobalId for e in house.by_type("IfcElement")}
    assert ids <= {e.GlobalId for e in f.by_type("IfcElement")}
    assert not [e for e in f.by_type("IfcElement") if (e.Name or "").startswith("House:")]
    assert ue.get_aggregate(f.by_guid(house.by_type("IfcBuilding")[0].GlobalId)).is_a("IfcSite")
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    for gid in ids:
        a = np.asarray(ifcopenshell.geom.create_shape(settings, house.by_guid(gid)).geometry.verts)
        b = np.asarray(ifcopenshell.geom.create_shape(settings, f.by_guid(gid)).geometry.verts)
        assert np.abs(a - b).max() < 1e-6
        assert psets(f.by_guid(gid)) | {"id": 0} == psets(house.by_guid(gid)) | {"id": 0}


def test_the_command_writes_through_the_api(made, tmp_path, domus, capsys):
    root = tmp_path / "site"
    shutil.copytree(made.root, root, ignore=shutil.ignore_patterns("viewer", ".domus"))
    (root / "data" / "house.ifc").unlink()
    domus("--site", root, "house", "ifc")
    out = capsys.readouterr().out
    assert "data/house.ifc: written" in out and "Recorded as edit 0001" in out and (root / "data" / "house.ifc").exists()
    with pytest.raises(SystemExit, match="is there already: domus house ifc --force"):
        domus("--site", root, "house", "ifc")
    domus("--site", root, "house", "ifc", "--force", "--why", "Again.", "--who", "sam")
    out = capsys.readouterr().out
    assert "the file as it was is in .domus/edits/0002-ifc-write/data/house.ifc" in out
    assert api.open_site(root).edits()[-1]["why"] == "Again."


def test_without_ifcopenshell_it_says_how_to_get_it_and_a_drawn_house_still_builds(made, project, monkeypatch, tmp_path):
    from domus import build
    from domus.model import terrain

    monkeypatch.setitem(sys.modules, "ifcopenshell", None)
    with pytest.raises(DomusError, match=r"uv sync --extra ifc"):
        made.site.write_house_ifc(force=True, say=quiet)
    cfg = project.settings()
    dem = terrain.ground(project)
    b = SimpleNamespace(site=project, cfg=cfg, frame=build.Frame(build.site_origin(cfg, dem)), layers=[], summary={},
                        ground=lambda e, n: dem.sample(np.atleast_1d(e), np.atleast_1d(n)))
    monkeypatch.setattr(project, "output", tmp_path)
    build.add_house(b)
    assert [x["id"] for x in b.layers] == ["house-walls", "house-roof", "house-eaves"]
