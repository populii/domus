"""Structure-from-motion core: bundle adjustment on a synthetic scene."""

import numpy as np

from domus.capture import sfm


def synthetic(seed=0, ncam=8, npts=300, noise=0.3):
    rng = np.random.default_rng(seed)
    pts = rng.uniform([-3, -2, 6], [3, 2, 10], (npts, 3))
    frames = []
    for k in range(ncam):
        rvec = np.array([0.02 * k, -0.05 * k, 0.01])
        tvec = np.array([-0.4 * k, 0.05 * k, 0.0])
        uv, _ = sfm.project(pts, np.repeat(rvec[None], npts, 0), np.repeat(tvec[None], npts, 0),
                            1100.0, -0.02, 720.0, 540.0)
        frames.append(sfm.Frame(f"f{k}", uv + rng.normal(0, noise, uv.shape), None, None, rvec, tvec))
    rec = sfm.Reconstruction(frames, f=1100.0, k1=-0.02, cx=720.0, cy=540.0)
    rec.tracks = [{k: i for k in range(ncam)} for i in range(npts)]
    rec.points = {i: p for i, p in enumerate(pts)}
    return rec, pts


def test_bundle_adjust_recovers_perturbed_scene():
    rec, _ = synthetic()
    rng = np.random.default_rng(1)
    true_poses = [(f.rvec.copy(), f.tvec.copy()) for f in rec.frames]
    # A start about as far off as a freshly registered frame (several pixels).
    for f in rec.frames[1:]:
        f.rvec = f.rvec + rng.normal(0, 0.002, 3)
        f.tvec = f.tvec + rng.normal(0, 0.01, 3)
    for i in rec.points:
        rec.points[i] = rec.points[i] + rng.normal(0, 0.01, 3)
    rec.f, rec.k1 = 1080.0, 0.0
    # Two fixed cameras pin the gauge and the scale to the truth.
    rms = sfm.bundle_adjust(rec, fix_frames=[0, 7], refine_intrinsics=True, max_nfev=50)
    assert rms < 0.5          # the 0.3 px noise, as a 2D residual
    assert abs(rec.f - 1100.0) < 15.0
    for f, (_, tv) in zip(rec.frames, true_poses):
        assert np.linalg.norm(f.tvec - tv) < 0.03
