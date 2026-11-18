"""Picking helpers: contact sheets of a clip's frames, a piece of a photo at full size, and editing a picks file."""

import json

import cv2
import numpy as np
import pytest
from PIL import Image

from domus.errors import DomusError


def test_a_sheet_takes_a_frame_every_step_and_names_it(tmp_path):
    from domus.capture import sheet

    names = [f"t{t:07.3f}" for t in np.arange(0.0, 10.0, 0.5)]
    assert sheet.pick(names, 2.0, 6.0, 1.0) == ["t002.000", "t003.000", "t004.000", "t005.000", "t006.000"]
    assert sheet.pick(names, 2.0, 6.0, 1.0, registered={"t002.500", "t004.000"}) == ["t002.500", "t004.000"]
    for n in names[:8]:
        cv2.imwrite(str(tmp_path / f"{n}.jpg"), np.full((60, 80, 3), 120, np.uint8))
    written = sheet.sheets(tmp_path, str(tmp_path / "sheet"), names[:8], cols=3, width=40, rows=2)
    assert [(w, h, a, b) for _, w, h, a, b in written] == [(120, 60, "t000.000", "t002.500"), (120, 30, "t003.000", "t003.500")]
    assert cv2.imread(written[1][0]).shape == (30, 120, 3)


def test_a_piece_of_a_photo_comes_at_full_size(tmp_path):
    from domus.capture import crop

    img = np.zeros((300, 400, 3), np.uint8)
    img[100:150, 200:260] = (255, 0, 0)
    Image.fromarray(img).save(tmp_path / "photo.png")
    size, full = crop.crop(tmp_path / "photo.png", tmp_path / "piece.jpg", 200, (100, 50, 130, 75), zoom=2.0)
    assert full == (200, 100, 260, 150) and size == (120, 100)
    piece = np.asarray(Image.open(tmp_path / "piece.jpg"))
    assert piece.shape == (100, 120, 3) and piece[50, 60, 0] > 200 and piece[50, 60, 2] < 60


def test_marks_are_set_and_dropped_with_a_backup(tmp_path):
    from domus.capture import picks_edit

    path = tmp_path / "clip.json"
    raw = json.dumps({"features": [{"id": "post", "desc": "a post", "obs": [{"frame": "t002.000", "px": [1.0, 2.0]},
                                                                             {"frame": "t001.000", "px": [3.0, 4.0]}]}]}, indent=1) + "\n"
    path.write_text(raw, encoding="utf-8")
    said = []
    data = picks_edit.apply(json.loads(raw), {"post": {"set": {"t000.500": [5, 6]}, "drop": ["t002.000"]},
                                              "tap": {"desc": "the tap", "set": {"t003.000": [7, 8]}}}, said.append)
    assert [o["frame"] for o in data["features"][0]["obs"]] == ["t000.500", "t001.000"]
    assert data["features"][1] == {"id": "tap", "desc": "the tap", "obs": [{"frame": "t003.000", "px": [7.0, 8.0]}]}
    assert said == ["   post t000.500: None -> [5.0, 6.0]", "   tap t003.000: None -> [7.0, 8.0]"]
    backup = picks_edit.write(path, data, raw)
    assert backup.name == "clip.json.bak-1" and backup.read_text(encoding="utf-8") == raw
    assert json.loads(path.read_text(encoding="utf-8")) == data and not (tmp_path / "clip.json.tmp").exists()
    assert picks_edit.write(path, data, path.read_text(encoding="utf-8")).name == "clip.json.bak-2"
    with pytest.raises(DomusError, match="no mark on t009.000"):
        picks_edit.apply(data, {"post": {"drop": ["t009.000"]}})
    with pytest.raises(DomusError, match="needs a desc"):
        picks_edit.apply(data, {"new": {"set": {"t001.000": [1, 1]}}})


def test_two_lens_mapping_needs_the_reconstructions_workspace(site, project):
    import argparse

    from domus.capture import two_lens

    args = argparse.Namespace(clip="walk", work_tag="walk-colmap", t_main=10.0, t_wide=12.0, f_main=1100.0, f_wide=630.0,
                              stage="copy", mapper="global", threads=1, min_matches=30, every=None, keep=None,
                              ranges=None, name="", model=None, as_=None)
    with pytest.raises(DomusError, match="run domus video reconstruct first"):
        two_lens.run(project, args)
    with pytest.raises(DomusError, match="map first"):
        two_lens.run(project, argparse.Namespace(**{**vars(args), "stage": "convert"}))
    assert two_lens.stamp("t012.345.jpg") == 12.345
