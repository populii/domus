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

import numpy as np
import pytest

from domus import serve


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("sketch") / "demo"
    env = {k: v for k, v in os.environ.items() if k not in ("DOMUS_OUTPUT", "DOMUS_SITE")}
    subprocess.run([sys.executable, "-m", "domus.cli", "demo", str(root)], check=True, env=env, capture_output=True)
    return root


def script(site, code):
    env = {**{k: v for k, v in os.environ.items() if k != "DOMUS_OUTPUT"}, "DOMUS_SITE": str(site)}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=False)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


CENTRE = """
import json, numpy as np
from domus import design, terrain, sketch
c = design.regrade_area().representative_point()
e, n = c.x, c.y
g = float(terrain.ground().sample(np.array([e]), np.array([n]))[0])
"""


def test_behind_is_square_to_the_line_and_mitred_at_a_corner():
    pts = serve_sketch().behind([[0, 0], [10, 0], [10, 10]], "right", 2.0)
    assert np.allclose(pts[0], [0, -2]) and np.allclose(pts[-1], [12, 10]) and np.allclose(pts[1], [12, -2])
    assert np.allclose(serve_sketch().behind([[0, 0], [10, 0]], "left", 2.0)[0], [0, 2])


def serve_sketch():
    # Importing domus.sketch reads the site from the environment, which is harmless here: behind() needs none.
    from domus import sketch
    return sketch


def test_a_retaining_wall_shapes_the_ground_and_reports_itself(site):
    r = script(site, CENTRE + """
items = [{"id": 1, "kind": "wall", "name": "Low wall", "en": [[e - 3, n], [e + 3, n]], "material": "timber", "mode": "level",
          "tops": [g + 1.0, g + 1.0], "retains": "left", "back": 2.0}]
a = sketch.evaluate(items)
print(json.dumps({"walls": a["walls"], "fill": a["volumes"]["fill_m3"], "grid": a["grid"] is not None}))
""")
    assert r["walls"][0]["id"] == "S1" and r["walls"][0]["retained_max_m"] > 0.3
    assert r["fill"] > 1 and r["grid"]


def test_it_says_nothing_about_cost_or_consent(site):
    r = script(site, CENTRE + """
a = sketch.evaluate([{"id": 1, "kind": "wall", "name": "W", "en": [[e - 3, n], [e + 3, n]], "material": "block", "mode": "level", "tops": [g + 2.5, g + 2.5], "retains": "left"}])
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
opt = sketch.to_option(items, "My sketch")
text = sketch.to_toml(opt)
back = tomllib.loads(text)
one = design.evaluate(opt, step=0.25)
two = design.evaluate({**back, "id": "x"}, step=0.25)
print(json.dumps({"same": abs(one["volumes"]["fill_m3"] - two["volumes"]["fill_m3"]) < 0.5, "name": back["name"], "wall": back["walls"][0]["name"]}))
""")
    assert r == {"same": True, "name": "My sketch", "wall": 'A "quoted" wall'}


def test_an_empty_sketch_is_empty(site):
    assert script(site, CENTRE + 'print(json.dumps(sketch.evaluate([{"id": 1, "kind": "wall", "en": [[0, 0]]}])))')["empty"]


def test_the_worker_answers_line_by_line_and_survives_a_bad_one(site):
    env = {**{k: v for k, v in os.environ.items() if k != "DOMUS_OUTPUT"}, "DOMUS_SITE": str(site)}
    p = subprocess.Popen([sys.executable, "-m", "domus.cli", "sketch-worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
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
    p = subprocess.Popen([sys.executable, "-m", "domus.cli", "sketch-worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
    wall = {"id": 1, "kind": "wall", "name": "W", "en": [[0, 0], [5, 0]], "material": "timber", "mode": "height", "height": 0.5}
    try:
        for _ in range(2):          # it answers, and is still there to answer again
            p.stdin.write(json.dumps({"items": [wall]}) + "\n")
            p.stdin.flush()
            assert "has no [regrade] section" in json.loads(p.stdout.readline())["error"]
    finally:
        p.stdin.close()
        p.wait(10)


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
