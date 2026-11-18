"""domus video place, on a made-up walk round the demo's house.

The walk is a reconstruction in a frame of its own: the site's grid turned, shrunk and moved by a known similarity, as
a structure-from-motion model is. Its points lie on the house's walls, below the roof plane at the wall, and on the
ground round it, and its cameras are at a hand's height, looking at the house. Three of the house's corners, marked in
the frames, hold the placement. The demo's [house] has no roof_at_wall_above_fl, so the eaves' height comes from
roof_plane_at_wall; a site that gives roof_at_wall_above_fl has its own.
"""

import json
import sys

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

F, W, H = 1100.0, 1920, 1080
TURN = Rotation.from_euler("xyz", [9.0, -6.0, 35.0], degrees=True)    # the reconstruction's frame -> the site's
SCALE = 0.4                                                            # metres to a unit of the reconstruction


def made_up_walk(site, tag):
    """Writes the walk's reconstruction and its control into the site's data/video."""
    from domus import demo
    from domus.project import rasters

    house = site.settings()["house"]
    fl, plane = house["fl_upper"], house["roof_plane_at_wall"]
    dem = rasters.dem(site)
    ground = lambda e, n: dem.sample(np.atleast_1d(np.asarray(e, float)), np.atleast_1d(np.asarray(n, float)))
    ring = np.array(demo.HOUSE, float)
    centre = np.r_[ring.mean(axis=0), fl]
    pts = []
    for a, b in zip(ring, np.roll(ring, -1, axis=0)):         # the walls, from the ground to just under the roof
        u = (b - a) / np.linalg.norm(b - a)
        for d in np.arange(0.4, np.linalg.norm(b - a) - 0.3, 0.5):
            e, n = a + d * u
            pts += [[e, n, z] for z in np.arange(float(ground(e, n)[0]) + 0.15, plane - 0.2, 0.35)]
    E, N = np.meshgrid(np.arange(demo.X0 + 1, demo.X0 + 21, 0.8), np.arange(demo.Y0 + 2, demo.Y0 + 19, 0.8))
    off = ((E < ring[:, 0].min() - 0.6) | (E > ring[:, 0].max() + 0.6) | (N < ring[:, 1].min() - 0.6)
           | (N > ring[:, 1].max() + 0.6))
    pts += np.column_stack([E[off], N[off], ground(E[off], N[off])]).tolist()      # the ground round the house
    local = lambda p: TURN.inv().apply(np.asarray(p, float) - centre) / SCALE
    names, rvecs, tvecs = [], [], []
    for k, a in enumerate(np.radians(np.arange(0, 360, 22.5))):    # cameras round the house, 1.5 m up, looking at it
        e, n = centre[0] + 10.0 * np.cos(a), centre[1] + 8.5 * np.sin(a)
        c = np.array([e, n, float(ground(e, n)[0]) + 1.5])
        z = centre + [0.0, 0.0, 1.0] - c
        z /= np.linalg.norm(z)
        x = np.cross(z, [0.0, 0.0, 1.0])
        x /= np.linalg.norm(x)
        r = np.vstack([x, np.cross(z, x), z]) @ TURN.as_matrix()      # the reconstruction's frame -> the camera's
        names.append(f"t{k:07.3f}")
        rvecs.append(Rotation.from_matrix(r).as_rotvec())
        tvecs.append(-r @ local(c))
    rvecs, tvecs = np.array(rvecs), np.array(tvecs)

    def seen(p):
        """Where a point on the site falls in the frames that see it."""
        out = []
        for name, rv, tv in zip(names, rvecs, tvecs):
            q = Rotation.from_rotvec(rv).apply(local(p)) + tv
            px = [F * q[0] / q[2] + W / 2, F * q[1] / q[2] + H / 2]
            if q[2] > 0.5 and 0 <= px[0] < W and 0 <= px[1] < H:
                out.append({"frame": name, "px": [round(v, 3) for v in px]})
        return out

    corners = [[*ring[k], fl] for k in range(4)]
    control = {"clip": tag, "anchors": [{"name": f"corner-{k + 1}", "site": corners[k], "obs": seen(corners[k])}
                                        for k in range(3)],
               "points": [{"name": "corner-4", "site": corners[3], "obs": seen(corners[3]), "check": True}]}
    video = site.path / "data" / "video"
    video.mkdir(parents=True, exist_ok=True)
    np.savez(video / f"{tag}-sfm.npz", xyz=local(pts), rgb=np.zeros((len(pts), 3), np.uint8),
             nview=np.full(len(pts), 4), frame_names=np.array(names), rvecs=rvecs, tvecs=tvecs,
             intrinsics=np.array([F, 0.0, W / 2, H / 2]))
    (video / f"{tag}-control.json").write_text(json.dumps(control), encoding="utf-8")
    return video


@pytest.mark.parametrize("above", [None, 2.6])
def test_placing_takes_the_eaves_from_the_roof_unless_the_site_gives_them(tmp_path, at_site, monkeypatch, above):
    from domus import demo
    from domus.capture import georef_sfm

    demo.main([str(tmp_path / "demo")])
    if above is not None:          # [house] is the last table in the demo's site.toml
        with open(tmp_path / "demo" / "data" / "site.toml", "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"roof_at_wall_above_fl = {above}\n")
    site = at_site(tmp_path / "demo")
    house = site.settings()["house"]
    video = made_up_walk(site, "made-up-walk")
    eaves, classify = [], georef_sfm.classify

    def watch(site_pts, planes, dem_z, at, tol):
        eaves.append(at)
        return classify(site_pts, planes, dem_z, at, tol)

    monkeypatch.setattr(georef_sfm, "classify", watch)
    monkeypatch.setattr(sys, "argv", ["domus video place", "made-up-walk"])
    georef_sfm.main(site)
    want = house["roof_plane_at_wall"] if above is None else house["fl_upper"] + above
    assert len(eaves) == 4 and eaves == [pytest.approx(want)] * 4
    got = json.loads((video / "made-up-walk-georef.json").read_text(encoding="utf-8"))
    assert got["scale"] == pytest.approx(SCALE, rel=1e-3)
    assert max(abs(v) for r in got["control_residuals_m"].values() for v in r) < 0.01     # the check corner too
    assert got["wall_points"] > 300 and got["wall_median_abs_m"] < 0.005
    assert got["ground_median_abs_m"] < 0.005 and got["camera_height_median_m"] == pytest.approx(1.5, abs=0.01)
    assert (video / "made-up-walk-site.npz").exists()
