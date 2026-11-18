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
    (tmp_path / "data" / "site.toml").write_text("", encoding="utf-8")       # a site project: its house is read by its source
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
    (tmp_path / "data" / "site.toml").write_text("", encoding="utf-8")
    (site / "boundary.geojson").write_text(fc(feature("Polygon", [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]], id="lot")), encoding="utf-8")
    s = build.snap_targets(at_site(tmp_path), (0.0, 0.0, 0.0))
    assert len(s["lines"]) == 1 and s["points"] == []


def test_the_viewers_s_and_o_are_the_options_frames():
    """The viewer's s and o (the corner readout while dragging, the sketch editor's corners, the s and o saved with a
    sketch) are domus.options.scheme.SiteFrame's, with the lot on the frame line's right, as unless a site says, and on
    its left, as [frame] side = "left" has it."""
    import numpy as np

    from domus.options.scheme import SiteFrame

    here = Path(__file__).parent
    core = here.parent / "src" / "domus" / "app" / "viewer" / "draw-core.js"
    rng = np.random.default_rng(4)
    en = (rng.uniform(-25.0, 25.0, (10, 2)) + [1500011.0, 5500015.0]).tolist()
    so = rng.uniform(-5.0, 30.0, (10, 2)).tolist()
    frames = [{"origin": [1500022.0, 5500030.0], "toward": [1500022.0, 5500000.0]},                    # the demo's
              {"origin": [1500022.0, 5500000.0], "toward": [1500022.0, 5500030.0], "side": "left"},     # the left site's
              {"origin": [1500003.0, 5500001.0], "toward": [1500021.0, 5500009.5], "side": "right"},
              {"origin": [1500003.0, 5500001.0], "toward": [1500021.0, 5500009.5], "side": "left"}]
    out = subprocess.run(["node", str(here / "options_frame_check.mjs"), str(core)], capture_output=True, text=True,
                         input=json.dumps([{"frame": f, "en": en, "so": so} for f in frames]), check=False)
    assert out.returncode == 0, out.stderr
    for f, got in zip(frames, json.loads(out.stdout), strict=True):
        frame = SiteFrame(f["origin"], f["toward"], f.get("side", "right"))
        assert np.allclose(got["so"], frame.so(np.array(en)), rtol=0, atol=1e-6), f
        assert np.allclose(got["en"], frame.en(np.array(so)), rtol=0, atol=1e-6), f
