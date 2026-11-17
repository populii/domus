"""pytest fixtures for a site's own tests, part of the public API (see domus.api).

In a site project's tests/conftest.py:

    from domus.testing import built, site, use_site  # noqa: F401 - pytest finds the fixtures here

    use_site(__file__)

A test then asks for `site`, the site project as domus.api.open_site gives it, or `built`, the folder of its model.
`built` builds the site once for the whole run, into a folder of the run's own, so the model a server shows is left
alone. Every test using either is skipped if the folder has no site project yet.
"""

import os
import tempfile
from pathlib import Path

import pytest

from .errors import DomusError

_output = None


def use_site(conftest):
    """Names the site these tests are for: DOMUS_SITE if it's set, else the folder above the tests' own (pass the
    conftest's __file__). Call it at the top of tests/conftest.py, since Domus reads the site when its modules are
    imported. Its model is built into a temporary folder, whatever DOMUS_OUTPUT says."""
    global _output
    os.environ["DOMUS_SITE"] = str(Path(os.environ.get("DOMUS_SITE") or Path(conftest).resolve().parents[1]).resolve())
    _output = Path(tempfile.mkdtemp(prefix="domus-site-tests-"))
    os.environ["DOMUS_OUTPUT"] = str(_output)


@pytest.fixture(scope="session")
def site():
    """The site project, as domus.api.open_site gives it."""
    from . import api

    try:
        return api.open_site()
    except DomusError as e:
        pytest.skip(str(e))


@pytest.fixture(scope="session")
def built(site):
    """The folder of the site's model, built once for the run."""
    if _output is None:
        raise DomusError("Call domus.testing.use_site(__file__) at the top of tests/conftest.py first.")
    return site.build()
