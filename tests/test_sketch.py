"""A sketch as a wall option, on the demo site: the sums, the file it makes and the server's side of it."""

import http.client
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

import numpy as np
import pytest

from domus.app import serve


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("sketch") / "demo"
    env = {k: v for k, v in os.environ.items() if k not in ("DOMUS_OUTPUT", "DOMUS_SITE")}
    subprocess.run([sys.executable, "-m", "domus.app.cli", "demo", str(root)], check=True, env=env, capture_output=True)
    return root


def script(site, code):
    env = {**{k: v for k, v in os.environ.items() if k != "DOMUS_OUTPUT"}, "DOMUS_SITE": str(site)}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


CENTRE = """
import json, numpy as np
from domus.model import terrain
from domus.options import design, sketch
from domus.project.site import started
site = started()
c = design.regrade_area(site).representative_point()
e, n = c.x, c.y
g = float(terrain.ground(site).sample(np.array([e]), np.array([n]))[0])
"""


def test_behind_is_square_to_the_line_and_mitred_at_a_corner():
    pts = serve_sketch().behind([[0, 0], [10, 0], [10, 10]], "right", 2.0)
    assert np.allclose(pts[0], [0, -2]) and np.allclose(pts[-1], [12, 10]) and np.allclose(pts[1], [12, -2])
    assert np.allclose(serve_sketch().behind([[0, 0], [10, 0]], "left", 2.0)[0], [0, 2])


def serve_sketch():
    # behind() needs no site.
    from domus.options import sketch
    return sketch


def test_a_retaining_wall_shapes_the_ground_and_reports_itself(site):
    r = script(site, CENTRE + """
items = [{"id": 1, "kind": "wall", "name": "Low wall", "en": [[e - 3, n], [e + 3, n]], "material": "timber", "mode": "level",
          "tops": [g + 1.0, g + 1.0], "retains": "left", "back": 2.0}]
a = sketch.evaluate(site, items)
print(json.dumps({"walls": a["walls"], "fill": a["volumes"]["fill_m3"], "grid": a["grid"] is not None}))
""")
    assert r["walls"][0]["id"] == "S1" and r["walls"][0]["retained_max_m"] > 0.3
    assert r["fill"] > 1 and r["grid"]


def test_it_says_nothing_about_cost_or_consent(site):
    r = script(site, CENTRE + """
a = sketch.evaluate(site, [{"id": 1, "kind": "wall", "name": "W", "en": [[e - 3, n], [e + 3, n]], "material": "block", "mode": "level", "tops": [g + 2.5, g + 2.5], "retains": "left"}])
print(json.dumps(a, default=float))
""")
    text = json.dumps({k: v for k, v in r.items() if k != "grid"}).lower()
    assert "cost" not in text and "consent" not in text


def test_an_area_falls_on_its_compass_direction(site):
    r = script(site, CENTRE + """
t = sketch.area_terrace({"id": 2, "kind": "area", "en": [[e, n], [e + 4, n], [e + 4, n + 4], [e, n + 4]], "level": 50.0, "fall": "1:50", "fall_to": "north", "area": "bed"})
print(json.dumps(t))
""")
    z = {tuple(p[:2]): p[2] for p in r["polygon"]}
    north, south = [v for k, v in z.items() if k[1] == max(q[1] for q in z)], [v for k, v in z.items() if k[1] == min(q[1] for q in z)]
    assert north[0] < 50 < south[0] and north[0] == pytest.approx(49.96) and r["kind"] == "planting"


def test_the_file_it_writes_gives_the_same_answer(site):
    r = script(site, CENTRE + """
import tomllib
items = [{"id": 1, "kind": "wall", "name": 'A "quoted" wall', "en": [[e - 3, n], [e + 3, n], [e + 3, n + 2]], "material": "stone", "mode": "level", "tops": [g + 1, g + 1, g + .8], "retains": "left"},
         {"id": 2, "kind": "wall", "name": "Height wall", "en": [[e - 3, n + 5], [e + 3, n + 5]], "material": "crib", "mode": "height", "height": 0.5}]
opt = sketch.to_option(site, items, "My sketch")
text = sketch.to_toml(opt)
back = tomllib.loads(text)
one = design.evaluate(site, opt, step=0.25)
two = design.evaluate(site, {**back, "id": "x"}, step=0.25)
print(json.dumps({"same": abs(one["volumes"]["fill_m3"] - two["volumes"]["fill_m3"]) < 0.5, "name": back["name"], "wall": back["walls"][0]["name"]}))
""")
    assert r == {"same": True, "name": "My sketch", "wall": 'A "quoted" wall'}


def test_an_empty_sketch_is_empty(site):
    assert script(site, CENTRE + 'print(json.dumps(sketch.evaluate(site, [{"id": 1, "kind": "wall", "en": [[0, 0]]}])))')["empty"]


def test_the_worker_answers_line_by_line_and_survives_a_bad_one(site):
    env = {**{k: v for k, v in os.environ.items() if k != "DOMUS_OUTPUT"}, "DOMUS_SITE": str(site)}
    p = subprocess.Popen([sys.executable, "-m", "domus.app.cli", "sketch-worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
    try:
        for req in ('{"items": []}', '{"nothing": 1}', '{"items": []}'):
            p.stdin.write(req + "\n")
            p.stdin.flush()
            ans = json.loads(p.stdout.readline())
            assert ans.get("empty") or "error" in ans
    finally:
        p.stdin.close()
        p.wait(10)


def test_the_worker_says_what_setting_needs_putting_right_and_stays_up(site, tmp_path):
    root = tmp_path / "site"
    shutil.copytree(site, root, ignore=shutil.ignore_patterns("viewer", ".domus"))
    shared = root / "options" / "_site.toml"
    text = shared.read_text(encoding="utf-8")
    shared.write_text(text[:text.index("[regrade]")] + text[text.index("[defaults]"):], encoding="utf-8")
    env = {**{k: v for k, v in os.environ.items() if k != "DOMUS_OUTPUT"}, "DOMUS_SITE": str(root)}
    p = subprocess.Popen([sys.executable, "-m", "domus.app.cli", "sketch-worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
    wall = {"id": 1, "kind": "wall", "name": "W", "en": [[0, 0], [5, 0]], "material": "timber", "mode": "height", "height": 0.5}
    try:
        for _ in range(2):          # it answers, and is still there to answer again
            p.stdin.write(json.dumps({"items": [wall]}) + "\n")
            p.stdin.flush()
            assert "has no [regrade] section" in json.loads(p.stdout.readline())["error"]
    finally:
        p.stdin.close()
        p.wait(10)


def test_saving_a_sketch_says_what_needs_putting_right(site, tmp_path):
    from domus.errors import DomusError

    root = tmp_path / "site"
    shutil.copytree(site / "options", root / "options")        # no data/site.toml: it isn't a site project
    wall = {"id": 1, "kind": "wall", "name": "W", "en": [[0, 0], [5, 0]], "material": "timber", "mode": "height", "height": 0.5}
    with pytest.raises(DomusError, match="isn't a site project"):
        serve.save_option(root, "W", [wall], own_output=True)
    assert not list((root / "options").glob("sketch-*.toml"))


def call(port, method, path, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port)
    c.request(method, path, body, {"Content-Type": "application/json"} if body is not None else {})
    r = c.getresponse()
    out = (r.status, json.loads(r.read() or b"{}"))
    c.close()
    return out


def test_the_server_works_a_sketch_out_and_saves_it_as_an_option(site, monkeypatch):
    monkeypatch.delenv("DOMUS_OUTPUT", raising=False)
    built = []
    monkeypatch.setattr(serve.BuildJob, "start", lambda self: built.append(self.root))
    httpd = serve.make_server("127.0.0.1", 0, [site / "viewer" / "data"], site / ".domus" / "viewer.db", root=site)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        boundary = json.loads((site / "data" / "site" / "boundary.geojson").read_text(encoding="utf-8"))["features"][0]["geometry"]["coordinates"][0]
        cx, cy = np.mean(boundary, axis=0)[:2]
        items = [{"id": 1, "kind": "wall", "name": "W", "en": [[cx - 2, cy], [cx + 2, cy]], "material": "timber", "mode": "height", "height": 0.5}]
        status, ans = call(port, "POST", "/api/sketch", json.dumps({"items": items}))
        assert status == 200 and ans["walls"][0]["length_m"] == pytest.approx(4.0, abs=0.01)
        assert call(port, "POST", "/api/sketch", "not json")[0] == 400
        assert call(port, "POST", "/api/sketch/option", json.dumps({"name": "", "items": items}))[0] == 400
        status, ans = call(port, "POST", "/api/sketch/option", json.dumps({"name": "My Own! Option", "items": items}))
        assert status == 202 and ans["file"] == "options/sketch-my-own-option.toml" and built == [site]
        saved = tomllib.loads((site / ans["file"]).read_text(encoding="utf-8"))
        assert saved["name"] == "My Own! Option"
        assert call(port, "POST", "/api/sketch/option", json.dumps({"name": "My Own! Option", "items": items}))[1]["file"] == "options/sketch-my-own-option-2.toml"
        assert call(port, "GET", "/api/sketch/option")[0] == 200
    finally:
        httpd.handler_class_worker().stop()
        httpd.shutdown()
        httpd.server_close()
        for f in (site / "options").glob("sketch-*.toml"):
            f.unlink()
    time.sleep(0)


AT = np.array([1500000.0, 5500000.0])       # on a grid as large as New Zealand's
SHAPES = {"a square": [[0, 0], [4, 0], [4, 4], [0, 4]],
          "a square with a corner twice": [[0, 0], [4, 0], [4, 0], [4, 4], [0, 4]],
          "a square closed": [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]],
          "a square with a corner on a straight side": [[0, 0], [2, 0], [4, 0], [4, 4], [0, 4]],
          "a bow-tie": [[0, 0], [4, 4], [4, 0], [0, 4]],
          "one touching itself at a corner": [[0, 0], [4, 0], [2, 2], [4, 4], [0, 4], [2, 2]],
          "one with a corner on another side": [[0, 0], [4, 0], [4, 4], [2, 0], [0, 4]],
          "one doubling back along a side": [[0, 0], [4, 0], [2, 0], [2, 3], [0, 3]],
          "three corners in a line": [[0, 0], [2, 0], [4, 0]]}


def held(en, **kw):
    return {"id": 2, "kind": "wall", "name": "Wall 2", "en": (AT + np.array(en, float)).tolist(), "mode": "level",
            "tops": [42.0] * len(en), "retains": "left", **kw}


def test_the_viewer_and_the_server_say_the_same_of_outlines_that_cross_themselves(tmp_path):
    """The viewer's check (sketch-core.js's crossesItself and crossings) and the server's (scheme.crossing and
    sketch.crossings) agree, word for word, on outlines that cross, touch or double back on themselves and on ones
    that don't, and on the ground a wall holds: its line and the line behind it."""
    from domus.options import scheme, sketch

    rings = [(AT + np.array(v, float)).tolist() for v in SHAPES.values()]
    rng = np.random.default_rng(3)
    rings += [(AT + rng.uniform(0.0, 12.0, (k, 2))).tolist() for k in (4, 5, 6, 8) for _ in range(25)]
    bow = rings[list(SHAPES).index("a bow-tie")]
    sketches = [[{"id": 1, "kind": "area", "name": "Lawn", "en": bow}],
                [{"id": 7, "kind": "area", "en": bow}],
                [{"id": 1, "kind": "area", "name": "Lawn", "en": rings[0]}],
                [held([[0, 0], [10, 0]])],
                [held([[0, 0], [2, 6], [4, 0]], retains="right", back=5.0)],      # a narrow V, the ground inside it
                [held([[0, 0], [2, 6], [4, 0]], back=5.0)],                       # and outside it
                [held([[0, 0], [5, 1], [10, 0]], retains="right")],
                [held([[0, 0], [6, 0], [6, 3], [3, -2]], back=1.0)],              # a line that crosses itself
                [held([[0, 0], [2, 6], [4, 0]], retains="right", back=5.0, mode="height", tops=None)],
                [held([[0, 0], [2, 6], [4, 0]], retains="none", back=5.0)]]
    (tmp_path / "cases.json").write_text(json.dumps({"rings": rings, "sketches": sketches}), encoding="utf-8")
    here = Path(__file__).parent
    out = subprocess.run(["node", str(here / "sketch_crossing_check.mjs"), str(here.parent / "src" / "domus" / "app" / "viewer" / "sketch-core.js"),
                          str(tmp_path / "cases.json")], capture_output=True, text=True, encoding="utf-8", check=False, timeout=120)
    assert out.returncode == 0, out.stderr
    js = json.loads(out.stdout)
    assert js["rings"] == [scheme.crossing(r) is not None for r in rings]
    assert js["rings"][:len(SHAPES)] == [False, False, False, False, True, True, True, True, True]
    assert 0 < sum(js["rings"][len(SHAPES):]) < len(rings) - len(SHAPES)
    assert js["sketches"] == [sketch.crossings(s) for s in sketches]
    area = "The outline of {} crosses itself: move its corners so that no side crosses another."
    wall = "The ground behind Wall 2 crosses itself: move the wall's corners, or hold less ground behind it."
    assert js["sketches"] == [[area.format("Lawn")], [area.format("Area 7")], [], [], [wall], [], [], [wall], [], []]


def test_the_server_wont_save_a_sketch_whose_outline_crosses_itself(site, monkeypatch):
    """A sketch whose outline crosses itself isn't saved as an option, with the message the viewer gives: the build
    would take the outline as shapely's buffer(0) reshapes it. Nothing is written and nothing built."""
    monkeypatch.delenv("DOMUS_OUTPUT", raising=False)
    built = []
    monkeypatch.setattr(serve.BuildJob, "start", lambda self: built.append(self.root))
    httpd = serve.make_server("127.0.0.1", 0, [site / "viewer" / "data"], site / ".domus" / "viewer.db", root=site)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        boundary = json.loads((site / "data" / "site" / "boundary.geojson").read_text(encoding="utf-8"))["features"][0]["geometry"]["coordinates"][0]
        cx, cy = np.mean(boundary, axis=0)[:2]
        bow = [[cx - 2, cy - 2], [cx + 2, cy + 2], [cx + 2, cy - 2], [cx - 2, cy + 2]]
        items = [{"id": 3, "kind": "area", "name": "Lawn", "en": bow, "area": "lawn", "level": 42.0, "fall": "flat", "fall_to": "north"}]
        status, ans = call(port, "POST", "/api/sketch/option", json.dumps({"name": "Crossed", "items": items}))
        assert status == 400
        assert ans["error"] == "The outline of Lawn crosses itself: move its corners so that no side crosses another."
        assert not list((site / "options").glob("sketch-*.toml")) and built == []
    finally:
        httpd.handler_class_worker().stop()
        httpd.shutdown()
        httpd.server_close()


def test_the_build_warns_of_a_terrace_whose_outline_crosses_itself():
    """A terrace written by hand whose outline crosses itself is taken as shapely's buffer(0) reshapes it: the build
    says so, naming the option and the terrace, and how much of what it closes round is kept."""
    from types import SimpleNamespace

    from domus import build
    from domus.options import scheme

    bow = scheme.Terrace({"id": "T1", "name": "Lawn", "polygon": [[0, 0], [4, 4], [4, 0], [0, 4]], "level": 42.0}, {"batter": 1.0}, None)
    fine = scheme.Terrace({"id": "T2", "name": "Bed", "polygon": [[0, 0], [4, 0], [4, 4], [0, 4]], "level": 42.0}, {"batter": 1.0}, None)
    assert bow.crossing == (8.0, 4.0) and fine.crossing is None
    found = [({"path": "options/a.toml"}, {"design": SimpleNamespace(terraces=[bow, fine])})]
    assert build.crossing_warnings(found) == [
        ("Warning: options/a.toml: terrace T1 (Lawn): its outline crosses itself, so the build takes it as shapely's "
         "buffer(0) reshapes it, 4.0 m2 of the 8.0 m2 it closes round. Move its corners so that no side crosses another.")]
