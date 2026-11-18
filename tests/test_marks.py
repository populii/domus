"""`domus marks`, with LINZ's answers stubbed, and --site's hold over where a build goes."""

import json
import os
import subprocess
import sys

import pytest

from domus.errors import DomusError
from domus.geo.crs import Grid
from domus.importers.nz import marks, publicdata

LOT = [[1000.0, 2000.0], [1020.0, 2000.0], [1020.0, 2020.0], [1000.0, 2020.0], [1000.0, 2000.0]]
NZTM = Grid(2193)       # the sites here are on NZTM, so LINZ's coordinates are their own


def mark(mark_id, name, x, y):
    return {"geometry": {"type": "Point", "coordinates": [x, y]}, "properties": {"id": mark_id, "name": name, "nominal_accuracy": 0.2}}


def height(name, x, y):
    return {"geometry": {"type": "Point", "coordinates": [x, y]},
            "properties": {"current_mark_name": name, "normal_orthometric_height": 35.3, "coordinate_system": "NZVD2016", "mark_condition": "Reliable", "order": "2"}}


@pytest.fixture
def nztm_site():
    return marks.Polygon(LOT)


def test_only_named_marks_on_the_line_are_kept(nztm_site):
    rows = [mark(1, "PEG 1 DP 9", 1000.5, 2010.0), mark(2, "UNMK 2 DP 9", 1000.0, 2005.0), mark(3, None, 1000.0, 2006.0), mark(4, "PEG 4 DP 9", 1040.0, 2010.0)]
    feats = marks.boundary_features(rows, nztm_site, NZTM)
    assert [f["properties"]["name"] for f in feats] == ["PEG 1 DP 9"]
    assert feats[0]["properties"]["kind"] == "mark"
    assert feats[0]["properties"]["confidence"] == "LINZ, +-0.2 m"


def test_height_marks_come_nearest_first_and_join_the_layer_only_by_name(nztm_site, monkeypatch, tmp_path, capsys, domus):
    (tmp_path / "data" / "site").mkdir(parents=True)
    (tmp_path / "data" / "site" / "boundary.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [LOT]}}]}), encoding="utf-8")
    (tmp_path / "data" / "site.toml").write_text("[domus]\nformat = 3\n\n[crs]\nepsg = 2193\n", encoding="utf-8")
    monkeypatch.setenv("DOMUS_LINZ_KEY", "k")
    asked = []

    def fake(key, layer, cql, count=20):
        asked.append(layer)
        return [mark(1, "PEG 1 DP 9", 1000.0, 2010.0)] if layer == marks.BOUNDARY_MARKS else [height("FAR", 1900.0, 2000.0), height("NEAR", 1100.0, 2000.0)]

    monkeypatch.setattr(publicdata, "_wfs", fake)
    out_file = tmp_path / "data" / "site" / "features-survey-marks.geojson"
    domus("--site", tmp_path, "marks")
    printed = capsys.readouterr().out
    assert asked == ["layer-50774", "layer-50784"]
    assert printed.index("NEAR") < printed.index("FAR")
    assert len(json.loads(out_file.read_text(encoding="utf-8"))["features"]) == 1
    domus("--site", tmp_path, "marks", "--height-mark", "NEAR")
    last = json.loads(out_file.read_text(encoding="utf-8"))["features"][-1]["properties"]
    assert last["id"] == "linz-height-mark" and last["name"] == "NEAR (35.3 m)"
    with pytest.raises(SystemExit, match="No height mark"):
        domus("--site", tmp_path, "marks", "--height-mark", "NOPE")


def test_marks_and_title_search_round_the_main_lot_not_the_first(nztm_site, monkeypatch, tmp_path, at_site):
    (tmp_path / "data" / "site").mkdir(parents=True)
    way = [[1020.0, 2000.0], [1023.0, 2000.0], [1023.0, 2020.0], [1020.0, 2020.0], [1020.0, 2000.0]]
    feats = [{"type": "Feature", "properties": {"id": "right-of-way"}, "geometry": {"type": "Polygon", "coordinates": [way]}},
             {"type": "Feature", "properties": {"id": "lot"}, "geometry": {"type": "Polygon", "coordinates": [LOT]}}]
    (tmp_path / "data" / "site" / "boundary.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}),
                                                                 encoding="utf-8")
    toml = tmp_path / "data" / "site.toml"
    toml.write_text('[domus]\nformat = 3\n\n[crs]\nepsg = 2193\n\n[boundary]\nmain = "lot"\n', encoding="utf-8")
    s = at_site(tmp_path)
    assert marks.lot_on_nztm(s).equals(marks.Polygon(LOT))
    toml.write_text(toml.read_text(encoding="utf-8").replace('"lot"', '"gone"'), encoding="utf-8")
    s.forget_settings()
    with pytest.raises(DomusError, match="'gone'"):
        marks.lot_on_nztm(s)


def test_it_asks_for_the_key(monkeypatch, project, domus):
    monkeypatch.delenv("DOMUS_LINZ_KEY", raising=False)
    with pytest.raises(SystemExit, match="DOMUS_LINZ_KEY"):
        domus("marks")


def run_python(code, **env):
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, **env}, check=True).stdout.strip()


def test_site_on_the_command_line_moves_the_output_with_it(tmp_path):
    code = (f"import domus.app.cli,sys; domus.app.cli.choose_site({str(tmp_path)!r}); from domus.project.site import started; "
            "print(started().output); print(started().path)")
    out = run_python(code, DOMUS_OUTPUT=str(tmp_path / "elsewhere"), DOMUS_SITE=str(tmp_path / "other")).splitlines()
    assert out == [str((tmp_path / "viewer" / "data").resolve()), str(tmp_path.resolve())]


def test_output_stays_where_the_environment_says_without_site(tmp_path):
    out = run_python("from domus.project.site import started; print(started().output)", DOMUS_OUTPUT=str(tmp_path / "there"),
                     DOMUS_SITE=str(tmp_path))
    assert out == str((tmp_path / "there").resolve())
