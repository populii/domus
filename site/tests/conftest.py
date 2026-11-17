"""Checks of this site's own data, run with pytest: `uv run pytest site/tests`.

Domus reads the site's folder when its modules are imported, so it's named here, before any test imports them:
DOMUS_SITE if it's set, else this folder's own site (the folder above tests/).
"""

import os
from pathlib import Path

import pytest

SITE = Path(os.environ.get("DOMUS_SITE") or Path(__file__).resolve().parents[1]).resolve()
os.environ["DOMUS_SITE"] = str(SITE)
os.environ.pop("DOMUS_OUTPUT", None)


@pytest.fixture(scope="session")
def site():
    if not (SITE / "data" / "site.toml").exists():
        pytest.skip(f"{SITE} has no site project yet: see site/README.md to start one")
    return SITE
