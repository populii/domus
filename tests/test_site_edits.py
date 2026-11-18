"""Edits to a site's layers that keep what was there: domus site shift, edit and regrade, on a copy of the demo."""

import json
import os
import subprocess
import sys

import numpy as np


def _domus(root, *args):
    env = {k: v for k, v in os.environ.items() if k not in ("DOMUS_OUTPUT", "DOMUS_SITE")}
    return subprocess.run([sys.executable, "-m", "domus.app.cli", "--site", str(root), *args], env=env, capture_output=True,
                          text=True, encoding="utf-8", check=False)


def _spec(tmp_path, name, spec):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return str(path)


def test_layer_edits_change_what_they_name_and_keep_what_was_there(tmp_path):
    root = tmp_path / "demo"
    env = {k: v for k, v in os.environ.items() if k not in ("DOMUS_OUTPUT", "DOMUS_SITE")}
    subprocess.run([sys.executable, "-m", "domus.app.cli", "demo", str(root)], check=True, env=env, capture_output=True)
    walls = root / "data" / "site" / "walls-existing.geojson"
    raw = walls.read_text(encoding="utf-8")
    w1 = json.loads(raw)["features"][0]["geometry"]["coordinates"]

    # shift: a dry run writes nothing; --apply moves W1 by the vector, says so in its basis, and keeps the old file
    shift = _spec(tmp_path, "shift", {"shift": [0.5, -0.25], "basis": "Moved for the test.",
                                      "files": {"walls-existing.geojson": ["W1"]}})
    dry = _domus(root, "site", "shift", shift, str(tmp_path / "b1"))
    assert "dry run: nothing written" in dry.stdout and walls.read_text(encoding="utf-8") == raw
    done = _domus(root, "site", "shift", shift, str(tmp_path / "b1"), "--apply")
    assert done.returncode == 0, done.stderr
    moved = json.loads(walls.read_text(encoding="utf-8"))["features"][0]
    assert np.allclose(np.asarray(moved["geometry"]["coordinates"])[:, :2], np.asarray(w1)[:, :2] + [0.5, -0.25])
    assert moved["properties"]["basis"].endswith("Moved for the test.")
    assert (tmp_path / "b1" / "walls-existing.geojson").read_text(encoding="utf-8") == raw
    again = _domus(root, "site", "shift", shift, str(tmp_path / "b1"), "--apply")
    assert again.returncode != 0 and "already there" in again.stderr + again.stdout

    # edit: a property changed, a point added on the grid, and the rest of the file untouched
    before = json.loads(walls.read_text(encoding="utf-8"))
    edit = _spec(tmp_path, "edit", {"walls-existing.geojson": [
        {"id": "W1", "props": {"confidence": "checked"}},
        {"id": "post", "new": {"type": "Point"}, "enz": [1500010.0, 5500020.0, 41.5], "props": {"name": "a post"}}]})
    assert _domus(root, "site", "edit", edit, str(tmp_path / "b2"), "--apply").returncode == 0
    after = json.loads(walls.read_text(encoding="utf-8"))["features"]
    assert after[0]["properties"]["confidence"] == "checked" and after[0]["geometry"] == before["features"][0]["geometry"]
    assert after[1] == {"type": "Feature", "properties": {"id": "post", "name": "a post"},
                        "geometry": {"type": "Point", "coordinates": [1500010.0, 5500020.0, 41.5]}}

    # regrade: a ground-fix line's vertices take the cloud's ground, 5 cm over their levels; one the cloud doesn't see
    # moves with its neighbours
    fixes = root / "data" / "site" / "ground-fixes.geojson"
    line = [[1500004.0 + 2 * k, 5500010.0, 41.0] for k in range(5)]
    fixes.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"id": "lawn"},
                     "geometry": {"type": "LineString", "coordinates": line}}]}, indent=1) + "\n", encoding="utf-8")
    rng = np.random.default_rng(3)
    seen = line[:4]
    pts = np.vstack([np.column_stack([rng.normal(e, 0.1, 50), rng.normal(n, 0.1, 50), np.full(50, h + 0.05)]) for e, n, h in seen])
    (root / "data" / "video").mkdir(parents=True, exist_ok=True)
    np.savez(root / "data" / "video" / "walk-dense.npz", xyz=pts)
    regrade = _spec(tmp_path, "regrade", {"features": ["lawn"], "basis": "Levels from the walk."})
    out = _domus(root, "site", "regrade", "walk-dense", regrade, str(tmp_path / "b3"), "--apply")
    assert out.returncode == 0, out.stderr
    assert "lawn: 5 vertices, 4 read from the cloud" in out.stdout
    got = json.loads(fixes.read_text(encoding="utf-8"))["features"][0]
    assert np.allclose(np.asarray(got["geometry"]["coordinates"])[:, 2], 41.05) and got["properties"]["basis"] == "Levels from the walk."
