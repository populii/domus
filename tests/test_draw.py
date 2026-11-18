"""Exact drawing: its sums, under node, and what the build gives it to snap to."""

import json
import subprocess
from pathlib import Path

from domus import build


def test_the_sums():
    here = Path(__file__).parent
    core = here.parent / "src" / "domus" / "app" / "viewer" / "draw-core.js"
    out = subprocess.run(["node", str(here / "draw_core_check.mjs"), str(core)], capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr


def fc(*features):
    return json.dumps({"type": "FeatureCollection", "features": list(features)})


def feature(gtype, coords, **props):
    return {"type": "Feature", "geometry": {"type": gtype, "coordinates": coords}, "properties": props}


def test_the_model_lists_what_to_snap_to_in_viewer_metres(tmp_path, at_site):
    site = tmp_path / "data" / "site"
    site.mkdir(parents=True)
    (site / "boundary.geojson").write_text(fc(feature("Polygon", [[[100.0, 200.0], [110.0, 200.0], [110.0, 210.0], [100.0, 200.0]]], id="lot", name="Lot")), encoding="utf-8")
    (site / "house.geojson").write_text(fc(feature("Polygon", [[[102.0, 202.0], [104.0, 202.0], [104.0, 204.0], [102.0, 202.0]]], id="house-walls"),
                                           feature("LineString", [[102.0, 202.0], [103.0, 202.0]], id="door-1", kind="door")), encoding="utf-8")
    (site / "walls-existing.geojson").write_text(fc(feature("LineString", [[101.0, 201.0, 45.0], [101.0, 205.0, 45.0]], id="W1", name="Old wall")), encoding="utf-8")
    (site / "features-survey-marks.geojson").write_text(fc(feature("Point", [105.0, 205.0], id="m", kind="mark", name="PEG 1")), encoding="utf-8")
    s = build.snap_targets(at_site(tmp_path), (100.0, 200.0, 40.0))
    assert s["lines"][0] == {"label": "Boundary", "pts": [[0.0, -0.0], [10.0, -0.0], [10.0, -10.0], [0.0, -0.0]]}
    assert [li["label"] for li in s["lines"]] == ["Boundary", "House", "Old wall"]
    assert s["lines"][2]["pts"] == [[1.0, -1.0], [1.0, -5.0]]
    assert s["points"] == [{"label": "PEG 1", "x": 5.0, "z": -5.0}]


def test_a_site_with_only_a_boundary_is_fine(tmp_path, at_site):
    site = tmp_path / "data" / "site"
    site.mkdir(parents=True)
    (site / "boundary.geojson").write_text(fc(feature("Polygon", [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]], id="lot")), encoding="utf-8")
    s = build.snap_targets(at_site(tmp_path), (0.0, 0.0, 0.0))
    assert len(s["lines"]) == 1 and s["points"] == []
