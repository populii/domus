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
    domus export PATH [--option ID]                           the built model as glTF (.glb) or IFC (.ifc), for other software
    domus video <step>                                        turn site videos into point clouds
    domus scan FILE                                           bring in a phone LiDAR scan
    domus prep <step>                                         plans, IFC, textures, checks from your documents
    domus serve                                               serve the viewer
    domus shots SPEC.json                                     pictures of the viewer, from a spec (docs/shots.md)
    domus site upgrade                                        write an older site's migrations into its files
    domus site shift|edit SPEC BACKUP [--apply]               change the site's layers, keeping what was there
    domus site regrade CLOUD SPEC BACKUP [--apply]            give ground fixes the levels a placed cloud shows

Every command but `new` and `demo` works on the site project in the current folder, or the one
named by --site or the DOMUS_SITE environment variable. The command line reads which, once, and hands
that Site (domus.project.site) to the command.
"""

import argparse
import os
import sys

from .. import __version__

COMMANDS = ["create", "new", "demo", "fetch", "canopy", "marks", "title", "points", "sketch-worker", "build", "export", "serve", "video",
            "scan", "prep", "site", "shots"]


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
    from ..project import site as sites

    if args.site:
        choose_site(args.site)          # for the processes a command starts, which read the environment
        site = sites.start(args.site)
    else:
        site = sites.started()
    named = site if args.site or os.environ.get("DOMUS_SITE") else None
    from ..errors import as_command

    return as_command(lambda: run(args.command, rest, site, named))


def run(command, rest, site, named=None):
    """One command, on `site`, the site the command line was given (`named`, if it was named at all). Modules are
    imported here, once the site is known."""
    if command == "create":
        from .. import create
        return create.main(rest, named)
    if command == "new":
        from ..project import newsite
        return newsite.main(rest)
    if command == "demo":
        from ..project import demo
        return demo.main(rest)
    if command == "serve":
        from . import serve
        return serve.main(rest)
    if command == "shots":
        from .. import shots
        return shots.main(site, rest)
    if command == "fetch":
        from .. import importers
        return importers.fetch(site)
    if command == "canopy":
        from ..model import canopy
        return canopy.map_site(site)
    if command == "sketch-worker":
        from ..options import sketch
        return sketch.main(site)
    if command == "marks":
        from ..importers.nz import marks
        return marks.main(site, rest)
    if command == "title":
        from ..importers.nz import title
        return title.main(site, rest)
    if command == "points":
        from ..importers.nz import points
        return points.main(site, rest)
    if command == "video":
        from .. import capture as video
        return video.main(site, rest)
    if command == "prep":
        from .. import prep
        return prep.main(site, rest)
    if command == "scan":
        from ..capture import scan
        return scan.main(site, rest)
    if command == "site":
        from ..project import upgrade
        return upgrade.main(site, rest)
    if command == "export":
        return export(site, rest)
    from .. import build
    return build.main(site, rest)


def export(site, rest):
    """`domus export PATH [--option ID] [--format glb|ifc]`: the site's built model for other software, through the
    public API (site.export). See docs/export.md."""
    ap = argparse.ArgumentParser(prog="domus export", description="Write the built model for other software: glTF (.glb) "
                                 "for Blender and 3D viewers, or IFC4 (.ifc) for architects' and builders' software.")
    ap.add_argument("path", help="the file to write: .glb for glTF, .ifc for IFC")
    ap.add_argument("--option", help="a wall option's id: its walls, steps, lawns and beds in place of what they replace, "
                                     "as the viewer shows it (default: the site as it is)")
    ap.add_argument("--format", choices=["glb", "ifc"], help="the format, if the path's suffix doesn't say it")
    args = ap.parse_args(rest)
    from .. import api

    path = api.Site(site).export(args.path, format=args.format, option=args.option)
    print(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
