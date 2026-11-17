"""The build cache: what a build may take from the last one, and a build that takes it is the build it saves."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from domus import buildcache

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def test_a_site_s_key_follows_its_files_but_not_its_options(tmp_path):
    for rel in ("data/site.toml", "data/site/lot.geojson", "options/_site.toml", "options/a.toml", "viewer/data/model.json",
                ".domus/viewer.db", "sources/raw/plan.pdf", "data/video/cache/frame.jpg", "data/video/a-site.npz"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(rel, encoding="utf-8")
    files = [f for f, _, _ in buildcache.site_files(tmp_path)]
    assert files == ["data/site.toml", "data/site/lot.geojson", "data/video/a-site.npz", "options/_site.toml"]
    before = buildcache.key(buildcache.site_files(tmp_path))
    (tmp_path / "options" / "a.toml").write_text("changed", encoding="utf-8")
    assert buildcache.key(buildcache.site_files(tmp_path)) == before
    (tmp_path / "data" / "site.toml").write_text("changed, and longer", encoding="utf-8")
    assert buildcache.key(buildcache.site_files(tmp_path)) != before


def test_a_part_is_kept_with_its_files_and_dropped_when_a_build_doesnt_use_it(tmp_path, monkeypatch):
    monkeypatch.delenv("DOMUS_BUILD_CACHE", raising=False)
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.jpg").write_bytes(b"picture")
    cache = buildcache.Cache(tmp_path, out)
    assert cache.enabled and cache.get("ground", "k1") is None
    cache.put("ground", "k1", {"relief": {"file": "a.jpg"}}, ["a.jpg"])
    cache.put("option-x", "k2", [[], {}, {}], [])
    (out / "a.jpg").unlink()
    again = buildcache.Cache(tmp_path, out)
    assert again.get("ground", "k1") == {"relief": {"file": "a.jpg"}} and (out / "a.jpg").read_bytes() == b"picture"
    assert again.get("ground", "other key") is None
    again.prune()
    assert [p.name for p in (tmp_path / ".domus" / "build-cache").iterdir()] == ["ground-k1"]


def test_no_cache_when_asked_or_where_it_cant_be_written(tmp_path, monkeypatch):
    monkeypatch.setenv("DOMUS_BUILD_CACHE", "0")
    assert not buildcache.Cache(tmp_path, tmp_path).enabled
    monkeypatch.delenv("DOMUS_BUILD_CACHE")
    (tmp_path / ".domus").write_text("a file where the folder would go", encoding="utf-8")
    assert not buildcache.Cache(tmp_path, tmp_path).enabled


def build(site, out, *extra):
    """A build of `site` into `out` (with DOMUS_SITE, as --site would build into the site's own folder)."""
    env = {**os.environ, "DOMUS_SITE": str(site), "DOMUS_OUTPUT": str(out)}
    subprocess.run([sys.executable, "-m", "domus.cli", "build", *extra], check=True, env=env, capture_output=True)
    return out


@pytest.fixture(scope="module")
def made_up(tmp_path_factory):
    sys.path.insert(0, str(TOOLS))
    import made_up_sites

    out = tmp_path_factory.mktemp("cache") / "sites"
    made_up_sites.write(out)
    return out


def test_a_cached_build_after_an_option_changes_is_the_fresh_build(made_up, tmp_path):
    import compare_builds

    site = tmp_path / "demo"
    shutil.copytree(made_up / "demo", site)
    build(site, tmp_path / "first")
    option = min(p for p in (site / "options").glob("*.toml") if not p.name.startswith("_"))
    text = option.read_text(encoding="utf-8")
    option.write_text(text.replace('name = "', 'name = "Changed: ', 1), encoding="utf-8")
    cached, fresh = build(site, tmp_path / "cached"), build(site, tmp_path / "fresh", "--fresh")
    a, b = (json.loads((d / "model.json").read_text(encoding="utf-8")) for d in (fresh, cached))
    assert list(compare_builds.diffs(a, b)) == []
    for f in fresh.iterdir():
        if f.name not in ("model.json", "build.json"):
            assert (cached / f.name).read_bytes() == f.read_bytes(), f.name
    assert "Changed: " in json.dumps(b["summary"])                           # the change is in it
    entries = sorted(p.name.rsplit("-", 1)[0] for p in (site / ".domus" / "build-cache").iterdir())
    assert entries[0] == "ground" and all(e.startswith("option-") for e in entries[1:])
