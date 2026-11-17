"""The `domus` command.

    domus create "12 Example Street, Christchurch"              build a model from an address and public data
    domus new PATH --name "My place" --centre -43.53,172.63   start a site project
    domus demo PATH                                           write the made-up demo site
    domus fetch                                               get its ground and aerial from LINZ
    domus canopy                                              map tree crowns from the LiDAR
    domus marks                                               add LINZ's survey marks around the lot
    domus title                                               add the lot's title and nearby easements from LINZ
    domus points [--ground]                                   get the classified LiDAR point cloud over the site
    domus build                                               build the model
    domus video <step>                                        turn site videos into point clouds
    domus scan FILE                                           bring in a phone LiDAR scan
    domus prep <step>                                         plans, IFC, textures, checks from your documents
    domus serve                                               serve the viewer
    domus site upgrade                                        write an older site's migrations into its files
    domus site shift|edit SPEC BACKUP [--apply]               change the site's layers, keeping what was there
    domus site regrade CLOUD SPEC BACKUP [--apply]            give ground fixes the levels a placed cloud shows

Every command but `new` and `demo` works on the site project in the current folder, or the one
named by --site or the DOMUS_SITE environment variable, which Domus reads the first time it needs
the site (domus.project.site).
"""

import argparse
import os
import sys

from . import __version__

COMMANDS = ["create", "new", "demo", "fetch", "canopy", "marks", "title", "points", "sketch-worker", "build", "serve", "video", "scan", "prep",
            "site"]


def choose_site(path):
    """--site is the whole project, so its build goes there too, whatever a container's DOMUS_OUTPUT says."""
    os.environ["DOMUS_SITE"] = path
    os.environ.pop("DOMUS_OUTPUT", None)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):     # NZ addresses have macrons, which a Windows console's encoding can't print
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog="domus", description="3D site models for comparing retaining wall options.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--site", help="the site project's folder (default: DOMUS_SITE, or the current folder)")
    ap.add_argument("command", choices=COMMANDS)
    # Everything after the command is the command's own, --help included.
    k = next((i for i, a in enumerate(argv) if a in COMMANDS), len(argv))
    args, rest = ap.parse_args(argv[:k + 1]), argv[k + 1:]
    if args.site:
        choose_site(args.site)
    from .errors import as_command

    return as_command(lambda: run(args.command, rest))


def run(command, rest):
    """One command, on the site already chosen. Modules are imported here, once the site is known."""
    if command == "create":
        from . import create
        return create.main(rest)
    if command == "new":
        from . import newsite
        return newsite.main(rest)
    if command == "demo":
        from . import demo
        return demo.main(rest)
    if command == "serve":
        from . import serve
        return serve.main(rest)
    if command == "fetch":
        from . import importers
        return importers.fetch()
    if command == "canopy":
        from . import canopy
        return canopy.map_site()
    if command == "sketch-worker":
        from . import sketch
        return sketch.main()
    if command == "marks":
        from . import marks
        return marks.main(rest)
    if command == "title":
        from . import title
        return title.main(rest)
    if command == "points":
        from . import points
        return points.main(rest)
    if command == "video":
        from . import video
        return video.main(rest)
    if command == "prep":
        from . import prep
        return prep.main(rest)
    if command == "scan":
        from . import scan
        return scan.main(rest)
    if command == "site":
        from .project import upgrade
        return upgrade.main(rest)
    from . import build
    return build.main(rest)


if __name__ == "__main__":
    sys.exit(main())
