"""The tests run on the made-up demo site, built once: DOMUS_SITE names it, as the command line would be told, and
`project` is it as Domus holds it, a domus.project.site.Site, for the tests to hand to what they test. In strict mode
(DOMUS_STRICT_SITE=1) a part of Domus called without its site is an error, so none can lean on the current site."""

import os
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="domus-tests-"))
os.environ["DOMUS_SITE"] = str(_tmp / "site")
os.environ.pop("DOMUS_OUTPUT", None)
os.environ["DOMUS_STRICT_SETTINGS"] = "1"   # a setting the code reads must be in the schema, with the schema's default
os.environ["DOMUS_STRICT_SITE"] = "1"       # a part of Domus is handed its site, never left to find the current one


@pytest.fixture(scope="session")
def site():
    from domus import demo

    demo.main([os.environ["DOMUS_SITE"]])
    return Path(os.environ["DOMUS_SITE"])


@pytest.fixture(scope="session")
def project(site):
    """The demo site as Domus holds it (domus.project.site.Site): the one the tests' process was started for."""
    from domus.project import site as sites

    return sites.started()


@pytest.fixture
def at_site():
    """Makes a folder the current site for the test (domus.project.site.use): at_site(path, output=None) gives the
    Site, and the site before it is current again after the test."""
    from contextlib import ExitStack

    from domus.project import site as sites

    with ExitStack() as stack:
        yield lambda path, output=None: stack.enter_context(sites.use(sites.Site(path, output)))


@pytest.fixture(scope="session")
def built(site, project):
    from domus import build

    build.main(project)
    return site / "viewer" / "data"
