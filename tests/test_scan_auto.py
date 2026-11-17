"""From a scan to the model: the fit to the AR track, on a made-up walk, and the job behind the page."""

import http.client
import json
import threading

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from domus import serve
from domus.capture.sfm_georef import apply, horn_initial


@pytest.fixture
def walk():
    """A walk of 20 m in a made-up reconstruction, a third of true size and turned, seen at site positions."""
    t = np.arange(0, 20.0, 0.5)
    site = np.column_stack([1000 + t, 2000 + 0.5 * t, 40 + 0.01 * t])
    rot = Rotation.from_euler("xyz", [20, -35, 60], degrees=True)
    local = rot.inv().apply((site - site[0]) / 3.0)
    return t, site, local


def test_the_fit_recovers_scale_and_place(site, walk):
    from domus.capture import auto

    t, pos, local = walk
    params, res = auto.fit_to_track(dict(zip(t.tolist(), local)), t, pos)
    assert np.exp(params[0]) == pytest.approx(3.0, abs=0.01)
    assert res.max() < 0.01
    assert apply(params, local[-1:])[0] == pytest.approx(pos[-1], abs=0.01)


def test_a_short_walk_is_refused(site, walk):
    from domus.capture import auto

    t, pos, local = walk
    still = pos[:12] * 0 + pos[0]
    assert auto.fit_to_track(dict(zip(t[:12].tolist(), local[:12])), t[:12], still)[0] is None
    assert auto.fit_to_track(dict(zip(t[:3].tolist(), local[:3])), t[:3], pos[:3])[0] is None


def test_noise_in_the_track_is_reported(site, walk):
    from domus.capture import auto

    t, pos, local = walk
    noisy = pos + np.random.default_rng(1).normal(0, 0.3, pos.shape)
    params, res = auto.fit_to_track(dict(zip(t.tolist(), local)), t, noisy)
    assert np.median(res) > 0.1
    assert np.exp(params[0]) == pytest.approx(3.0, abs=0.2)


def test_only_times_inside_the_track_are_used(site, walk):
    from domus.capture import auto

    t, pos, local = walk
    a, b = auto.match(dict(zip((t + 15).tolist(), local)), t, pos)
    assert 0 < len(a) < len(t)
    assert horn_initial(a, b) is not None


def test_pose_track_turns_viewer_metres_into_the_grid(site):
    from domus.capture import auto

    poses = {"started": 1000, "frames": [{"t": 1000, "x": 0, "z": 0, "y": 0}, {"t": 2000, "x": 2, "z": 3, "y": 0.5}]}
    t, pos = auto.pose_track(poses, [100.0, 200.0, 30.0], lambda e, n: np.full(np.shape(e), 40.0))
    assert t.tolist() == [0.0, 1.0]
    assert pos[1].tolist() == pytest.approx([102.0, 197.0, 40 + auto.EYE_HEIGHT_M + 0.5])


def test_ground_shift_uses_the_lowest_points(site):
    from domus.capture import auto

    e, n = np.meshgrid(np.arange(0, 10.0, 0.5), np.arange(0, 10.0, 0.5))
    floor = np.column_stack([e.ravel(), n.ravel(), np.full(e.size, 38.0)])
    tree = floor + [0, 0, 5]
    assert auto.ground_shift(np.vstack([floor, tree]), lambda a, b: np.full(np.shape(a), 40.0)) == pytest.approx(2.0)


def test_cloud_rebuild_is_held_back_when_listed_clouds_are_missing(site):
    from domus.capture import auto

    assert auto.missing_clouds({"video": {"clouds": ["nowhere-site"]}}) == ["nowhere-site"]
    assert auto.missing_clouds({}) == []


@pytest.fixture
def app(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(serve.ScanJob, "start", lambda self, rel: started.append(rel))
    out = tmp_path / "out"
    out.mkdir()
    (out / "model.json").write_text("{}", encoding="utf-8")
    httpd = serve.make_server("127.0.0.1", 0, [out], tmp_path / "v.db", root=tmp_path)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], tmp_path, started
    httpd.shutdown()
    httpd.server_close()


def post(port, body, kind="application/json"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("POST", "/api/scan-job", json.dumps(body), {"Content-Type": kind})
    return c.getresponse().status


def test_the_job_starts_only_on_a_saved_scan(app):
    port, root, started = app
    (root / "sources" / "raw").mkdir(parents=True)
    (root / "sources" / "raw" / "scan-1.webm").write_bytes(b"x")
    assert post(port, {"file": "sources/raw/scan-1.webm"}) == 202
    assert started == ["sources/raw/scan-1.webm"]
    assert post(port, {"file": "sources/raw/scan-2.webm"}) == 404
    assert post(port, {"file": "../../etc/passwd"}) == 404
    assert post(port, {"file": "sources/raw/scan-1.webm"}, "text/plain") == 415
    assert post(port, {}) == 400
