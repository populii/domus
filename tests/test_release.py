"""Calendar versions, and what they do with the workflow and package tools."""

import datetime
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release", ROOT / "tools" / "release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
NOV = datetime.date(2026, 11, 2)


def test_first_of_a_month_counts_from_zero():
    assert release.next_version(["v0.5.0", "v2026.10.3"], NOV) == "2026.11.0"


def test_later_in_the_month_counts_up():
    assert release.next_version(["v2026.11.0", "v2026.11.9", "v2026.11.10"], NOV) == "2026.11.11"


def test_a_new_year_and_single_digit_month():
    assert release.next_version(["v2026.12.4"], datetime.date(2027, 1, 5)) == "2027.1.0"


def test_it_sorts_after_the_old_versions_and_fits_the_release_tag():
    def key(v):
        return tuple(int(x) for x in v.split("."))

    v = release.next_version([], NOV)
    assert key(v) > key("0.5.0")
    pattern = re.search(r'tags: \["v(.+?)"\]', (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")).group(1)
    assert re.fullmatch(pattern, v)
