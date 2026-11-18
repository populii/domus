"""The model's parts drawn over a placed frame: domus prep overlay."""

import json

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation
from shapely.geometry import Polygon


def test_the_models_parts_are_drawn_where_a_camera_sees_them(site, project):
    from domus import geojson, terrain
    from domus.prep import fitting, overlay

    # A camera 30 m over the middle of the demo's lot looking straight down, image x east and y south: the lot, the
    # house and its walls fall inside a 1000 by 800 frame.
    lot = Polygon(geojson.read(site / "data" / "site" / "boundary.geojson")["features"][0]["geometry"]["coordinates"][0])
    e, n = lot.centroid.x, lot.centroid.y
    h = float(terrain.ground(project).sample(np.array([e]), np.array([n]))[0]) + 30.0
    axes = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, -1.0]])        # the camera's axes on the site, by column
    video = site / "data" / "video"
    video.mkdir(parents=True, exist_ok=True)
    np.savez(video / "made-up-over-sfm.npz", rvecs=Rotation.from_matrix(axes.T).as_rotvec()[None], tvecs=np.zeros((1, 3)),
             intrinsics=np.array([500.0, 0.0, 500.0, 400.0]), frame_names=np.array(["t001.000"]))
    (video / "made-up-over-georef.json").write_text(json.dumps({"params": [0, 0, 0, 0, e, n, h]}), encoding="utf-8")
    try:
        placed = fitting.Placed(project, "made-up-over")
        px, depth = placed.project(np.array([[e, n, h - 30.0], [e + 6.0, n, h - 30.0]]), 0)
        assert np.allclose(px, [[500, 400], [600, 400]]) and np.allclose(depth, 30.0)     # 6 m east: 100 px right
        img = Image.new("RGB", (1000, 800))
        _, camera = placed.camera("t001.000")
        overlay.Scene(project).draw(overlay.Canvas(img, placed, 0), camera, set(overlay.GROUPS), see_through=True)
        a = np.asarray(img)
        assert ((a == (255, 255, 255)).all(-1)).sum() > 500                                # the house's walls
        assert ((a == (255, 40, 40)).all(-1)).sum() > 500                                   # the boundary
        assert ((a == overlay.WALL_COLOURS[0]).all(-1)).sum() > 100                         # the existing wall
    finally:
        (video / "made-up-over-sfm.npz").unlink()
        (video / "made-up-over-georef.json").unlink()
