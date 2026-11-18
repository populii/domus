"""The weatherboards' spacing and lap lines from a rectified photo of a wall: domus prep boards, on made-up walls."""

import json
import re
import shutil

import numpy as np
import pytest
from PIL import Image

from domus.errors import DomusError

SPACING, LAP = 0.142, 12.437        # the made-up boards: a lap line at 12.437 m, and one every 142 mm up and down from it
WINDOW = (1.6, 2.7, 13.0, 14.3)     # the made-up window: from u 1.6 to 2.7 m along the wall, 13.0 to 14.3 m high


def wall(spacing=SPACING, lap=LAP, z0=12.4, z1=15.2, width=4.0, ppm=100, seed=1):
    """A made-up rectified photo of a weatherboard wall, as domus prep house-textures writes one (rows down from z1,
    ppm pixels a metre): each board lighter towards its top, with a dark line 10 mm deep under each lap, the light
    changing slowly across and up the wall, noise, a window in a white frame cut out of the boards, and a corner the
    photo doesn't cover. Returns the photo in grey, where it covers the wall, and its record in faces.json."""
    rng = np.random.default_rng(seed)
    rows, cols = int(round((z1 - z0) * ppm)), int(round(width * ppm))
    fine = z1 - (np.arange(rows * 10) + 0.5) / (ppm * 10)          # ten heights a row, so a lap line can fall part way
    from_lap = np.mod(fine - lap + spacing / 2, spacing) - spacing / 2
    boards = 175 + 20 * np.mod(fine - lap, spacing) / spacing - 70 * (np.abs(from_lap) < 0.005)
    z, u = z1 - (np.arange(rows) + 0.5) / ppm, (np.arange(cols) + 0.5) / ppm
    img = boards.reshape(rows, 10).mean(axis=1)[:, None] + 15 * np.sin(u / width * np.pi)[None, :] + \
        12 * ((z - z0) / (z1 - z0))[:, None] + rng.normal(0, 8, (rows, cols))
    a0, a1, h0, h1 = WINDOW
    across, up = (u > a0) & (u < a1), (z > h0) & (z < h1)
    glass = np.outer((z > h0 + 0.05) & (z < h1 - 0.05), (u > a0 + 0.05) & (u < a1 - 0.05))
    img[np.outer(up, across)] = 230                                   # the frame
    img[glass] = 60 + rng.normal(0, 5, int(glass.sum()))
    covered = np.ones((rows, cols), bool)
    covered[np.outer(z < 12.8, u < 1.0)] = False                     # behind a made-up bush
    rec = {"file": "made-up-wall.jpg", "mask": "made-up-wall-mask.png", "u0": 0.0, "u1": width, "z0": z0, "z1": z1,
           "ppm": ppm, "source": "made up", "note": ""}
    return np.clip(img, 0, 255).round(), covered, rec


def off_lap(height, spacing=SPACING, lap=LAP):
    """How far a height is from the nearest made-up lap line."""
    return abs(np.mod(height - lap + spacing / 2, spacing) - spacing / 2)


def test_the_spacing_and_lap_lines_come_back_from_a_made_up_wall():
    from domus.prep import boards

    rows = boards.measure_photo(*wall())
    assert [r["u"] for r in rows] == [(k * 0.5, k * 0.5 + 0.5) for k in range(8)]
    found = [r for r in rows if "spacing" in r]
    assert len(found) == 8 and all(r["rows"] >= 240 for r in found)              # the bush's corner leaves 240 rows
    assert abs(np.median([r["spacing"] for r in found]) - SPACING) < 0.001
    clear = [r for r in found if r["u"][1] <= WINDOW[0] or r["u"][0] >= WINDOW[1]]
    assert len(clear) == 5
    for r in clear:
        assert abs(r["spacing"] - SPACING) < 0.001 and not r["edge"]
        assert off_lap(r["lap"]) < 0.004 and r["lap"] - r["z"][0] < SPACING       # the lowest lap line in the band
    assert [round(r["z"][0], 3) for r in found[:2]] == [12.805, 12.805]           # above the bush's corner


def test_one_band_between_heights_and_a_range_that_stops_at_the_boards():
    from domus.prep import boards

    grey, covered, rec = wall(spacing=0.105, lap=12.51, seed=2)
    (row,) = boards.measure_photo(grey, covered, rec, along=(2.9, 3.9), band=0, heights=(13.5, 15.0))
    assert row["u"] == (2.9, 3.9) and row["rows"] == 150 and 13.5 <= row["z"][0] < row["z"][1] <= 15.0
    assert abs(row["spacing"] - 0.105) < 0.001 and off_lap(row["lap"], 0.105, 12.51) < 0.004
    (row,) = boards.measure_photo(grey, covered, rec, along=(2.9, 3.9), band=0, spacing=(0.105, 0.2))
    assert row["edge"] and abs(row["spacing"] - 0.105) < 0.001                    # at the range's end: marked
    (row,) = boards.measure_photo(grey, covered, rec, along=(2.9, 3.9), band=0, heights=(13.5, 13.8))
    assert "spacing" not in row and row["rows"] == 30                             # too few rows to read
    with pytest.raises(DomusError, match="more than 0"):
        boards.measure_photo(grey, covered, rec, step=0)


def test_twice_the_boards_spacing_isnt_taken_for_it():
    from domus.prep import boards

    # Boards ten pixel rows apart: a comb twice that, also in the range tried, fits nearly as well, and in the band
    # across the window better; every band still finds theirs.
    rows = boards.measure_photo(*wall(spacing=0.1, lap=12.51))
    assert [round(r["spacing"], 4) for r in rows] == [0.1] * 8
    # A profile folded at twice the boards' spacing shows two of their lap lines a cycle; at theirs, one.
    z = np.arange(0, 3, 0.01) + 0.005
    lines = -60.0 * (np.abs(np.mod(z - 0.03 + 0.05, 0.1) - 0.05) < 0.006)
    assert boards.dips(z, lines, 0.2, 2, 12) and not boards.dips(z, lines, 0.1, 2, 10)


@pytest.fixture
def house_photo(site):
    """The made-up wall's photo, its mask and faces.json in the demo's data/house/, taken away after."""
    folder = site / "data" / "house"
    made = not folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    grey, covered, rec = wall()
    Image.fromarray(grey.astype(np.uint8)).save(folder / rec["file"], quality=85)
    Image.fromarray(covered.astype(np.uint8) * 255).save(folder / rec["mask"])
    (folder / "faces.json").write_text(json.dumps({"made-up-wall": rec}), encoding="utf-8")
    yield folder
    if made:
        shutil.rmtree(folder)
    else:
        for name in (rec["file"], rec["mask"], "faces.json"):
            (folder / name).unlink()


def run(project, *args):
    from domus.app import cli

    return cli.call(["prep", "boards", *args], project)


def test_domus_prep_boards_reads_the_sites_wall_photos(house_photo, capsys, project):
    assert run(project, "--above", "13.0") == 0
    out = capsys.readouterr().out
    assert out.startswith("made-up-wall: 8 bands from 0.00 to 4.00 m along it\n")
    spacing = float(re.search(r"made-up-wall: spacing ([\d.]+) mm, the median of 8 bands", out)[1])
    assert abs(spacing / 1000 - SPACING) < 0.001
    first = [float(h) for h in re.findall(r"the first at or above 13\.000: ([\d.]+)", out)]
    assert len(first) == 8 and sum(off_lap(h) < 0.004 for h in first) >= 5       # the window's bands may miss
    with pytest.raises(DomusError, match="no face 'south': it has made-up-wall"):
        run(project, "south")


def test_a_site_without_wall_photos_says_so(site, project):
    assert not (site / "data" / "house" / "faces.json").exists()
    with pytest.raises(DomusError, match="house-textures"):
        run(project)
