"""The tests run on the made-up demo site, built once: DOMUS_SITE names it, as the command line would be told, and
`project` is it as Domus holds it, a domus.project.site.Site, for the tests to hand to what they test."""

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
    from domus.project import demo

    demo.write(os.environ["DOMUS_SITE"])
    return Path(os.environ["DOMUS_SITE"])


@pytest.fixture(scope="session")
def project(site):
    """The demo site as Domus holds it (domus.project.site.Site): the one the tests' process was started for."""
    from domus.project import site as sites

    return sites.started()


@pytest.fixture
def at_site():
    """Another folder's site, for the test to hand to what it tests: at_site(path, output=None) gives its Site
    (domus.project.site.Site), its model built into `output` if that's given."""
    from domus.project import site as sites

    return sites.Site


@pytest.fixture
def domus(monkeypatch):
    """The command line, in this process: domus("--site", path, "marks") runs `domus --site PATH marks` and gives what
    it returns; an error ends it as the command line's would, with SystemExit. The site it was given, DOMUS_SITE and
    DOMUS_OUTPUT, and sys.argv are as they were after the test."""
    import sys

    from domus import api
    from domus.app import cli
    from domus.project import site as sites

    monkeypatch.setattr(sites, "_started", sites._started)
    monkeypatch.setattr(api, "_opened", dict(api._opened))
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    before = {name: os.environ.get(name) for name in ("DOMUS_SITE", "DOMUS_OUTPUT")}
    yield lambda *args: cli.main([str(a) for a in args])
    for name, value in before.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(scope="session")
def built(site, project):
    from domus import build

    build.main(project)
    return site / "viewer" / "data"
