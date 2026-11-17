"""Phone LiDAR scans: each export format read, and placed on the grid. Files are written here to the
formats' published layouts; none comes from a real phone."""

import struct

import numpy as np
import pytest

PTS = np.array([[0.0, 0.0, 0.0], [1.5, 0.25, -0.5], [2.0, 1.0, 0.75], [-1.0, 2.0, 0.5]])
RGB = np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255], [10, 20, 30]], np.uint8)


def ply_binary(path, order="<", fmt="binary_little_endian"):
    head = (f"ply\nformat {fmt} 1.0\ncomment made by a test\nelement vertex {len(PTS)}\nproperty float x\nproperty float y\n"
            "property float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
    body = b"".join(struct.pack(order + "fff3B", *p, *c) for p, c in zip(PTS, RGB))
    path.write_bytes(head.encode() + body)


def las(path, fmt=2):
    length = {2: 26, 3: 34, 7: 36}[fmt]
    rgb_at = {2: 20, 3: 28, 7: 30}[fmt]
    head = bytearray(227)
    head[:4] = b"LASF"
    head[24:26] = bytes([1, 2])
    struct.pack_into("<H", head, 94, 227)
    struct.pack_into("<I", head, 96, 227)
    head[104] = fmt
    struct.pack_into("<H", head, 105, length)
    struct.pack_into("<I", head, 107, len(PTS))
    struct.pack_into("<3d", head, 131, 0.001, 0.001, 0.001)
    struct.pack_into("<3d", head, 155, 100.0, 200.0, 300.0)
    body = bytearray()
    for p, c in zip(PTS, RGB):
        rec = bytearray(length)
        struct.pack_into("<3i", rec, 0, *np.round((p - [100.0, 200.0, 300.0]) / 0.001).astype(int).tolist())
        struct.pack_into("<3H", rec, rgb_at, *(int(v) * 257 for v in c))
        body += rec
    path.write_bytes(bytes(head) + bytes(body))


@pytest.mark.parametrize(("name", "writer"), [
    ("a.ply", ply_binary),
    ("b.ply", lambda p: ply_binary(p, ">", "binary_big_endian")),
    ("c.las", las),
    ("d.las", lambda p: las(p, 3)),
    ("e.las", lambda p: las(p, 7)),
])
def test_binary_formats(site, tmp_path, name, writer):
    from domus import scan

    writer(tmp_path / name)
    xyz, rgb = scan.read_cloud(tmp_path / name)
    assert xyz == pytest.approx(PTS, abs=1e-3)
    assert np.abs(rgb.astype(int) - RGB).max() <= 1


@pytest.mark.parametrize(("name", "text"), [
    ("a.ply", ("ply\nformat ascii 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\n"
              "property uchar red\nproperty uchar green\nproperty uchar blue\nelement face 0\n"
              "property list uchar int vertex_indices\nend_header\n{rows}\n")),
    ("b.xyz", "{rows}\n"),
    ("c.csv", "x,y,z,r,g,b\n{commas}\n"),
])
def test_text_formats(site, tmp_path, name, text):
    from domus import scan

    rows = [f"{a} {b} {c} {r} {g} {bl}" for (a, b, c), (r, g, bl) in zip(PTS, RGB)]
    f = tmp_path / name
    f.write_text(text.format(rows="\n".join(rows), commas="\n".join(x.replace(" ", ",") for x in rows)))
    xyz, rgb = scan.read_cloud(f)
    assert xyz == pytest.approx(PTS)
    assert np.array_equal(rgb, RGB)


def test_obj_vertices(site, tmp_path):
    from domus import scan

    f = tmp_path / "m.obj"
    f.write_text("# mesh\nv 0 0 0\nv 1 2 3\nvn 0 0 1\nf 1 2 2\n")
    xyz, rgb = scan.read_cloud(f)
    assert xyz.tolist() == [[0, 0, 0], [1, 2, 3]]
    assert rgb.shape == (2, 3)


@pytest.mark.parametrize("name", ["scan.laz", "scan.usdz", "scan.e57"])
def test_formats_it_cannot_read_say_what_to_do(site, tmp_path, name):
    from domus import scan

    (tmp_path / name).write_bytes(b"x")
    with pytest.raises(SystemExit, match="PLY"):
        scan.read_cloud(tmp_path / name)


def test_compressed_las_is_refused(site, tmp_path):
    from domus import scan

    las(tmp_path / "x.las")
    raw = bytearray((tmp_path / "x.las").read_bytes())
    raw[104] |= 0x80
    (tmp_path / "x.las").write_bytes(raw)
    with pytest.raises(SystemExit, match="LAZ"):
        scan.read_cloud(tmp_path / "x.las")


def turned(th, t, src):
    c, s = np.cos(th), np.sin(th)
    return src @ np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]).T + t


def test_place_about_vertical_from_a_y_up_scan(site):
    from domus import scan

    z_up = np.array([[0.0, 0, 0], [3, 0, 0.5], [3, 4, 1], [0, 4, -1], [1, 1, 2]])
    y_up = np.column_stack([z_up[:, 0], z_up[:, 2], -z_up[:, 1]])    # ARKit: y up, z towards the viewer
    t = np.array([1500000.0, 5500000.0, 40.0])
    truth = turned(0.7, t, z_up)
    site_pts, res = scan.place(y_up, (y_up[:2], truth[:2]), "y")
    assert res.max() < 1e-6
    assert site_pts == pytest.approx(truth)


def test_place_in_3d_needs_points_off_a_line(site):
    from domus import scan

    src = np.array([[0.0, 0, 0], [3, 0, 0], [3, 4, 0], [0, 4, 1]])
    dst = turned(-1.1, np.array([10.0, 20, 30]), src)
    site_pts, res = scan.place(src, (src, dst))
    assert res.max() < 1e-6 and site_pts == pytest.approx(dst)
    line = np.array([[0.0, 0, 0], [1, 0, 0], [2, 0, 0]])
    with pytest.raises(SystemExit, match="line"):
        scan.place(line, (line, line))
    with pytest.raises(SystemExit, match="three"):
        scan.place(src, (src[:2], dst[:2]))


def test_a_scan_reaches_the_build(built, site, tmp_path):
    """Through the command, the export and the build, with no [video] section in the site."""
    import json

    from domus import build, cli
    from domus.video import export_cloud

    ext = json.loads((built / "model.json").read_text(encoding="utf-8"))["extent"]
    e, n = (ext[0] + ext[2]) / 2, (ext[1] + ext[3]) / 2
    grid = np.array([[e, n, 42.0], [e + 2, n, 42.5], [e + 2, n + 3, 43.0], [e, n + 3, 42.0]])
    local = grid - grid[0]
    f = tmp_path / "scan.xyz"
    f.write_text("\n".join(" ".join(map(str, p)) for p in local))
    c = tmp_path / "control.csv"
    c.write_text("x,y,z,E,N,H\n" + "\n".join(",".join(map(str, [*a, *b])) for a, b in zip(local, grid)))
    cli.main(["scan", str(f), "--tag", "walk", "--control", str(c), "--up", "z"])
    export_cloud.main()
    build.main()
    layers = json.loads((built / "model.json").read_text(encoding="utf-8"))["layers"]
    assert next(x for x in layers if x["id"] == "video-points")["count"] == 4
    (site / "data" / "video" / "points.npz").unlink()
    (site / "data" / "video" / "walk-scan.npz").unlink()
