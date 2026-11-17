"""The tests run on the made-up demo site, built once. Domus reads DOMUS_SITE the first time it needs
the site, so it's set here, before any test runs."""

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


@pytest.fixture
def at_site():
    """Makes a folder the current site for the test (domus.project.site.use): at_site(path, output=None) gives the
    Site, and the site before it is current again after the test."""
    from contextlib import ExitStack

    from domus.project import site as sites

    with ExitStack() as stack:
        yield lambda path, output=None: stack.enter_context(sites.use(sites.Site(path, output)))


@pytest.fixture(scope="session")
def built(site):
    from domus import build

    build.main()
    return site / "viewer" / "data"
