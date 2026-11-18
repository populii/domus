"""Site formats: an older site read in memory as it said things, and `domus site upgrade` writing that into its files."""

import math
import sys
import tomllib
from pathlib import Path

import numpy as np
import pytest

from domus.errors import DomusError
from domus.project import formats, upgrade

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import made_up_sites


@pytest.fixture(scope="module")
def old_site(tmp_path_factory):
    out = tmp_path_factory.mktemp("formats") / "sites"
    made_up_sites.write(out)
    return out / "format1"


def read(path):
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_the_versions_a_site_can_ask_for():
    assert formats.satisfies("2026.12.0", ">=2026.12.0") and not formats.satisfies("2026.11.30", ">=2026.12.0")
    assert formats.satisfies("2026.12.3", ">=2026.11.27,<2027.1.0") and not formats.satisfies("2027.1.0", ">=2026.11.27,<2027.1.0")
    with pytest.raises(DomusError, match="isn't a version"):
        formats.satisfies("2026.12.0", "the latest")


def test_a_site_this_domus_cant_read_is_refused():
    with pytest.raises(DomusError, match="reads formats up to"):
        formats.check({"domus": {"format": formats.FORMAT + 1}})
    with pytest.raises(DomusError, match="needs Domus >=2099.1.0"):
        formats.check({"domus": {"requires": ">=2099.1.0"}})
    formats.check({"domus": {"format": 1, "requires": ">=2026.1.0"}})


def test_an_old_site_reads_as_it_said_things(old_site):
    raw = read(old_site / "data" / "site.toml")
    assert formats.format_of(raw) == 1
    site = formats.site(raw)
    assert site["domus"]["format"] == formats.FORMAT and "printed_line" not in site["services"]
    assert site["house"]["fl_lower"] == pytest.approx(raw["house"]["fl_upper"] - 2.6)
    assert "fl_upper_rel" not in site["house"] and "fl_upper_rel" in raw["house"]          # the file as read is left alone
    shared = formats.options_site(read(old_site / "options" / "_site.toml"), 1)
    assert "regrade" in shared and "front_garden" not in shared


def test_old_compass_words_point_the_same_way_on_the_grid(old_site):
    frame = formats.options_site(read(old_site / "options" / "_site.toml"), 1)["frame"]
    option = formats.option(read(old_site / "options" / "f-old-falls.toml"), 1, frame)
    upper, lower = option["terraces"]
    # The frame runs 200 degrees from north, the lot on its right: format 1's "east", towards the boundary, is 110, and
    # its "north", back along it, 20 (a little off both, as the frame's far point is rounded to the millimetre).
    assert upper["fall_to"] == pytest.approx(110.0, abs=0.01) and lower["fall_to"] == pytest.approx(20.0, abs=0.01)
    from domus import design

    assert np.allclose(design.compass(upper["fall_to"]), [math.sin(math.radians(110)), math.cos(math.radians(110))], atol=1e-3)
    assert np.allclose(design.compass("east"), [1, 0]) and np.allclose(design.compass(90), [1, 0])
    with pytest.raises(DomusError, match="fall_to"):
        design.compass("uphill")


def test_upgrading_writes_what_was_read_and_keeps_the_comments(old_site, tmp_path, capsys):
    import shutil

    site = tmp_path / "site"
    shutil.copytree(old_site, site, ignore=shutil.ignore_patterns("viewer", ".domus"))
    before = {p: p.read_text(encoding="utf-8") for p in [site / "data" / "site.toml", site / "options" / "_site.toml",
                                                          site / "options" / "f-old-falls.toml"]}
    assert upgrade.upgrade(site, dry_run=True) == 0
    assert {p: p.read_text(encoding="utf-8") for p in before} == before              # a dry run writes nothing
    assert upgrade.upgrade(site) == 0
    printed = capsys.readouterr().out
    assert "fall_to 'east' along the frame" in printed and "[front_garden] is now [regrade]" in printed
    new = read(site / "data" / "site.toml")
    assert new == formats.site(tomllib.loads(before[site / "data" / "site.toml"]))
    assert formats.format_of(new) == formats.FORMAT
    shared = (site / "options" / "_site.toml").read_text(encoding="utf-8")
    assert "[regrade]" in shared and "[front_garden]" not in shared
    assert "# Where options may regrade the ground" in shared                         # its comments are still there
    assert read(site / "options" / "f-old-falls.toml")["terraces"][0]["fall_to"] == pytest.approx(110.0, abs=0.01)
    kept = list((site / ".domus" / "upgrade").glob("format-1-*/data/site.toml"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == before[site / "data" / "site.toml"]
    assert upgrade.upgrade(site) == 0 and "nothing to upgrade" in capsys.readouterr().out


def test_a_new_site_is_in_this_domus_s_format(site):
    assert formats.format_of(read(site / "data" / "site.toml")) == formats.FORMAT


def test_a_format_2_site_keeps_what_it_took_from_new_zealand(old_site, tmp_path, capsys):
    import shutil

    from domus.project.formats import LINZ_CREDIT

    two = old_site.parent / "format2"
    raw = read(two / "data" / "site.toml")
    assert formats.format_of(raw) == 2 and "crs" not in raw
    site = formats.site(raw)
    assert site["crs"] == {"epsg": 2193, "vertical": "NZVD2016"} and site["site"]["attribution"] == LINZ_CREDIT
    # A site that says them keeps its own.
    abroad = formats.site({"domus": {"format": 2}, "crs": {"epsg": 27700, "vertical": ""}, "site": {"attribution": ""}})
    assert abroad["crs"] == {"epsg": 27700, "vertical": ""} and abroad["site"]["attribution"] == ""
    # Upgraded, it says them in its own file.
    copy = tmp_path / "two"
    shutil.copytree(two, copy, ignore=shutil.ignore_patterns("viewer", ".domus"))
    assert upgrade.upgrade(copy) == 0
    assert "[crs] epsg = 2193" in capsys.readouterr().out
    new = read(copy / "data" / "site.toml")
    assert new["crs"]["epsg"] == 2193 and new["site"]["attribution"] == LINZ_CREDIT and new == formats.site(raw)


def test_a_format_3_site_reads_clean_with_how_sure_its_levels_are_under_local(old_site, at_site, capsys):
    """[datums.<datum>] uncertainty_m and [house] fl_upper_uncertainty_m aren't Domus's settings since format 4: a
    format 3 site that gives them reads with them under [local], with no warning, in the tests' strict settings."""
    from domus import crs
    from domus.project import schema

    three = old_site.parent / "format3"
    raw = read(three / "data" / "site.toml")
    assert formats.format_of(raw) == 3
    assert raw["datums"]["plans"]["uncertainty_m"] == 0.05 and raw["house"]["fl_upper_uncertainty_m"] == 0.02
    assert len(schema.unknown(raw, schema.SITE)) == 2                       # as it says them, Domus doesn't know them
    site = formats.site(raw)
    assert site["local"] == {"datums": {"plans": {"uncertainty_m": 0.05}}, "house": {"fl_upper_uncertainty_m": 0.02}}
    assert "uncertainty_m" not in site["datums"]["plans"] and "fl_upper_uncertainty_m" not in site["house"]
    assert schema.unknown(site, schema.SITE) == []
    at_site(three)
    capsys.readouterr()
    s = crs.settings()
    assert "Warning" not in capsys.readouterr().err
    assert s["local"]["house"]["fl_upper_uncertainty_m"] == 0.02 and s["local"]["datums"]["plans"]["uncertainty_m"] == 0.05
    assert crs.datums() == [{"key": "plans", "label": "The plans' own datum (made up)", "offset_m": 57.3}]


def test_upgrading_a_format_3_site_moves_them_under_local(old_site, tmp_path, capsys):
    import shutil

    copy = tmp_path / "three"
    shutil.copytree(old_site.parent / "format3", copy, ignore=shutil.ignore_patterns("viewer", ".domus"))
    before = (copy / "data" / "site.toml").read_text(encoding="utf-8")
    assert upgrade.upgrade(copy) == 0
    printed = capsys.readouterr().out
    assert "[house] fl_upper_uncertainty_m is now [local.house] fl_upper_uncertainty_m" in printed
    assert "[datums.plans] uncertainty_m is now [local.datums.plans] uncertainty_m" in printed
    text = (copy / "data" / "site.toml").read_text(encoding="utf-8")
    new = tomllib.loads(text)
    assert new == formats.site(tomllib.loads(before)) and formats.format_of(new) == formats.FORMAT
    assert new["local"]["datums"]["plans"]["uncertainty_m"] == 0.05 and new["local"]["house"]["fl_upper_uncertainty_m"] == 0.02
    assert "uncertainty_m" not in new["datums"]["plans"] and "fl_upper_uncertainty_m" not in new["house"]
    assert "[local.house]\nfl_upper_uncertainty_m = 0.02          # made up\n" in text        # with its comment
    assert "uncertainty_m = 0.05          # made up too\n" in text.split("[local.datums.plans]\n")[1]
    kept = list((copy / ".domus" / "upgrade").glob("format-3-*/data/site.toml"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == before


def test_moving_them_under_local_keeps_what_local_has():
    """One the site has under [local] already is kept, and one with something other than a table where it would go
    stays where it was."""
    site = {"datums": {"a": {"offset_m": 1.0, "uncertainty_m": 0.1}, "b": {"offset_m": 2.0, "uncertainty_m": 0.2}},
            "house": {"fl_upper": 9.0, "fl_upper_uncertainty_m": 0.3},
            "local": {"datums": {"a": {"uncertainty_m": 0.05}, "b": "mine"}, "note": "kept"}}
    notes = formats.site_3(site)
    assert site == {"datums": {"a": {"offset_m": 1.0}, "b": {"offset_m": 2.0, "uncertainty_m": 0.2}}, "house": {"fl_upper": 9.0},
                    "local": {"datums": {"a": {"uncertainty_m": 0.05}, "b": "mine"}, "note": "kept",
                              "house": {"fl_upper_uncertainty_m": 0.3}}}
    assert any("[datums.a] uncertainty_m dropped" in n for n in notes) and any("[datums.b] uncertainty_m left" in n for n in notes)
    assert formats.site_3({"datums": {"a": {"offset_m": 1.0}}, "house": {"fl_upper": 9.0}}) == []


def test_a_format_3_site_names_its_grid(site, monkeypatch):
    from domus import crs

    assert crs.site_epsg() == 2193
    monkeypatch.setattr(crs, "settings", lambda: {"crs": {}})
    with pytest.raises(DomusError, match=r"\[crs\] epsg is missing"):
        crs.site_epsg()
