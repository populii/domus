"""The tests run on the made-up demo site, built once. Modules read DOMUS_SITE when imported, so
it's set here, before any test imports them."""

import os
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="domus-tests-"))
os.environ["DOMUS_SITE"] = str(_tmp / "site")
os.environ.pop("DOMUS_OUTPUT", None)
os.environ["DOMUS_STRICT_SETTINGS"] = "1"   # a setting the code reads must be in the schema, with the schema's default


@pytest.fixture(scope="session")
def site():
    from domus import demo

    demo.main([os.environ["DOMUS_SITE"]])
    return Path(os.environ["DOMUS_SITE"])


@pytest.fixture(scope="session")
def built(site):
    from domus import build

    build.main()
    return site / "viewer" / "data"
