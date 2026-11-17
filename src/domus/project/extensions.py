"""A site's own code in its builds, only where it's trusted.

A site can declare Python files of its own for the build to run: `[domus] extensions = ["tools/pegs.py"]` in
data/site.toml. Each defines `layers(site)`, which gets the site through Domus's public API (domus.api) and returns
layers of its own, written as data/layers/*.json holds them (docs/model-format.md). The build places them, under ids
that start `site-`, as it does a site's layers written as data.

Running a site's code is trusting it, so none runs until it's trusted on this machine. `domus site trust` shows each
declared file and its SHA-256, and records them in this user's trust file; a file changed since is untrusted again,
as direnv treats an .envrc. A build whose declared extensions aren't all trusted stops and says so, rather than build
a model without them. The container image trusts nothing (DOMUS_NO_EXTENSIONS=1), and the server never imports a
site's code: only `domus build` runs it, and build.json records what ran.
"""

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

from ..errors import DomusError


def declared(settings):
    """The site's extensions as data/site.toml declares them: paths relative to the site's folder."""
    files = settings.get("domus", {}).get("extensions", [])
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        raise DomusError('data/site.toml: [domus] extensions is a list of files, such as ["tools/pegs.py"]')
    return files


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def trust_file():
    """This user's trust file: DOMUS_TRUST_FILE, else domus/trust.json in their configuration folder."""
    if os.environ.get("DOMUS_TRUST_FILE"):
        return Path(os.environ["DOMUS_TRUST_FILE"])
    base = os.environ.get("APPDATA") if sys.platform == "win32" else os.environ.get("XDG_CONFIG_HOME")
    return Path(base or Path.home() / ".config") / "domus" / "trust.json"


def trusted():
    """{absolute path: SHA-256} of the files trusted on this machine."""
    try:
        return json.loads(trust_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def check(root, settings):
    """The site's declared extensions as [(relative path, absolute path, SHA-256)], or a DomusError saying why the
    build can't run them: none run here, one is missing, or one isn't trusted as it is now."""
    files = declared(settings)
    if not files:
        return []
    if os.environ.get("DOMUS_NO_EXTENSIONS") == "1":
        raise DomusError(f"This site declares extensions ({', '.join(files)}), and this Domus runs no site's code (the "
                         "container image trusts none). Build it where its extensions are trusted, with domus build, "
                         "or take them out of [domus] extensions.")
    known, out, untrusted = trusted(), [], []
    for rel in files:
        path = (Path(root) / rel).resolve()
        if not path.is_file():
            raise DomusError(f"data/site.toml declares the extension {rel}, and there's no such file.")
        sha = digest(path)
        if known.get(str(path)) != sha:
            untrusted.append(rel)
        out.append((rel, path, sha))
    if untrusted:
        raise DomusError(f"This site's extensions {', '.join(untrusted)} aren't trusted on this machine, or have changed "
                         "since they were. Read them, then run domus site trust to let builds run them.")
    return out


def trust(root, settings, revoke=False):
    """Records the site's declared extensions as they are now in this user's trust file (or drops them, with
    `revoke`), and returns [(relative path, SHA-256)]."""
    known, out = trusted(), []
    for rel in declared(settings):
        path = (Path(root) / rel).resolve()
        if revoke:
            known.pop(str(path), None)
            out.append((rel, None))
            continue
        if not path.is_file():
            raise DomusError(f"data/site.toml declares the extension {rel}, and there's no such file.")
        known[str(path)] = digest(path)
        out.append((rel, known[str(path)]))
    target = trust_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(known, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return out


def run(checked, site):
    """Runs each checked extension's layers(site): [(relative path, SHA-256, its layers)]."""
    out = []
    for rel, path, sha in checked:
        spec = importlib.util.spec_from_file_location(f"domus_site_extension_{sha[:12]}", path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            layers = module.layers(site) if hasattr(module, "layers") else None
        except DomusError:
            raise
        except Exception as e:      # the site's own code: whatever it raises, the build says whose it was
            raise DomusError(f"The site's extension {rel} failed: {type(e).__name__}: {e}") from e
        if not isinstance(layers, list):
            raise DomusError(f"The site's extension {rel} has no layers(site) that returns a list of layers.")
        out.append((rel, sha, layers))
    return out
