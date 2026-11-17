"""What a build can take from the last one: the ground's pictures, and each wall option's layers.

Each part is kept with a key made of everything it's built from: the Domus code itself, the site's files (all of them
but the wall options, which each option's key adds on its own) and what the part takes from the rest of the build.
A part whose key hasn't changed is taken from the cache instead of drawn again, so saving one option rebuilds that
option, not the whole model. A part is only ever taken whole, files and all, and a cached build is byte for byte the
build it saves.

The cache is the site's .domus/build-cache/, and holds what the last build used. A site folder Domus can't write to
builds without it, and DOMUS_BUILD_CACHE=0 (or `domus build --fresh`) builds everything afresh.
"""

import hashlib
import json
import os
import shutil
from functools import lru_cache
from pathlib import Path

from .. import __version__

# Folders a build never reads: the model it writes, the app's own state, documents, and the video work files.
SKIP = {"viewer", ".domus", ".git", "sources", "__pycache__", "tests", "docs"}
SKIP_UNDER_DATA = {"cache"}


@lru_cache
def code_key():
    """The Domus code that builds: its version and every module's source."""
    h = hashlib.sha256(__version__.encode())
    here = Path(__file__).resolve().parents[1]           # the whole domus package
    for path in sorted(here.rglob("*.py")):
        h.update(path.relative_to(here).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def site_files(root):
    """The site's files a build may read, all but the wall options: (relative path, size, mtime in ns), in order."""
    root = Path(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        dirnames[:] = sorted(d for d in dirnames if not (rel == Path(".") and d in SKIP) and
                             not (rel.parts[:1] == ("data",) and len(rel.parts) >= 2 and rel.parts[-1] == "video" and d in SKIP_UNDER_DATA))
        for name in sorted(filenames):
            path = rel / name
            if path.parts[0] == "options" and len(path.parts) == 2 and not name.startswith("_"):
                continue
            st = (root / path).stat()
            out.append((path.as_posix(), st.st_size, st.st_mtime_ns))
    return out


def key(*parts):
    h = hashlib.sha256()
    for part in parts:
        h.update(json.dumps(part, sort_keys=True, default=str).encode())
        h.update(b"\0")
    return h.hexdigest()


class Cache:
    """The cache of one site's builds, in `root`/.domus/build-cache. `enabled` is False where it can't be used."""

    def __init__(self, root, out):
        self.dir = Path(root) / ".domus" / "build-cache"
        self.out = Path(out)
        self.used = set()
        self.enabled = os.environ.get("DOMUS_BUILD_CACHE", "1") != "0"
        if self.enabled:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
                probe = self.dir / ".written"
                probe.write_text("", encoding="utf-8")
                probe.unlink()
            except OSError:
                self.enabled = False
        self.site = key(code_key(), site_files(root)) if self.enabled else None

    def get(self, name, k):
        """The data kept under `name` for key `k`, with its files put back in the output folder, or None."""
        if not self.enabled:
            return None
        entry = self.dir / f"{name}-{k[:24]}"
        try:
            data = json.loads((entry / "entry.json").read_text(encoding="utf-8"))
            if data["key"] != k:
                return None
            for f in data["files"]:
                shutil.copyfile(entry / f, self.out / f)
        except (OSError, ValueError, KeyError):
            return None
        self.used.add(entry.name)
        return data["value"]

    def put(self, name, k, value, files):
        """Keeps `value` (JSON) and the output folder's `files` under `name` for key `k`."""
        if not self.enabled:
            return
        entry = self.dir / f"{name}-{k[:24]}"
        try:
            if entry.exists():
                shutil.rmtree(entry)
            entry.mkdir(parents=True)
            for f in files:
                shutil.copyfile(self.out / f, entry / f)
            (entry / "entry.json").write_text(json.dumps({"key": k, "files": sorted(files), "value": value}),
                                              encoding="utf-8")
        except (OSError, TypeError, ValueError):
            shutil.rmtree(entry, ignore_errors=True)
            return
        self.used.add(entry.name)

    def prune(self):
        """Drops what this build didn't use."""
        if not self.enabled:
            return
        for entry in self.dir.iterdir():
            if entry.is_dir() and entry.name not in self.used:
                shutil.rmtree(entry, ignore_errors=True)


def written(out, before):
    """The files in `out` written since `before` (a snapshot from snapshot())."""
    now = snapshot(out)
    return sorted(name for name, stamp in now.items() if before.get(name) != stamp)


def snapshot(out):
    out = Path(out)
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in out.iterdir() if p.is_file()} if out.is_dir() else {}
