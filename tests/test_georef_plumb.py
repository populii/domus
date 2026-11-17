"""domus.video.georef_sfm: the frames' vertical, on synthetic cameras with a known up direction.

The scene is posts (vertical edges) and rails (horizontal ones) on a site whose vertical is +z.
The reconstruction's frame is the site turned by a known rotation, as a structure-from-motion
model is, so its up direction is known. Frames are drawn through the cameras with the radial
distortion the models carry; the tool must find that up direction from them, ignore frames whose
straight edges are not vertical (a drain grate's slots seen from above, strap-like leaves), and a
placement held by it must come out level where two anchors alone leave it turned about the line
through them. With the part toward or away from the camera weighed down, frames that all look
one way leave a bias there loose, and frames from two headings take most of it out.
"""

import math

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

F, K1, W, H = 1100.0, -0.02, 1440, 1080
CX, CY = W / 2, H / 2
TO_MODEL = Rotation.from_euler("xyz", [11.0, -7.0, 40.0], degrees=True)   # site -> reconstruction
UP = TO_MODEL.apply([0.0, 0.0, 1.0])                                         # the site's vertical there


def tool():
    from domus.capture import georef_sfm as mod

    return mod


def camera(centre, yaw, pitch, roll):
    """Site -> camera rotation (x right, y down, z ahead) of a camera at `centre` looking north,
    turned by yaw (right), pitch (up) and roll, in degrees."""
    base = np.array([[1.0, 0, 0], [0, 0, -1], [0, 1, 0]])
    turn = Rotation.from_euler("zxy", [roll, pitch, yaw], degrees=True).inv().as_matrix()
    return turn @ base, np.asarray(centre, float)


def draw(rot, centre, segments, img=None):
    """Segments (site coordinates) drawn 5 px wide through the camera, with radial distortion."""
    img = np.full((H, W), 60, np.uint8) if img is None else img
    for a, b in segments:
        p = (np.asarray(a) + np.linspace(0, 1, 60)[:, None] * (np.asarray(b) - np.asarray(a)) - centre) @ rot.T
        if (p[:, 2] < 0.2).any():
            continue
        x, y = p[:, 0] / p[:, 2], p[:, 1] / p[:, 2]
        rad = 1 + K1 * (x * x + y * y)
        uv = np.column_stack([F * rad * x + CX, F * rad * y + CY])
        cv2.polylines(img, [np.round(uv * 16).astype(np.int32)], False, 230, 5, cv2.LINE_AA, 4)
    return img


def scene():
    """Posts 2.4 m tall and rails between them, north of the cameras."""
    rng = np.random.default_rng(3)
    segs = []
    for x, y in zip(rng.uniform(-5, 5, 14), rng.uniform(1, 5, 14)):
        segs.append(((x, y, 0.0), (x, y, 2.4)))
    for z in (0.5, 1.2, 2.0):
        segs.append(((-5, 3.0, z), (5, 3.0, z)))
    return segs


def reconstruction(cams):
    """A model of the frames: names, camera poses in the reconstruction's frame, intrinsics."""
    names, rvecs, tvecs = [], [], []
    for k, (rot, centre) in enumerate(cams):
        r_model = rot @ TO_MODEL.inv().as_matrix()        # reconstruction -> camera
        names.append(f"t{k:07.3f}")
        rvecs.append(Rotation.from_matrix(r_model).as_rotvec())
        tvecs.append(-r_model @ TO_MODEL.apply(centre))
    return {"names": names, "rvecs": np.array(rvecs), "tvecs": np.array(tvecs), "intr": np.array([F, K1, CX, CY])}


def angle(a, b):
    return math.degrees(math.acos(np.clip(np.dot(a, b) / np.linalg.norm(a) / np.linalg.norm(b), -1, 1)))


def frames_and_images():
    rng = np.random.default_rng(0)
    segs = scene()
    cams, images = [], {}
    for k in range(8):                         # true verticals, the camera held at various angles
        cams.append(camera((rng.uniform(-1.5, 1.5), -6.0, 1.5), rng.uniform(-15, 15), rng.uniform(-12, 6),
                           rng.uniform(-6, 6)))
        images[f"t{k:07.3f}"] = draw(*cams[-1], segs)
    # a drain grate's slots seen from above: parallel lines on the ground that pass for verticals
    cams.append(camera((0.0, -1.0, 1.3), 0.0, -70.0, 2.0))
    slots = [((x, 0.0, 0.0), (x, 1.2, 0.0)) for x in np.arange(-0.4, 0.41, 0.06)]
    images["t008.000"] = draw(*cams[-1], slots)
    # strap-like leaves: long strokes leaning every which way
    cams.append(camera((0.5, -6.0, 1.5), 5.0, -5.0, 0.0))
    img = np.full((H, W), 60, np.uint8)
    for _ in range(60):
        x0, y0 = rng.uniform(100, W - 100), rng.uniform(200, H - 50)
        a = math.radians(rng.uniform(-25, 25))
        n = rng.uniform(80, 220)
        cv2.line(img, (int(x0), int(y0)), (int(x0 + n * math.sin(a)), int(y0 - n * math.cos(a))), 230, 4, cv2.LINE_AA)
    images["t009.000"] = img
    return reconstruction(cams), images


def test_frames_vertical_recovers_the_known_up():
    g = tool()
    rec, images = frames_and_images()
    frames = g.plumb_frames(rec, "synthetic", "auto")
    ups = g.frame_ups(rec, frames, images.get)
    names = [n for n, _ in ups]
    true = {f"t{k:07.3f}" for k in range(8)}
    assert true <= set(names)
    for n, u in ups:
        if n in true:
            assert angle(u, UP) < 0.4, n          # each true frame on its own
    m, keep, _ = g.consensus_up([u for _, u in ups])
    assert angle(m, UP) < 0.15
    kept = {n for n, k in zip(names, keep) if k}
    assert true <= kept
    assert not kept & {"t008.000", "t009.000"}    # the grate and the leaves, if they gave a vertical at all
    if "t008.000" in names:                       # the slots point along the ground, 90 degrees off
        assert angle(dict(ups)["t008.000"], UP) > 60


def test_consensus_leans_on_what_each_frame_fixes():
    """Each frame's vertical is good sideways and biased 2 degrees toward the camera (as a lens
    model's error would make it): from one heading the bias stays, but only in the direction the
    frames cannot fix, and the hold there is loose; from two headings most of it goes."""
    g = tool()
    rng = np.random.default_rng(5)

    def frames(headings):
        ups, axes = [], []
        for h in headings:
            rot, _ = camera((0, 0, 1.5), h + rng.uniform(-4, 4), rng.uniform(-8, 4), rng.uniform(-4, 4))
            r_model = rot @ TO_MODEL.inv().as_matrix()           # reconstruction -> camera
            x, f = r_model[0], r_model[2]
            xs, fs = x - (x @ UP) * UP, f - (f @ UP) * UP
            u = UP + math.tan(math.radians(2.0)) * fs / np.linalg.norm(fs) \
                + math.tan(math.radians(rng.normal(0, 0.1))) * xs / np.linalg.norm(xs)
            ups.append(u / np.linalg.norm(u))
            axes.append((x, f))
        return ups, axes

    ups, axes = frames([10.0] * 20)
    c = g.plumb_consensus(ups, axes, 0.1)
    view = TO_MODEL.apply(camera((0, 0, 0), 10.0, 0, 0)[0][2])      # the common viewing direction
    view -= (view @ UP) * UP
    view /= np.linalg.norm(view)
    side = np.cross(UP, view)
    assert abs(math.degrees((c["up"] - UP) @ side)) < 0.1             # sideways: right
    ev, vec = np.linalg.eigh(c["info"])
    assert abs(vec[:, 1] @ view) > 0.95 and ev[2] / ev[1] > 5         # the least-fixed: toward/away
    true = np.r_[np.log(0.4), TO_MODEL.inv().as_rotvec(), 0, 0, 0]  # a placement level by the truth
    held = g.plumb_lean(true, c["up"], c["info"])
    plain = g.plumb_lean(true, c["up"])
    assert np.linalg.norm(held) < 0.5 * np.linalg.norm(plain)        # the bias is held loosely

    ups, axes = frames([10.0] * 10 + [100.0] * 10)
    c = g.plumb_consensus(ups, axes, 0.1)
    assert angle(c["up"], UP) < 0.3                                   # a tenth of the bias is left
    iso, _, _ = g.consensus_up(ups)
    assert angle(iso, UP) > 1.0                                       # a plain mean keeps it all


def test_roi_leaves_out_edges_outside_the_box():
    g = tool()
    rot, centre = camera((0.0, -6.0, 1.5), 4.0, 8.0, 3.0)
    rec = reconstruction([(rot, centre)])
    img = draw(rot, centre, [s for s in scene() if s[0][2] == 0.0 and s[1][2] == 2.4])
    # leaves in the bottom third all leaning the same way: a consistent false vertical
    for x in range(120, W - 120, 45):
        cv2.line(img, (x, H - 40), (x + 70, H - 330), 230, 4, cv2.LINE_AA)
    roi = [0, 0, W, H - 380]
    up = g.frame_ups(rec, [("t000.000", roi)], {"t000.000": img}.get)
    assert len(up) == 1 and angle(up[0][1], UP) < 0.4


def test_plumb_frames_names_ranges_and_boxes():
    g = tool()
    rec = {"names": ["t000.100", "t000.300", "t001.000", "x/t000.200"],
           "rvecs": np.array([[0.0, 0, 0], [np.nan] * 3, [0, 0, 0], [0, 0, 0]])}
    assert g.plumb_frames(rec, "c-m2", "auto") == [("t000.100", None), ("t001.000", None), ("x/t000.200", None)]
    spec = ["t001.000", {"frame": "t000.300"}, {"from": 0.0, "to": 0.5, "roi": [1, 2, 3, 4]},
            {"from": 0, "to": 1, "clip": "x"}]
    assert g.plumb_frames(rec, "c-m2", spec) == [("t001.000", None), ("t000.100", [1, 2, 3, 4]),
                                                ("x/t000.200", None)]


def test_held_plumb_levels_a_placement_two_anchors_leave_turned():
    g = tool()
    # The true placement: reconstruction -> site, scale 0.4.
    true = np.r_[math.log(0.4), TO_MODEL.inv().as_rotvec(), 1570000.0, 5180000.0, 40.0]
    a = np.array([[0.3, -1.0, 2.0], [4.0, 1.5, -0.5]])          # two anchors in the reconstruction
    site = g.apply(true, a)
    assert g.tilt_of(true, UP)[0] < 1e-6
    # A start turned 15 degrees about the line through the anchors fits them just as well.
    axis = (site[1] - site[0]) / np.linalg.norm(site[1] - site[0])
    turn = Rotation.from_rotvec(axis * math.radians(15.0))
    start = np.r_[true[0], (turn * Rotation.from_rotvec(true[1:4])).as_rotvec(), np.zeros(3)]
    start[4:7] = site[0] - g.apply(start, a[:1])[0]
    assert np.abs(g.apply(start, a) - site).max() < 1e-6
    assert g.tilt_of(start, UP)[0] > 10.0

    def resid(p, plumb):
        r = ((g.apply(p, a) - site) / 0.03).ravel()
        return np.r_[r, g.plumb_lean(p, UP) / math.radians(0.5)] if plumb else r

    alone = least_squares(resid, start, args=(False,)).x
    held = least_squares(resid, start, args=(True,)).x
    assert abs(g.tilt_of(alone, UP)[0] - g.tilt_of(start, UP)[0]) < 0.01   # the anchors cannot see it
    assert g.tilt_of(held, UP)[0] < 0.01             # the plumb takes it out
    assert np.abs(g.apply(held, a) - site).max() < 1e-3
    # level() takes it out directly, keeping the pivot where it was
    lev = g.level(start, UP, a[0])
    assert g.tilt_of(lev, UP)[0] < 1e-6
    assert np.abs(g.apply(lev, a[:1]) - g.apply(start, a[:1])).max() < 1e-9
