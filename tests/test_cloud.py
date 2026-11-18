"""A placed cloud against the ground, and seen from above: domus prep cloud and cloud-plan."""

import numpy as np
import pytest
from PIL import Image

from domus.errors import DomusError


def _cloud(project, name, lift=0.05, n=4000, seed=0):
    """A cloud of `n` red points `lift` metres over the model's ground, over s and o from 2 to 6 m in the demo."""
    from domus import design, terrain

    rng = np.random.default_rng(seed)
    en = design.frame(project).en(np.column_stack([rng.uniform(2, 6, n), rng.uniform(2, 6, n)]))
    z = terrain.ground(project).sample(en[:, 0], en[:, 1]) + lift
    path = project.path / "data" / "video" / f"{name}.npz"
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, xyz=np.column_stack([en, z]), rgb=np.tile([[200, 30, 30]], (n, 1)).astype(np.uint8),
             nview=np.r_[np.full(n - 10, 3), np.full(10, 2)])
    return path


def test_a_cloud_over_the_ground_reads_as_high_as_it_lies(site, project):
    from domus.prep import cloud

    path = _cloud(project, "made-up-site")
    try:
        xyz, rgb = cloud.read_clouds(project, ["made-up-site"])
        assert len(xyz) == 3990 and rgb.shape == (3990, 3)          # those three cameras or more saw
        assert len(cloud.read_clouds(project, ["made-up-site"], all_points=True)[0]) == 4000
        rows = cloud.compare(project, xyz, [[6, 6], [2, 2]], cell=1.0, pct=50, nmin=12)
        assert len(rows) == 16 and all(abs(d - 0.05) < 0.002 and n > 100 for _, _, n, d, _ in rows)
        assert {(u, v) for u, v, *_ in rows} == {(s + 0.5, o + 0.5) for s in range(2, 6) for o in range(2, 6)}
        assert cloud.compare(project, xyz, [[2, 2], [6, 6]], cell=1.0, nmin=5000) == []
    finally:
        path.unlink()


def test_a_cloud_is_drawn_from_above(site, tmp_path, project):
    from domus.prep import cloud

    path = _cloud(project, "made-up-plan")
    try:
        xyz, rgb = cloud.read_clouds(project, [str(path)])
        size, n = cloud.plan(project, xyz, rgb, [[1, 1], [7, 7]], tmp_path / "plan.png", px=0.05)
        img = np.asarray(Image.open(tmp_path / "plan.png"))
        assert size == (120, 120) and n == 3990 and (img[..., 0] == 200).sum() > 1000
        size, n = cloud.plan(project, xyz, rgb, [[1, 1], [7, 7]], tmp_path / "high.png", px=0.05, above=0.02)
        assert n == 0                                                   # every point is 5 cm over the ground
    finally:
        path.unlink()


def test_a_cloud_that_isnt_there_says_so(site, project):
    from domus.prep import cloud

    with pytest.raises(DomusError, match="no cloud"):
        cloud.read_clouds(project, ["nothing-here"])
    with pytest.raises(DomusError, match="two corners"):
        cloud.box_of([[1, 1]])
