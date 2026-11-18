"""`domus site upgrade`: writes a site's migrations into its own files, so they say what this Domus reads.

    domus site upgrade [--dry-run]

Domus reads an older site as it is, migrating it in memory, so a site never has to be upgraded to be used. Upgrading
is for editing it: its files then say what the model is built from, in this Domus's terms. The files that change keep
their comments, are copied to .domus/upgrade/ first, and are checked to say just what the site said in memory.
"""

import datetime
import shutil
import tomllib
from pathlib import Path

import tomlkit

from ..errors import DomusError
from . import formats


def option_files(root):
    return sorted(p for p in (root / "options").glob("*.toml") if not p.name.startswith("_"))


def plan(root):
    """What upgrading the site in `root` would write: (the format it's in, {path: new text}, notes)."""
    site_path = root / "data" / "site.toml"
    site = tomlkit.parse(site_path.read_text(encoding="utf-8"))
    formats.check(site)
    fmt = formats.format_of(site)
    if fmt >= formats.FORMAT:
        return fmt, {}, []
    docs, notes = {site_path: site}, []
    options_site_path = root / "options" / "_site.toml"
    options_site = tomlkit.parse(options_site_path.read_text(encoding="utf-8")) if options_site_path.exists() else None
    if fmt < 2:
        notes += [f"data/site.toml: {n}" for n in formats.site_1(site)]
        if options_site is not None:
            changed = formats.options_site_1(options_site)
            if changed:
                docs[options_site_path] = options_site
                notes += [f"options/_site.toml: {n}" for n in changed]
        frame = options_site.get("frame") if options_site is not None else None
        for path in option_files(root):
            doc = tomlkit.parse(path.read_text(encoding="utf-8"))
            changed = formats.option_1(doc, frame)
            if changed:
                docs[path] = doc
                notes += [f"options/{path.name}: {n}" for n in changed]
    if fmt < 3:
        notes += [f"data/site.toml: {n}" for n in formats.site_2(site)]
    if fmt < 4:
        notes += [f"data/site.toml: {n}" for n in formats.site_3(site)]
    if "domus" in site:
        site["domus"]["format"] = formats.FORMAT
    else:
        site.add(tomlkit.nl())
        site.add("domus", tomlkit.table())
        site["domus"].add("format", formats.FORMAT)
        site["domus"]["format"].comment("this site folder's format: `domus site upgrade` moves an older one on")
    notes.append(f"data/site.toml: [domus] format = {formats.FORMAT}")
    return fmt, {path: tomlkit.dumps(doc) for path, doc in docs.items()}, notes


def verify(root, fmt, writes):
    """Raises a DomusError unless each new file says what Domus read from the old one, in memory."""
    old = lambda p: tomllib.loads(p.read_text(encoding="utf-8"))
    site_path, options_site_path = root / "data" / "site.toml", root / "options" / "_site.toml"
    frame = formats.options_site(old(options_site_path), fmt).get("frame") if options_site_path.exists() else None
    for path, text in writes.items():
        if path == site_path:
            expected = formats.site(old(path))
        elif path == options_site_path:
            expected = formats.options_site(old(path), fmt)
        else:
            expected = formats.option(old(path), fmt, frame)
        if tomllib.loads(text) != expected:
            raise DomusError(f"Upgrading {path.relative_to(root)} would change what it says, so nothing was written. "
                             "This is a fault in Domus: please report it.")


def upgrade(root, dry_run=False, say=print):
    """`domus site upgrade`: writes the site's migrations into its files, keeping the files as they were in
    .domus/upgrade/; with `dry_run`, only says what would change. What it does goes to `say`."""
    root = Path(root).resolve()
    if not (root / "data" / "site.toml").exists():
        raise DomusError(f"{root} isn't a site project: it has no data/site.toml. domus new or domus create starts one.")
    fmt, writes, notes = plan(root)
    if fmt >= formats.FORMAT:
        say(f"{root} is in format {fmt} already: there's nothing to upgrade.")
        return 0
    verify(root, fmt, writes)
    say(f"{root}, format {fmt} to {formats.FORMAT}:")
    for note in notes:
        say(f"  {note}")
    if dry_run:
        say("Nothing written (--dry-run).")
        return 0
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    backup = root / ".domus" / "upgrade" / f"format-{fmt}-{stamp}"
    for path in writes:
        (backup / path.relative_to(root)).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup / path.relative_to(root))
    for path, text in writes.items():
        path.write_text(text, encoding="utf-8", newline="")
    say(f"Written. The files as they were are in {backup}.")
    return 0
