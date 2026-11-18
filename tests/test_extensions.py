"""A site's own code in its builds: run only when trusted on this machine, as it is, and never in the image."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from domus.errors import DomusError
from domus.project import extensions

TOOLS = Path(__file__).resolve().parents[1] / "tools"
PEGS = '''def layers(site):
    e0, n0 = site.settings()["model"]["origin"][:2]
    return [{"id": "site-peg", "type": "labels", "name": "Pegs", "items": [{"text": "Peg", "at": [e0, n0, 45.0]}]}]
'''


@pytest.fixture
def trust_file(tmp_path, monkeypatch):
    path = tmp_path / "trust.json"
    monkeypatch.setenv("DOMUS_TRUST_FILE", str(path))
    monkeypatch.delenv("DOMUS_NO_EXTENSIONS", raising=False)
    return path


def site_with(root, code=PEGS):
    (root / "tools").mkdir(parents=True, exist_ok=True)
    (root / "tools" / "pegs.py").write_text(code, encoding="utf-8")
    return {"domus": {"extensions": ["tools/pegs.py"]}}


def test_nothing_runs_until_it_s_trusted_as_it_is(tmp_path, trust_file):
    settings = site_with(tmp_path)
    assert extensions.check(tmp_path, {"domus": {}}) == []
    with pytest.raises(DomusError, match="aren't trusted on this machine"):
        extensions.check(tmp_path, settings)
    (rel, sha), = extensions.trust(tmp_path, settings)
    assert rel == "tools/pegs.py" and json.loads(trust_file.read_text(encoding="utf-8")) == {
        str((tmp_path / "tools" / "pegs.py").resolve()): sha}
    (checked,) = extensions.check(tmp_path, settings)
    assert checked[0] == "tools/pegs.py" and checked[2] == sha
    (tmp_path / "tools" / "pegs.py").write_text(PEGS + "# changed\n", encoding="utf-8")
    with pytest.raises(DomusError, match="or have changed"):
        extensions.check(tmp_path, settings)
    extensions.trust(tmp_path, settings, revoke=True)
    assert json.loads(trust_file.read_text(encoding="utf-8")) == {}


def test_the_image_runs_no_site_s_code_and_a_missing_file_stops_the_build(tmp_path, trust_file, monkeypatch):
    settings = site_with(tmp_path)
    extensions.trust(tmp_path, settings)
    monkeypatch.setenv("DOMUS_NO_EXTENSIONS", "1")
    with pytest.raises(DomusError, match="runs no site's code"):
        extensions.check(tmp_path, settings)
    monkeypatch.delenv("DOMUS_NO_EXTENSIONS")
    with pytest.raises(DomusError, match="no such file"):
        extensions.check(tmp_path, {"domus": {"extensions": ["tools/gone.py"]}})
    with pytest.raises(DomusError, match="a list of files"):
        extensions.declared({"domus": {"extensions": "tools/pegs.py"}})


def test_an_extension_s_own_failure_is_named(tmp_path, trust_file):
    for code, says in [("x = 1\n", "has no layers"), ("def layers(site):\n    raise KeyError('lot')\n", "failed: KeyError")]:
        settings = site_with(tmp_path, code)
        extensions.trust(tmp_path, settings)
        with pytest.raises(DomusError, match=says):
            extensions.run(extensions.check(tmp_path, settings), site=None)


def build(site, out, env):
    return subprocess.run([sys.executable, "-m", "domus.app.cli", "build"], check=False, env={**os.environ, **env, "DOMUS_SITE": str(site),
                          "DOMUS_OUTPUT": str(out)}, capture_output=True, text=True, encoding="utf-8")


def test_a_trusted_extension_draws_in_the_build_and_build_json_says_so(tmp_path):
    sys.path.insert(0, str(TOOLS))
    import made_up_sites

    made_up_sites.write(tmp_path / "sites")
    site = tmp_path / "demo"
    shutil.copytree(tmp_path / "sites" / "demo", site)
    site_with(site)
    toml = site / "data" / "site.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace("[domus]\n", '[domus]\nextensions = ["tools/pegs.py"]\n', 1),
                    encoding="utf-8")
    env = {"DOMUS_TRUST_FILE": str(tmp_path / "trust.json"), "DOMUS_NO_EXTENSIONS": "0"}
    stopped = build(site, tmp_path / "out", env)
    assert stopped.returncode != 0 and "aren't trusted on this machine" in stopped.stderr + stopped.stdout
    assert not (tmp_path / "out" / "model.json").exists()
    subprocess.run([sys.executable, "-m", "domus.app.cli", "--site", str(site), "site", "trust"], check=True,
                   env={**os.environ, **env}, capture_output=True)
    assert build(site, tmp_path / "out", env).returncode == 0
    model = json.loads((tmp_path / "out" / "model.json").read_text(encoding="utf-8"))
    (peg,) = [layer for layer in model["layers"] if layer["id"] == "site-peg"]
    assert peg["items"][0]["text"] == "Peg" and peg["items"][0]["at"][0] == 0.0
    made = json.loads((tmp_path / "out" / "build.json").read_text(encoding="utf-8"))
    assert list(made["extensions"]) == ["tools/pegs.py"] and len(made["extensions"]["tools/pegs.py"]) == 64
