"""domus.api and domus.testing, the public API, on the made-up demo site."""

import json
import os
import shutil
import subprocess
import sys
import types

import numpy as np
import pytest

from domus import api
from domus.errors import DomusError

# The promise: these stay, from one release to the next. Removing or renaming one needs a deprecation first.
PUBLIC = {"DomusError", "Site", "clip", "create", "demo", "fit_affine", "linz_query", "new", "open_site", "resample_line"}
SITE_METHODS = {"accept", "build", "check", "edit", "edits", "evaluate", "export", "fetch", "fetch_marks", "fetch_points",
                "fetch_title", "frame", "grid", "ground", "house_walls", "layer", "lot", "map_canopy", "options", "output", "path",
                "proposals", "propose", "regrade", "reject", "scan", "settings", "shift", "shots", "title", "trust", "undo",
                "upgrade", "video", "prep"}
# Provisional, as the video and prep steps are (docs/api.md says why): the observations, and the house kept as an IFC file.
PROVISIONAL = {"house", "observations", "propose_observation", "write_house_ifc"}


def test_the_promised_names_are_all_there():
    assert set(api.__all__) == PUBLIC
    assert all(hasattr(api, name) for name in PUBLIC)
    assert SITE_METHODS | PROVISIONAL <= set(dir(api.Site)) | {"path"}


def test_a_deprecation_is_put_at_the_line_outside_domus_that_led_to_it():
    """How the promise deprecates something: domus.errors.deprecated() warns with a DomusDeprecationWarning, a
    DeprecationWarning, put at the first line outside Domus that led there, past Domus's own code on the way."""
    from domus.errors import DomusDeprecationWarning, deprecated

    def old():
        deprecated("old() is going: use new()")

    # The same function as a part of Domus would have it: its code in Domus's own folder.
    in_domus = old.__code__.replace(co_filename=os.path.join(os.path.dirname(api.__file__), "made_up.py"))
    part = types.FunctionType(in_domus, globals(), "old", None, old.__closure__)
    here = sys._getframe().f_lineno
    with pytest.warns(DomusDeprecationWarning, match=r"old\(\) is going: use new\(\)") as got:
        part()
    assert issubclass(DomusDeprecationWarning, DeprecationWarning)
    assert [(w.filename, w.lineno) for w in got] == [(__file__, here + 2)]


def test_a_site_reads_its_lot_house_layers_and_options(site):
    s = api.open_site()
    assert s.path == site.resolve() and api.open_site(site) is s
    assert s.title == "Demo site (made up)"
    assert s.lot().area == pytest.approx(660.0) and s.house_walls().area == pytest.approx(108.0)
    assert len(s.layer("trees")) == 4 and s.layer("no-such-layer") == []
    assert [o["id"] for o in s.options()] == ["a-one-wall", "b-two-tiers"]
    with pytest.raises(DomusError, match="layer's name"):
        s.layer("../site")


def test_its_settings_are_a_copy(site):
    s = api.open_site()
    s.settings()["site"]["title"] = "changed"
    assert s.title == "Demo site (made up)"


def test_its_grid_turns_points_and_heights(site):
    grid = api.open_site().grid
    assert grid.epsg == 2193 and grid.vertical == "NZVD2016" and grid.datums == ()
    e, n = grid.origin[:2]
    x, y = grid.to_nztm(e, n)
    assert grid.from_nztm(x, y) == pytest.approx((e, n))
    lon, lat = grid.to_lonlat(e, n)
    assert 165 < lon < 180 and -48 < lat < -34                  # off New Zealand's coast, where the demo is made up


def test_the_ground_comes_back_the_shape_it_was_asked(site):
    ground = api.open_site().ground()
    e, n = np.asarray(api.open_site().lot().exterior.coords).T
    assert np.isfinite(ground(e, n)).all() and ground(e, n).shape == e.shape
    grid = ground(e[:2, None] + np.zeros(3), n[:2, None] + np.zeros(3))
    assert grid.shape == (2, 3) and ground(e[0], n[0]).shape == ()


def test_the_ground_is_the_ground_the_build_draws(site, project):
    """site.ground() is the ground as the build draws it, the existing walls' faces and tops in it: just behind the
    demo's old wall, the wall's top, which stands above the LiDAR there."""
    from domus.geo import mesh
    from domus.model import terrain, walls
    from domus.options import design

    ground = api.open_site().ground()
    old = design.existing_walls(project)[0]
    pts, _, _, _, top = walls.wall_levels(old, terrain.ground(project))
    k = len(pts) // 2
    e, n = mesh.offset_behind(pts, old["thickness"] + 0.01, old["side"])[k]
    assert float(ground(e, n)) == float(terrain.existing(project).sample(np.array([e]), np.array([n]))[0])
    assert float(ground(e, n)) == pytest.approx(top[k], abs=0.03)
    assert float(ground(e, n)) > float(terrain.ground(project).sample(np.array([e]), np.array([n]))[0]) + 0.3


def test_an_option_is_evaluated_by_itself_or_its_id(site):
    s = api.open_site()
    a = s.evaluate("a-one-wall")
    assert a["volumes"]["fill_m3"] >= 0 and a["walls"]
    assert s.evaluate(s.options()[0])["volumes"] == a["volumes"]
    with pytest.raises(DomusError, match="no option"):
        s.evaluate("z-nothing")


def test_the_frame_goes_both_ways(site):
    fr = api.open_site().frame()
    so = np.array([[3.0, 2.0], [11.0, 7.5]])
    assert np.allclose(fr.so(fr.en(so)), so)


def test_the_checks_report_what_they_found(site):
    report = api.open_site().check(["sanity"])
    assert set(report["failures"]) == {"sanity"} and isinstance(report["findings"], list)
    assert json.loads((site / ".domus" / "checks" / "check_model.json").read_text(encoding="utf-8")) == report


def test_the_demos_roof_check_passes(site):
    """The demo's surface model has the roof Domus draws for its house, so the roof check, as `domus prep check roof`
    runs it, finds the house where it is."""
    report = api.open_site().check(["roof"])
    assert report["failures"] == {"roof": 0}
    assert any("best plan shift of the house E +0.00 N +0.00 m" in f["detail"] for f in report["findings"])


def test_a_folder_that_isnt_a_site_is_refused(site, tmp_path):
    with pytest.raises(DomusError, match="isn't a site project"):
        api.open_site(tmp_path)
    assert api.open_site(site) is api.open_site(site)


def _plain(model):
    return {k: v for k, v in model.items() if k not in ("built", "commit")}


def test_two_sites_in_one_process_are_read_apart(site, tmp_path):
    # A copy of the demo whose walls stand 0.2 m higher over the ground they retain: each site's option comes to its
    # own walls, whichever was read first, and the copy built in this process is the copy built in a process of its own.
    other = tmp_path / "other"
    shutil.copytree(site, other, ignore=shutil.ignore_patterns("viewer", ".domus"))
    shared = other / "options" / "_site.toml"
    shared.write_text(shared.read_text(encoding="utf-8").replace("upstand_m = 0.1", "upstand_m = 0.3"), encoding="utf-8")
    a, b = api.open_site(site), api.open_site(other)
    option = a.options()[0]["id"]
    top_a = a.evaluate(option)["walls"][0]["top_max"]
    top_b = b.evaluate(option)["walls"][0]["top_max"]
    assert top_b == pytest.approx(top_a + 0.2, abs=0.02)
    assert a.evaluate(option)["walls"][0]["top_max"] == top_a
    here = json.loads((b.build() / "model.json").read_text(encoding="utf-8"))
    r = run("from domus import build; from domus.project.site import started; build.main(started(), fresh=True)",
            DOMUS_SITE=str(other), DOMUS_OUTPUT=str(tmp_path / "apart"))
    assert r.returncode == 0, r.stderr
    apart = json.loads((tmp_path / "apart" / "model.json").read_text(encoding="utf-8"))
    assert _plain(here) == _plain(apart)


def run(code, **env):
    keep = {k: v for k, v in os.environ.items() if k not in ("DOMUS_SITE", "DOMUS_OUTPUT")}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                          env={**keep, **env, "PYTHONUTF8": "1"}, check=False, timeout=300)


def test_a_site_opened_by_path_is_built_in_its_own_folder(site, tmp_path):
    r = run(f"from domus import api; s = api.open_site({str(site)!r}); print(s.path); print(s.output)",
            DOMUS_SITE=str(tmp_path), DOMUS_OUTPUT=str(tmp_path / "elsewhere"))
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == [str(site.resolve()), str((site / "viewer" / "data").resolve())]


def test_a_site_opens_whatever_domus_imported_first(site, tmp_path):
    r = run("from domus.geo import crs; from domus import api; from domus.options import design; "
            f"print(api.open_site({str(site)!r}).title)", DOMUS_SITE=str(tmp_path))
    assert r.returncode == 0 and r.stdout.strip() == "Demo site (made up)", r.stderr


def test_a_sites_own_tests_get_it_and_build_it_out_of_the_way(site, tmp_path):
    root = tmp_path / "place"
    shutil.copytree(site, root, ignore=shutil.ignore_patterns("viewer", "docs", ".domus"))
    (root / "tests").mkdir()
    (root / "tests" / "conftest.py").write_text(
        "from domus.testing import built, site, use_site  # noqa: F401\n\nuse_site(__file__)\n", encoding="utf-8")
    (root / "tests" / "test_it.py").write_text(
        "def test_it(site, built):\n"
        "    assert site.title == 'Demo site (made up)'\n"
        "    assert (built / 'model.json').exists() and built != site.path / 'viewer' / 'data'\n", encoding="utf-8")
    keep = {k: v for k, v in os.environ.items() if k not in ("DOMUS_SITE", "DOMUS_OUTPUT")}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(root / "tests")], cwd=root,
                       capture_output=True, text=True, encoding="utf-8", env={**keep, "PYTHONUTF8": "1"}, check=False, timeout=600)
    assert r.returncode == 0 and "1 passed" in r.stdout, r.stdout + r.stderr
    assert not (root / "viewer").exists()           # the site's own model, which a server would show, is left alone


def test_the_commands_are_the_apis_own(tmp_path):
    said = []
    demo = api.demo(tmp_path / "demo", say=said.append)
    assert demo.path == (tmp_path / "demo").resolve() and demo.title == "Demo site (made up)"
    assert said[-1].startswith(f"Wrote the demo site to {demo.path}")
    demo.upgrade(dry_run=True, say=said.append)
    assert "there's nothing to upgrade" in said[-1]                          # it's at this Domus's format already
    assert demo.trust(say=said.append) == [] and "declares no extensions" in said[-1]
    built = []
    assert demo.build(fresh=True, say=built.append) == demo.output and built[-1].startswith("wrote ")
    assert (demo.output / "model.json").exists()
    place = api.new(tmp_path / "new", "A place", centre=(-43.53, 172.63), say=said.append)
    assert place.grid.epsg == 2193 and place.title == "A place" and said[-1].startswith("Next:")
    with pytest.raises(DomusError, match="one of boundary"):
        api.new(tmp_path / "neither", "Nowhere")
    with pytest.raises(DomusError, match="tag takes letters"):
        demo.scan(tmp_path / "walk.ply", "a walk", epsg=2193)
    with pytest.raises(DomusError, match="one of control"):
        demo.scan(tmp_path / "walk.ply", "walk")
    with pytest.raises(DomusError, match="timeout"):
        demo.shots(tmp_path / "shots.json", timeout=0)
    with pytest.raises(DomusError, match="There's no"):
        demo.shift(tmp_path / "nothing.json", tmp_path / "backup")


def test_the_video_steps_are_the_sites_and_say_what_they_print(site, tmp_path):
    from PIL import Image

    frames = tmp_path / "frames"
    frames.mkdir()
    for t in (0.0, 1.0, 2.0):
        Image.new("RGB", (64, 48), (90, 120, 150)).save(frames / f"t{t:07.3f}.jpg")
    said = []
    video = api.open_site(site).video
    written = video.sheet(frames, str(tmp_path / "sheet"), 0, 2, 1, cols=2, width=32, rows=1, say=said.append)
    assert [w[0] for w in written] == [str(tmp_path / "sheet-1.jpg"), str(tmp_path / "sheet-2.jpg")]
    assert said == [f"{out} {w} x {h} {first} .. {last}" for out, w, h, first, last in written]
    size, box = video.crop(frames / "t000.000.jpg", tmp_path / "piece.jpg", 64, (8, 8, 24, 16), say=said.append)
    assert size == (16, 8) and said[-1] == f"{tmp_path / 'piece.jpg'} (16, 8) full-res box {box}"


def test_the_prep_steps_are_the_sites_and_say_what_they_print(site, tmp_path):
    said = []
    rows = api.open_site(site).prep.profile([[0.0, 2.0], [4.0, 2.0]], offsets=[0.0, 1.0], step=2.0, csv=tmp_path / "p.csv",
                                            say=said.append)
    assert len(rows) == 3 and said[0].split() == ["along", "s", "o", "+0", "+1"] and len(said) == 4
    assert (tmp_path / "p.csv").read_text(encoding="utf-8").splitlines()[0] == "along,s,o,+0,+1"


def test_the_helpers(monkeypatch):
    pts, along = api.resample_line([[0, 0, 5], [10, 0, 6]], 2.5)
    assert len(pts) == 5 and along[-1] == pytest.approx(10) and pts[:, 2][-1] == pytest.approx(6)
    a, resid = api.fit_affine([[0, 0], [10, 0], [0, 10]], [[100, 200], [110, 200], [100, 190]])
    assert np.allclose(a, [[1, 0, 100], [0, -1, 200]]) and np.allclose(resid, 0)
    from domus.importers.nz import publicdata

    asked = []
    monkeypatch.setattr(publicdata, "_wfs", lambda key, layer, cql, count=20: asked.append((key, layer, cql, count)) or [])
    api.linz_query(50820, bbox=(1, 2, 3, 4), key="k")
    api.linz_query("layer-50774", cql="name = 'X'", count=5, key="k")
    assert asked == [("k", "layer-50820", "(BBOX(shape,1,2,3,4,'EPSG:2193'))", 20), ("k", "layer-50774", "(name = 'X')", 5)]
    with pytest.raises(DomusError, match="bbox"):
        api.linz_query(50820, key="k")
    monkeypatch.delenv("DOMUS_LINZ_KEY", raising=False)
    with pytest.raises(DomusError, match="DOMUS_LINZ_KEY"):
        api.linz_query(50820, bbox=(1, 2, 3, 4))
