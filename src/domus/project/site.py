"""A site project as a workflow holds it: its folder, where its model is built, and what Domus has read of it so far.

Domus's modules read the site that is current. That's the one the process was started for (DOMUS_SITE, else the
current folder, with its model built into DOMUS_OUTPUT if that's set), found the first time something asks for it, or
one a workflow makes current for a while:

    with use(Site("sites/other")):
        build.main([])

Whatever a module keeps of what it read (the settings, the rasters, the ground) it keeps on the site (`cached`), so
one process can hold several sites, one after another or in turn, and each is read as itself.
"""

import contextvars
import functools
import os
from contextlib import contextmanager
from pathlib import Path


class Site:
    """A site project: its folder (`path`), the folder its model is built into (`output`, viewer/data in the site
    unless given), and what Domus has read of it (`cache`, filled by the functions marked `cached`)."""

    def __init__(self, path, output=None):
        self.path = Path(path).resolve()
        self.output = Path(output).resolve() if output else self.path / "viewer" / "data"
        self.cache = {}

    def __repr__(self):
        return f"Site({str(self.path)!r})"


_current = contextvars.ContextVar("domus_site", default=None)
_started = None


def started():
    """The site the process was started for: DOMUS_SITE (else the current folder), its model built into DOMUS_OUTPUT if
    that's set. Read the first time it's asked for, not when Domus is imported."""
    global _started
    if _started is None:
        root = Path(os.environ.get("DOMUS_SITE", ".")).resolve()
        _started = Site(root, os.environ.get("DOMUS_OUTPUT") or None)
    return _started


def current():
    """The site Domus's modules read now: the one a workflow made current with use(), else the one the process was
    started for."""
    return _current.get() or started()


@contextmanager
def use(site):
    """Make `site` the current one while the block runs."""
    token = _current.set(site)
    try:
        yield site
    finally:
        _current.reset(token)


def cached(fn):
    """functools.lru_cache, kept on the current site: each site has its own of fn's results. cache_clear() forgets the
    current site's."""
    name = f"{fn.__module__}.{fn.__qualname__}"

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        store = current().cache
        key = (name, args, tuple(sorted(kwargs.items())))
        if key not in store:
            store[key] = fn(*args, **kwargs)
        return store[key]

    def cache_clear():
        store = current().cache
        for key in [k for k in store if k[0] == name]:
            del store[key]

    wrapper.cache_clear = cache_clear
    return wrapper
