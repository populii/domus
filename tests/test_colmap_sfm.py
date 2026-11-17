"""domus.video.colmap_sfm: a COLMAP model carried into our conventions, on a synthetic camera.

COLMAP puts the centre of the top-left pixel at (0.5, 0.5) and ours at (0, 0); its
SIMPLE_RADIAL camera must be the model domus.sfm.project uses. No COLMAP run is needed:
the scene, poses and pixels are made up, and pycolmap only supplies its camera model and
its Reconstruction container.
"""


import numpy as np
import pytest

from domus import sfm

pycolmap = pytest.importorskip("pycolmap")

F, K, W, H = 640.0, 0.03, 1440, 1080          # a wide-lens frame, with strong distortion


def tool():
    from domus.capture import colmap_sfm as mod

    return mod


def scene(noise=0.0, seed=0):
    """A pycolmap.Reconstruction of 4 frames and 200 points, its keypoints projected by
    COLMAP's own camera model (plus noise), and the true pixels in our convention."""
    rng = np.random.default_rng(seed)
    pts = rng.uniform([-4, -3, 5], [4, 3, 12], (200, 3))
    cam = pycolmap.Camera.create_from_model_name(1, "SIMPLE_RADIAL", F, W, H)
    cam.params = [F, W / 2, H / 2, K]                 # COLMAP's image centre: (720, 540)
    rec = pycolmap.Reconstruction()
    rec.add_camera_with_trivial_rig(cam)
    poses, truth = [], []
    for k in range(4):
        rvec = np.array([0.03 * k, -0.08 * k, 0.02])
        tvec = np.array([-0.5 * k, 0.1, 0.2 * k])
        # Our convention: the image centre is at ((W - 1) / 2, (H - 1) / 2).
        uv, _ = sfm.project(pts, np.repeat(rvec[None], len(pts), 0), np.repeat(tvec[None], len(pts), 0),
                            F, K, (W - 1) / 2, (H - 1) / 2)
        cam_from_world = pycolmap.Rigid3d(pycolmap.Rotation3d(rvec), tvec)
        colmap_uv = cam.img_from_cam(cam_from_world * pts)
        assert np.abs(colmap_uv - (uv + 0.5)).max() < 1e-9   # same model, half a pixel apart
        keypoints = colmap_uv + rng.normal(0, noise, colmap_uv.shape)
        rec.add_image_with_trivial_frame(
            pycolmap.Image(name=f"clip/t{k:07.3f}.jpg", keypoints=keypoints, camera_id=1, image_id=k + 1),
            cam_from_world)
        poses.append((rvec, tvec))
        truth.append(uv)
    for i, x in enumerate(pts):
        track = pycolmap.Track()
        for k in range(4):
            track.add_element(k + 1, i)
        rec.add_point3D(x, track, np.array([10, 20, 30], np.uint8))
    return rec, poses, np.array(truth)


def test_conversion_keeps_camera_poses_and_pixels(tmp_path):
    cs = tool()
    rec, poses, truth = scene()
    images = [rec.image(i).name for i in sorted(rec.images.keys())]
    names = [n.split("/")[1].removesuffix(".jpg") for n in images]
    ours, duplicates = cs.to_ours(rec, names, {n: k for k, n in enumerate(images)})
    assert duplicates == 0
    assert np.allclose([ours.f, ours.k1, ours.cx, ours.cy], [F, K, (W - 1) / 2, (H - 1) / 2])
    for k, (fr, (rvec, tvec)) in enumerate(zip(ours.frames, poses)):
        assert fr.name == f"t{k:07.3f}"
        assert np.allclose(fr.rvec, rvec) and np.allclose(fr.tvec, tvec)
        # Keypoints are back on the true pixels in our convention, in point order.
        assert np.abs(fr.keypoints - truth[k]).max() < 1e-9
    assert cs.median_reproj(ours) < 1e-9
    sfm.save(ours, tmp_path, "synthetic", 0.0)
    d = np.load(tmp_path / "synthetic-sfm.npz")
    assert np.allclose(d["intrinsics"], [F, K, (W - 1) / 2, (H - 1) / 2])
    assert d["obs"].shape == (4 * 200, 4) and (d["rgb"] == [10, 20, 30]).all()
    assert cs.saved_median_reproj(tmp_path / "synthetic-sfm.npz") < 1e-9


def test_reprojection_error_agrees_with_colmap(tmp_path):
    """With noisy keypoints, sfm.project on the converted model and COLMAP's own camera model
    give the same median error (the check the tool runs on every model it writes)."""
    cs = tool()
    rec, _, _ = scene(noise=0.7, seed=1)
    images = [rec.image(i).name for i in sorted(rec.images.keys())]
    ours, _ = cs.to_ours(rec, images, {n: k for k, n in enumerate(images)})
    own = cs.colmap_median_reproj(rec)
    assert 0.5 < own < 1.5
    assert abs(cs.median_reproj(ours) - own) < 1e-9
    sfm.save(ours, tmp_path, "noisy", own)
    assert abs(cs.saved_median_reproj(tmp_path / "noisy-sfm.npz") - own) < 1e-9
