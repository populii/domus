"""The prep steps on made-up documents: a PDF, a photo, the demo site."""

import json

import numpy as np
import pytest
from PIL import Image

from domus.errors import DomusError

pytest.importorskip("pdfplumber")


def write_pdf(path, width, height, content):
    """A one-page PDF with the given content stream (y up, as PDF draws) and Helvetica as /F1."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>".encode(),
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))


@pytest.fixture
def prep_json(site):
    path = site / "data" / "prep.json"
    yield path
    path.unlink(missing_ok=True)


def run(project, step, *args):
    from domus.app import cli

    return cli.call(["prep", step, *args], project)


def test_trace_puts_a_vector_sheet_on_the_grid(site, prep_json, project):
    raw = site / "sources" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    # A red line 100 pt down the page, a black one 200 pt down and a label between them (PDF's y runs up from 400).
    write_pdf(raw / "plan.pdf", 600, 400, b"1 0 0 RG 1 w 100 300 m 300 300 l S 0 0 0 RG 2 w 100 200 m 300 200 l S "
                                          b"BT /F1 12 Tf 150 250 Td (1.05) Tj ET")
    ring = json.loads((site / "data" / "site" / "boundary.geojson").read_text(encoding="utf-8"))["features"][0]["geometry"]["coordinates"][0]
    a, b = ring[0], ring[1]
    prep_json.write_text(json.dumps({"plans": {"trace": [{
        "name": "plan", "pdf": "sources/raw/plan.pdf", "out": "data/sources/plan.geojson", "window": [50, 50, 500, 350],
        "text": "words", "fill_colour": True, "colours": {"1.0,0.0,0.0": "drain", "0.0,0.0,0.0": "line"},
        "fit": {"kind": "similarity", "paper": {"A": [100, 100], "B": [300, 100]}, "boundary_corners": ["A", "B"]}}]}}), encoding="utf-8")
    run(project, "plan-trace")
    feats = json.loads((site / "data" / "sources" / "plan.geojson").read_text(encoding="utf-8"))["features"]
    drain = next(f for f in feats if f["properties"]["layer"] == "drain")
    assert np.allclose(drain["geometry"]["coordinates"], [a, b], atol=1e-6)
    assert any(f["properties"].get("text") == "1.05" for f in feats)
    assert any(f["properties"]["layer"] == "line" for f in feats)


def test_feature_textures_rectify_and_tile(site, prep_json, project):
    raw = site / "sources" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    a = np.zeros((200, 200, 3), np.uint8)
    a[50:150, 50:150] = (200, 100, 50)
    Image.fromarray(a).save(raw / "photo.png")
    prep_json.write_text(json.dumps({"feature_textures": {
        "textures": {"plate.jpg": {"source": "photo.png", "quad": [[60, 60], [140, 60], [140, 140], [60, 140]], "size": [64, 64], "seams": "xy"}},
        "turned": {"plate-up.jpg": "plate.jpg"}}}), encoding="utf-8")
    run(project, "feature-textures")
    out = np.asarray(Image.open(site / "data" / "features" / "plate.jpg"))
    assert out.shape == (64, 64, 3)
    assert np.abs(out.mean(axis=(0, 1)) - (200, 100, 50)).max() < 8
    assert (site / "data" / "features" / "plate-up.jpg").exists()
    for name in ("plate.jpg", "plate-up.jpg"):
        (site / "data" / "features" / name).unlink()   # the demo draws plain; leave it so


def test_check_finds_a_duplicate_id_on_the_demo(site, built, project):
    from domus.checks import model as check

    m = check.Model(project)
    findings = []
    check.check_sanity(m, findings)
    assert not [f for f in findings if f[1] == "duplicate"]
    assert not [f for f in findings if f[1] == "ground"], findings      # the ground is found everywhere in the lot
    m.site_features.append(m.site_features[0])
    findings = []
    assert check.check_sanity(m, findings) >= 1
    assert [f for f in findings if f[1] == "duplicate"]


def test_two_ground_fixes_at_one_place_are_warned_of(tmp_path, at_site):
    from domus import build
    from domus.checks import model as check
    from domus.project import demo

    # A copy of the demo with a made-up ground fix: a taped line, and a spot level on one of its corners 30 mm higher.
    # The build warns, and the sanity check reports it, naming both and their heights; the later is kept.
    root = tmp_path / "fixes"
    demo.write(root, say=lambda *_: None)
    x0, y0 = demo.X0, demo.Y0
    layer = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": "lawn-ground", "name": "Lawn (made up)"}, "geometry": {
            "type": "Polygon", "coordinates": [[[x0 + 10, y0 + 23], [x0 + 14, y0 + 23], [x0 + 14, y0 + 27], [x0 + 10, y0 + 27],
                                                [x0 + 10, y0 + 23]]]}},
        {"type": "Feature", "properties": {"id": "lawn-tape"}, "geometry": {"type": "LineString", "coordinates": [
            [x0 + 10.5, y0 + 23.5, 41.4], [x0 + 12.0, y0 + 25.0, 41.3], [x0 + 13.5, y0 + 26.5, 41.2]]}},
        {"type": "Feature", "properties": {"id": "lawn-spot"}, "geometry": {"type": "Point", "coordinates": [x0 + 12.0, y0 + 25.0, 41.33]}}]}
    (root / "data" / "site" / "ground-fixes.geojson").write_text(json.dumps(layer), encoding="utf-8")
    site = at_site(root)
    said = ("ground fixes: lawn-tape and lawn-spot give one place two heights, 41.300 and 41.330 m; the later in the file, "
            "lawn-spot's 41.330 m, is kept. Make them agree, or take one out.")
    assert build.fix_warnings(site) == [f"Warning: data/site/ground-fixes.geojson: {said}"]
    findings = []
    check.check_sanity(check.Model(site), findings)
    assert [f for f in findings if f[1] == "warn"] == [("sanity", "warn", "lawn-spot", said)]


def test_a_missing_settings_file_says_so(site, prep_json, project):
    prep_json.unlink(missing_ok=True)
    with pytest.raises(DomusError, match="from-documents"):
        run(project, "plan-pages")


def test_each_command_keeps_its_own_help(capsys):
    from domus.app import cli

    with pytest.raises(SystemExit):
        cli.main(["scan", "--help"])
    assert "phone LiDAR" in capsys.readouterr().out


def box(x0, z0, x1, z1, y):
    """A level square at height y, in viewer metres, looking up as a solid's top does: positions and indices."""
    return {"positions": [x0, y, z0, x1, y, z0, x1, y, z1, x0, y, z1], "indices": [0, 2, 1, 0, 3, 2]}


def test_the_option_checks_find_nothing_wrong_on_the_demo(site, built, project):
    from domus.checks import model as check

    for f in (check.check_through, check.check_fixes, check.check_blocks):
        findings = []
        assert f(project, findings) == 0, findings


def test_ground_through_a_wall_or_over_a_tread_is_found(site, tmp_path, monkeypatch, at_site):
    from domus.checks import model as check

    model = {"origin": [100.0, 200.0, 40.0], "summary": {"options": [{"id": "x", "keep_existing": []}]},
             "layers": [{"id": "option-x-ground", "type": "mesh", "role": "ground", "option": "x", **box(-5, -5, 5, 5, 1.0)},
                        {"id": "option-x-W1", "type": "mesh", "option": "x", **box(-1, -1, 1, 1, 0.5)},
                        {"id": "option-x-steps-1", "type": "mesh", "option": "x", **box(3, 3, 4, 4, 0.9)}]}
    (tmp_path / "model.json").write_text(json.dumps(model), encoding="utf-8")
    there = at_site(site, output=tmp_path)
    findings = []
    assert check.check_through(there, findings) == 3
    through = {f[2]: f[3] for f in findings if "comes through" in f[3]}
    assert set(through) == {"x/W1", "x/steps-1"} and "by up to 0.500 m" in through["x/W1"]
    assert "height 40.50" in through["x/W1"]                                # where, on the site's heights
    assert [f for f in findings if "stands over a tread's edge" in f[3] and f[2] == "x/steps-1"]
    (tmp_path / "model.json").unlink()
    findings = []
    assert check.check_through(there, findings) == 0 and findings[0][1] == "info"


def test_the_soil_over_a_walls_body_behind_its_cap_is_how_it_is_built(site, tmp_path, at_site):
    """A wall whose units are deeper than its capping units: behind the cap's back (z 0 to 1) the body's top is 0.1 m
    under the cap's top, and the soil behind the wall is graded flush with the cap's top over it, as a maker's manual
    has it. The layer says so (`soil_over`, `soil_over_m`), so the check doesn't take that soil for ground coming
    through; ground over the cap's top there still is."""
    from domus.checks import model as check

    cap = box(-1, -1, 1, 0, 1.0)                                       # the cap's top, then the body's top behind it
    body = box(-1, 0, 1, 1, 0.9)
    wall = {"id": "option-x-W1", "type": "mesh", "option": "x", "soil_over": [2, 3], "soil_over_m": 0.1,
            "positions": cap["positions"] + body["positions"], "indices": cap["indices"] + [i + 4 for i in body["indices"]]}
    for level, found in ((1.0, 0), (1.05, 1)):
        model = {"origin": [100.0, 200.0, 40.0], "summary": {"options": [{"id": "x", "keep_existing": []}]},
                 "layers": [{"id": "option-x-ground", "type": "mesh", "role": "ground", "option": "x", **box(-5, 0.2, 5, 5, level)},
                            wall]}
        (tmp_path / "model.json").write_text(json.dumps(model), encoding="utf-8")
        findings = []
        assert check.check_through(at_site(site, output=tmp_path), findings) == found, findings
    wall.pop("soil_over")                                              # without it, that soil is ground through the wall
    (tmp_path / "model.json").write_text(json.dumps({**model, "layers": [model["layers"][0] | {**box(-5, 0.2, 5, 5, 1.0)}, wall]}),
                                         encoding="utf-8")
    findings = []
    assert check.check_through(at_site(site, output=tmp_path), findings) == 1


def block(x0, z0, x1, z1, y0, y1):
    """A closed box from y0 up to y1 over a rectangle, in viewer metres, its faces turned outward: positions and
    indices."""
    positions = [c for y in (y0, y1) for c in (x0, y, z0, x1, y, z0, x1, y, z1, x0, y, z1)]
    indices = [0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6]                     # its base, looking down, and its top, up
    for i in range(4):
        j = (i + 1) % 4
        indices += [i, 4 + j, j, i, 4 + i, 4 + j]                       # its sides, looking out
    return {"positions": positions, "indices": indices}


def ramp(toe, back=0.4, low=0.4, high=1.0):
    """Ground in viewer metres: level at `low` up to x = `toe`, rising evenly to `high` at x = `back`, level beyond."""
    positions = [c for x, y in ((-3.0, low), (toe, low), (back, high), (3.0, high)) for c in (x, y, -3.0, x, y, 3.0)]
    indices = [k for a in (0, 2, 4) for k in (a, a + 2, a + 3, a, a + 3, a + 1)]
    return {"positions": positions, "indices": indices}


def tucked_corner(tmp_path, toe):
    """A made-up option: a thin wall's end tucked 0.08 m into a thicker wall at a corner, on ground that rises from
    `toe` across the thicker wall's body to the ground it holds, as the option's ground runs from its face to its back."""
    model = {"origin": [100.0, 200.0, 40.0], "summary": {"options": [{"id": "x", "keep_existing": []}]},
             "layers": [{"id": "option-x-ground", "type": "mesh", "role": "ground", "option": "x", **ramp(toe)},
                        {"id": "option-x-W1", "type": "mesh", "option": "x", **block(0.0, -2.0, 0.4, 2.0, -0.6, 1.2)},
                        {"id": "option-x-W2", "type": "mesh", "option": "x", **block(-0.5, -0.1, 0.08, 0.1, -0.3, 0.45)}]}
    (tmp_path / "model.json").write_text(json.dumps(model), encoding="utf-8")


def test_a_top_hidden_inside_another_wall_is_not_ground_through_it(site, tmp_path, at_site):
    """Inside the thicker wall the ground rises over the thin wall's tucked end, but nothing of it shows there."""
    from domus.checks import model as check

    tucked_corner(tmp_path, toe=0.0)
    there = at_site(site, output=tmp_path)
    findings = []
    assert check.check_through(there, findings) == 0, findings


def test_ground_through_a_wall_just_beside_a_thicker_one_is_still_found(site, tmp_path, at_site):
    """The ground starts rising 0.1 m out from the thicker wall's face, so it comes through the thin wall's top just
    beside it, by at most its height at that face over the top (0.07 m). The tucked end, where it would be more, isn't
    counted."""
    import re

    from domus.checks import model as check

    tucked_corner(tmp_path, toe=-0.1)
    there = at_site(site, output=tmp_path)
    findings = []
    assert check.check_through(there, findings) == 1, findings
    assert findings[0][2] == "x/W2" and findings[0][3].startswith("ground comes through")
    by = float(re.search(r"by up to ([\d.]+) m", findings[0][3]).group(1))
    assert check.TOL_THROUGH < by <= 0.070, findings


def test_a_block_piece_is_whole_half_or_cut():
    from domus.checks import model as check

    assert check._piece(3.0, 0, True) == 1.0 and check._piece(3.5, 1, False) == 1.0      # on a joint
    assert abs(check._piece(3.5, 0, True) - 0.5) < 1e-9 and not check._cut(0.5)          # half a block: a corner's end
    assert check._cut(check._piece(3.3, 0, True)) and not check._cut(check._piece(3.004, 0, True))


def test_a_profile_reads_the_ground_along_a_line_and_either_side(site, project):
    from domus.model import terrain
    from domus.options import design
    from domus.prep import profile

    assert [round(s, 3) for s, _, _ in profile.stations(np.array([[0.0, 0.0], [2.0, 0.0], [2.0, 1.5]]), 1.0)] == [0, 1, 2, 3, 3.5]
    g, fr = terrain.ground(project), design.frame(project)
    z = lambda en: float(np.atleast_1d(g.sample(np.array([en[0]]), np.array([en[1]])))[0])
    rows = profile.profile(project, [[4.0, 3.0], [8.0, 3.0]], offsets=(-1.0, 0.0, 1.0), step=2.0)
    assert [s for s, _, _ in rows] == [0.0, 2.0, 4.0]
    _, p, h = rows[1]
    assert np.allclose(p, [6.0, 3.0])
    # along s, the right is +o: into the lot, the way o grows
    for off in (-1.0, 0.0, 1.0):
        assert h[off] == pytest.approx(z(fr.en(np.array([6.0, 3.0 + off]))), abs=1e-9)
    e, n = fr.en(np.array([6.0, 3.0]))
    east = profile.profile(project, [[e - 1, n], [e + 1, n]], offsets=(1.0,), step=0, grid=True)
    assert east[0][2][1.0] == pytest.approx(z([e - 1, n - 1]), abs=1e-9)                       # east's right is south
    with pytest.raises(DomusError, match="two points or more"):
        profile.profile(project, [[0, 0]])


def test_the_checks_skip_what_a_site_doesnt_have(tmp_path):
    """A site with no house, no placed videos and no wall options: the checks that need them say why they're skipped,
    and the rest run. On its own copy of the demo, in a process of its own."""
    import os
    import subprocess
    import sys

    root = tmp_path / "bare"
    env = {k: v for k, v in os.environ.items() if k not in ("DOMUS_OUTPUT", "DOMUS_SITE")}
    subprocess.run([sys.executable, "-m", "domus.app.cli", "demo", str(root)], check=True, env=env, capture_output=True)
    (root / "data" / "site" / "house.geojson").unlink()
    for option in (root / "options").glob("[!_]*.toml"):
        option.unlink()
    out = subprocess.run([sys.executable, "-m", "domus.app.cli", "--site", str(root), "prep", "check", "sanity", "roof",
                          "plumb", "clouds", "through", "blocks"], env=env, capture_output=True, text=True, encoding="utf-8",
                         check=False)                  # exits 1 if a check fails; the report says what ran
    report = json.loads((root / ".domus" / "checks" / "check_model.json").read_text(encoding="utf-8"))
    assert set(report["failures"]) == {"sanity"}, out.stderr
    assert set(report["skipped"]) == {"roof", "plumb", "clouds", "through", "blocks"}
    assert "no house" in report["skipped"]["roof"] and "no placed videos" in report["skipped"]["clouds"]
    assert "no wall options" in report["skipped"]["blocks"] and "roof    skipped" in out.stdout
