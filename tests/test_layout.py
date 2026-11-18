"""The package's parts, and the old import paths of the modules that moved into them."""

import importlib
import subprocess
import sys

import pytest

import domus


@pytest.mark.filterwarnings("ignore::domus.errors.DomusDeprecationWarning")      # tests/test_old_names.py has them
def test_an_old_name_is_the_module_itself():
    for old, new in domus.MOVED.items():
        assert importlib.import_module(f"domus.{old}") is importlib.import_module(f"domus.{new}"), old
    from domus.video import georef_sfm as old_georef

    from domus.capture import georef_sfm

    assert old_georef is georef_sfm and old_georef.__spec__.name == "domus.capture.georef_sfm"


@pytest.mark.filterwarnings("ignore::domus.errors.DomusDeprecationWarning")
def test_patching_an_old_name_patches_the_module(monkeypatch):
    from domus import terrain
    from domus.model import terrain as moved

    monkeypatch.setattr(terrain, "fixes_path", lambda: "patched")
    assert moved.fixes_path() == "patched"


def test_the_apps_old_names_say_they_moved(monkeypatch):
    """domus.cli and domus.serve are the app's modules, and say they're deprecated; `python -m domus.cli` still runs the
    command line."""
    for old in ("cli", "serve"):
        monkeypatch.delitem(sys.modules, f"domus.{old}", raising=False)
        with pytest.warns(DeprecationWarning, match=f"domus.{old} is domus.app.{old} now"):
            module = importlib.import_module(f"domus.{old}")
        assert module is importlib.import_module(f"domus.app.{old}")
    r = subprocess.run([sys.executable, "-m", "domus.cli", "--version"], capture_output=True, text=True, check=False)
    assert r.returncode == 0 and r.stdout.strip() == domus.__version__, r.stderr
    assert "python -m domus.cli is python -m domus.app.cli now" in r.stderr
