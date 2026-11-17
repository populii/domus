"""Checks of this site's own data, run with pytest: `uv run pytest site/tests`.

The site is DOMUS_SITE if it's set, else this folder's own site (the folder above tests/). Tests get it through the
fixtures in domus.testing: `site`, and `built` for its model.
"""

from domus.testing import built, site, use_site  # noqa: F401 - pytest finds the fixtures here

use_site(__file__)
