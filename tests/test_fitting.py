"""Fitting a site's parts to a placed video by hand: domus prep face-fit and frame-points."""

import json

import numpy as np
from scipy.spatial.transform import Rotation


def test_a_face_is_fitted_through_its_points_and_against_the_model():
    from domus.prep import fitting

    # A wall's face along s at o = 3 + 0.02 (s - 2), 1 to 2 m high, read to 5 mm, with points off it in front and behind.
    rng = np.random.default_rng(1)
    s = rng.uniform(2, 8, 3000)
    o = 3 + 0.02 * (s - 2) + rng.normal(0, 0.005, len(s))
    stray = rng.uniform(2, 8, 200)
    so = np.column_stack([np.r_[s, stray], np.r_[o, 3.4 + 0 * stray]])
    z = np.r_[rng.uniform(1, 2, len(s)), rng.uniform(1, 2, len(stray))]
    item = {"box": [2, 8, 2.5, 3.6, 0.5, 2.5], "along": "s", "model": [[2, 3.0], [8, 3.12]]}
    fit = fitting.face_fit(so, z, item)
    assert fit["on"] == 3000 and fit["points"] == 3200 and fit["spread"] < 0.006
    for (u, w), (_, model) in zip(fit["ends"], fit["model"], strict=True):
        assert abs(w - (3 + 0.02 * (u - 2))) < 0.002 and abs(w - model) < 0.002
    line = fitting.face_line("the wall", "s", fit)
    assert line.startswith("the wall: 3000 of 3200 points on the face") and abs(float(line.rsplit(": ", 1)[1])) < 0.002
    assert fitting.face_line("empty", "s", fitting.face_fit(so, z, {**item, "box": [20, 21, 0, 1, 0, 1]})) == \
        "empty: only 0 points"


def test_a_frames_pixels_meet_a_plane(site):
    from domus import design
    from domus.prep import fitting

    # One frame, its camera 40 m up at s 5, o 5, looking along the grid's north: a pixel half the focal length below
    # the middle looks down 1 in 2, and meets a level 1.5 m below the camera 3 m north of it.
    fr = design.frame()
    e, n = fr.en(np.array([5.0, 5.0]))
    looks = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])     # camera axes on the site, by column
    video = site / "data" / "video"
    video.mkdir(parents=True, exist_ok=True)
    np.savez(video / "made-up-sfm.npz", rvecs=Rotation.from_matrix(looks.T).as_rotvec()[None], tvecs=np.zeros((1, 3)),
             intrinsics=np.array([1000.0, 0.0, 500.0, 400.0]), frame_names=np.array(["t001.000"]))
    (video / "made-up-georef.json").write_text(json.dumps({"params": [0, 0, 0, 0, e, n, 40.0]}), encoding="utf-8")
    try:
        placed = fitting.Placed("made-up")
        c, what, points = placed.on_plane("t001.000", {"level": 38.5}, {"below": (500, 900), "level": (500, 400)}, fr)
        assert np.allclose(c, [e, n, 40.0]) and what == "level 38.5"
        p, behind = points["below"]
        assert np.allclose(p, [e, n + 3.0, 38.5]) and not behind
        assert placed.on_plane("t009.000", {"level": 38.5}, {}, fr) is None
    finally:
        (video / "made-up-sfm.npz").unlink()
        (video / "made-up-georef.json").unlink()
