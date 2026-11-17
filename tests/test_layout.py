"""The package's parts, and the old import paths of the modules that moved into them in 2026.11.38."""

import importlib

import domus


def test_an_old_name_is_the_module_itself():
    for old, new in domus.MOVED.items():
        assert importlib.import_module(f"domus.{old}") is importlib.import_module(f"domus.{new}"), old
    from domus.video import georef_sfm as old_georef

    from domus.capture import georef_sfm

    assert old_georef is georef_sfm and old_georef.__spec__.name == "domus.capture.georef_sfm"


def test_patching_an_old_name_patches_the_module(monkeypatch):
    from domus import terrain
    from domus.model import terrain as moved

    monkeypatch.setattr(terrain, "fixes_path", lambda: "patched")
    assert moved.fixes_path() == "patched"
