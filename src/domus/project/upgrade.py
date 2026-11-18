"""`domus site upgrade`: writes a site's migrations into its own files, so they say what this Domus reads.

    domus site upgrade [--dry-run]

Domus reads an older site as it is, migrating it in memory, so a site never has to be upgraded to be used. Upgrading
is for editing it: its files then say what the model is built from, in this Domus's terms. The files that change keep
their comments, are copied to .domus/upgrade/ first, and are checked to say just what the site said in memory.
"""

import argparse
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


def upgrade(root, dry_run=False):
    root = Path(root).resolve()
    if not (root / "data" / "site.toml").exists():
        raise DomusError(f"{root} isn't a site project: it has no data/site.toml.")
    fmt, writes, notes = plan(root)
    if fmt >= formats.FORMAT:
        print(f"{root} is in format {fmt} already: there's nothing to upgrade.")
        return 0
    verify(root, fmt, writes)
    print(f"{root}, format {fmt} to {formats.FORMAT}:")
    for note in notes:
        print(f"  {note}")
    if dry_run:
        print("Nothing written (--dry-run).")
        return 0
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    backup = root / ".domus" / "upgrade" / f"format-{fmt}-{stamp}"
    for path in writes:
        (backup / path.relative_to(root)).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup / path.relative_to(root))
    for path, text in writes.items():
        path.write_text(text, encoding="utf-8", newline="")
    print(f"Written. The files as they were are in {backup}.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="domus site", description="The site folder itself.")
    sub = ap.add_subparsers(dest="action", required=True)
    up = sub.add_parser("upgrade", help="write the site's migrations into its files, so they say what this Domus reads")
    up.add_argument("--dry-run", action="store_true", help="say what would change, and write nothing")
    tr = sub.add_parser("trust", help="let this machine's builds run the site's extensions, as they are now")
    tr.add_argument("--revoke", action="store_true", help="stop trusting them")
    for action, what in (("shift", "move named features of the site's layers by one vector, saying so in their basis"),
                         ("edit", "change, add or delete named features of the site's layers"),
                         ("regrade", "give ground-fix points the levels a placed cloud shows")):
        ed = sub.add_parser(action, help=what)
        if action == "regrade":
            ed.add_argument("clouds", help="the cloud: <tag>-dense or <tag>-site in data/video, or several, comma-separated")
        ed.add_argument("spec", help="what to change: a JSON file (domus.project.edits says what goes in it)")
        ed.add_argument("backup", help="a fresh folder for the files as they were")
        ed.add_argument("--apply", action="store_true", help="write the changes (without it, only say what they'd be)")
    args = ap.parse_args(argv)
    from ..geo import crs

    if args.action in ("shift", "edit", "regrade"):
        import json

        from . import edits

        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        if args.action == "regrade":
            edits.regrade(args.clouds.split(","), spec, args.backup, args.apply)
        else:
            getattr(edits, args.action)(spec, args.backup, args.apply)
        return 0

    if args.action == "trust":
        from . import extensions

        done = extensions.trust(crs.ROOT, crs.settings(), args.revoke)
        if not done:
            print("This site declares no extensions ([domus] extensions in data/site.toml).")
        for rel, sha in done:
            print(f"{'No longer trusted' if args.revoke else 'Trusted'}: {rel}" + (f" (SHA-256 {sha})" if sha else ""))
        if done and not args.revoke:
            print(f"Builds on this machine will run them until they change. Recorded in {extensions.trust_file()}.")
        return 0
    return upgrade(crs.ROOT, args.dry_run)
