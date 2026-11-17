"""The settings schema (domus.project.schema): defaults from one place, warnings on unknown keys, a strict test mode,
and the doc's tables written from it."""

import sys
from pathlib import Path

import pytest

from domus.project import schema

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import settings_doc


def site(data):
    return schema.Settings(data, schema.SITE, "data/site.toml: ")


def test_a_key_left_out_reads_as_the_schema_s_default():
    s = site({"house": {"fl_upper": 43.0}})
    assert s["house"].get("board_m") == 0.15 and s["house"]["board_m"] == 0.15
    assert s.get("site").get("title") == "Site model" and s.get("enrich").get("enabled") is True
    assert s.get("drapes") == [] and s.get("datums") == {} and s["house"].get("roof_azimuth_deg") is None
    with pytest.raises(KeyError):
        s["model"]                                             # a required section, still missing
    assert isinstance(s.get("house"), schema.Settings) and s.get("house").where == "data/site.toml: house."


def test_tables_maps_and_arrays_keep_their_schema():
    s = site({"services": {"notes": {"sewer": {"text": "From the plan"}}}, "drapes": [{"key": "plan", "label": "Plan", "georef": "a.json"}],
              "house": {"cladding": {"north": ["boards-grey", "plaster"]}}})
    assert s["services"]["notes"]["sewer"].get("confidence") is None
    assert s.get("drapes")[0].get("px") == 0.05
    assert s["house"]["cladding"].get("default") == ["boards-beige", "boards-beige"]


def test_the_tests_catch_a_setting_the_schema_doesnt_declare_or_a_default_it_doesnt_give():
    assert schema.STRICT                                       # tests/conftest.py turns it on
    with pytest.raises(schema.UndeclaredSetting, match="house.board_mm isn't in the settings schema"):
        site({"house": {}})["house"].get("board_mm", 0.15)
    with pytest.raises(schema.UndeclaredSetting, match="isn't the schema's"):
        site({"house": {}})["house"].get("board_m", 0.2)
    assert site({}).get("site").get("heading", "anything") == "anything"   # a default worked out where it's read


def test_an_unknown_key_gets_a_warning_naming_the_nearest_known_one():
    warnings = schema.unknown({"house": {"boards_m": 0.12}, "survey_offset": {"shift_m": [1, 2]},
                               "local": {"anything": {"at": "all"}}, "views": {"Front": {"pos": [0, 1, 2], "target": [0, 0, 0]}}},
                              schema.SITE, "data/site.toml: ")
    assert warnings == ["data/site.toml: house.boards_m isn't a setting Domus knows: did you mean board_m?",
                        "data/site.toml: survey_offset isn't a setting Domus knows."]
    # options/_site.toml keeps a site's own notes under [local] too, in the options' frame
    assert schema.unknown({"regrade": {"driveway_so": [[0, 0], [1, 1]]}, "local": {"driveway_so": [[0, 0], [1, 1]]}},
                          schema.OPTIONS_SITE, "options/_site.toml: ") ==         ["options/_site.toml: regrade.driveway_so isn't a setting Domus knows."]


def test_the_doc_says_what_the_schema_says():
    assert settings_doc.main(["--check"]) == 0, "run uv run python tools/settings_doc.py"
