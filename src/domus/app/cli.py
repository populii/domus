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
    domus site shift|edit SPEC [BACKUP] [--apply]             change the site's data, recorded so it can be undone
    domus site regrade CLOUD SPEC [BACKUP] [--apply]          give ground fixes the levels a placed cloud shows
    domus site edits | undo EDIT                              the changes made to the site's data, and undo one
    domus site proposals | accept ID | reject ID --why ...    the changes proposed, to accept or reject

Every command but `new` and `demo` works on the site project in the current folder, or the one
named by --site or the DOMUS_SITE environment variable. The command line reads which, once, and hands
that Site (domus.project.site) to the command.

A command is a call to the public API (domus.api): this module reads the arguments, makes the call, and prints what
the call doesn't say itself; domus.app.video and domus.app.prep do the same for the video and prep steps. `serve`
reads its own arguments. Every command and step prints its usage with --help, and does nothing else.
"""

import argparse
import json
import math
import os
import sys

from .. import __version__


def choose_site(path):
    """--site is the whole project, so its build goes there too, whatever a container's DOMUS_OUTPUT says."""
    os.environ["DOMUS_SITE"] = path
    os.environ.pop("DOMUS_OUTPUT", None)


def parser():
    """The command line: every command and step, each with its own --help. One that's `handed` on takes what follows
    it as it is, and reads it itself."""
    from .. import shots
    from . import prep, video

    ap = argparse.ArgumentParser(prog="domus", description="3D site models for comparing retaining wall options.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--site", help="the site project's folder (default: DOMUS_SITE, or the current folder)")
    ap.set_defaults(handed=False)
    commands = ap.add_subparsers(dest="command", required=True, metavar="command")

    def command(under, name, run, description, what=None, handed=False):
        """A command under `under`, run by run(args, site, named). One without `what` isn't listed: it's the server's."""
        sub = under.add_parser(name, description=description, add_help=not handed, **({"help": what} if what else {}))
        sub.set_defaults(run=run, parser=sub, handed=handed)
        if handed:
            sub.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
        return sub

    sub = command(commands, "create", _create, "Build a 3D model from an address and public data.",
                  "build a model from an address and public data")
    sub.add_argument("address")
    sub.add_argument("path", nargs="?", help="folder for the site project (default: from the address)")

    sub = command(commands, "new", _new, "Start a site project.", "start a site project")
    sub.add_argument("path")
    sub.add_argument("--name", required=True, help="the site's name, shown in the viewer")
    sub.add_argument("--boundary", help="a GeoJSON polygon of the lot")
    sub.add_argument("--centre", help="latitude,longitude of the lot, when you have no boundary yet")
    sub.add_argument("--size", type=float, default=30.0, help="side of the stand-in square, metres")
    sub.add_argument("--epsg", type=int, help="the site's grid, in metres (default: the boundary's own, else one for where it is)")

    sub = command(commands, "demo", _demo, "Write the made-up demo site.", "write the made-up demo site")
    sub.add_argument("path")

    command(commands, "fetch", _fetch, "Get the site's ground, surface and aerial photo from the public source for where "
            "it is: LINZ in New Zealand, the Copernicus ground model elsewhere.", "get its ground and aerial from LINZ")
    command(commands, "canopy", _canopy, "Map candidate tree crowns from the LiDAR, for curating into "
            "data/site/trees.geojson.", "map tree crowns from the LiDAR")

    sub = command(commands, "marks", _marks, "LINZ's survey marks around the lot, as a site layer.",
                  "add LINZ's survey marks around the lot")
    sub.add_argument("--height-mark", help="add this height mark to the layer, by its name as printed")

    command(commands, "title", _title, "The lot's title and the easements around it, from LINZ.",
            "add the lot's title and nearby easements from LINZ")

    sub = command(commands, "points", _points, "The LiDAR point cloud over the site, classified.",
                  "get the classified LiDAR point cloud over the site")
    sub.add_argument("--ground", action="store_true", help="also write a 0.5 m ground model from the ground points to dem-points.tif")

    sub = command(commands, "build", _build, "Build the site's model.", "build the model")
    sub.add_argument("--fresh", action="store_true", help="draw everything again, taking nothing from the last build")

    sub = command(commands, "export", _export, "Write the built model for other software: glTF (.glb) for Blender and 3D "
                  "viewers, or IFC4 (.ifc) for architects' and builders' software.",
                  "the built model as glTF (.glb) or IFC (.ifc), for other software")
    sub.add_argument("path", help="the file to write: .glb for glTF, .ifc for IFC")
    sub.add_argument("--option", help="a wall option's id: its walls, steps, lawns and beds in place of what they replace, "
                                      "as the viewer shows it (default: the site as it is)")
    sub.add_argument("--format", choices=["glb", "ifc"], help="the format, if the path's suffix doesn't say it")

    sub = command(commands, "video", video.listed, "Site videos into 3D point clouds on the site's grid. See docs/video.md.",
                  "turn site videos into point clouds")
    video.add(sub.add_subparsers(dest="video_step", metavar="step"), command)

    sub = command(commands, "scan", _scan, "Bring in a phone LiDAR scan as a point cloud.", "bring in a phone LiDAR scan")
    sub.add_argument("file")
    sub.add_argument("--tag", required=True, help="a short name for this scan")
    how = sub.add_mutually_exclusive_group(required=True)
    how.add_argument("--control", help="CSV with x,y,z,E,N,H for points in both the scan and on the grid")
    how.add_argument("--epsg", type=int, help="the file is already on this grid")
    sub.add_argument("--up", choices=["y", "z"], help="the scan's vertical axis, taken as true")

    sub = command(commands, "prep", prep.listed, "Turning a site's documents and photos into its data. See "
                  "docs/from-documents.md.", "plans, IFC, textures, checks from your documents")
    prep.add(sub.add_subparsers(dest="prep_step", metavar="step"), command)

    command(commands, "serve", _serve, "Serve the viewer and the built model.", "serve the viewer", handed=True)

    sub = command(commands, "shots", _shots, "Pictures of the viewer, from a spec (docs/shots.md).",
                  "pictures of the viewer, from a spec (docs/shots.md)")
    sub.add_argument("spec", help="the spec, a JSON file")
    sub.add_argument("--timeout", type=float, default=shots.TIMEOUT_S,
                     help=f"seconds the whole run may take, starting the browser included (default {shots.TIMEOUT_S:g})")

    sub = command(commands, "site", None, "The site folder itself.", "the site folder itself: upgrade it, trust its "
                  "extensions, change its data and undo a change, accept or reject a proposed one")
    actions = sub.add_subparsers(dest="action", required=True)
    up = command(actions, "upgrade", _upgrade, "Write the site's migrations into its files, so they say what this Domus reads.",
                 "write the site's migrations into its files, so they say what this Domus reads")
    up.add_argument("--dry-run", action="store_true", help="say what would change, and write nothing")
    tr = command(actions, "trust", _trust, "Let this machine's builds run the site's extensions, as they are now.",
                 "let this machine's builds run the site's extensions, as they are now")
    tr.add_argument("--revoke", action="store_true", help="stop trusting them")
    for action, what in (("shift", "move named features of the site's layers by one vector, saying so in their basis"),
                         ("edit", "change the site's data: its layers' features, or its settings"),
                         ("regrade", "give ground-fix points the levels a placed cloud shows")):
        ed = command(actions, action, _edit, f"{what[0].upper()}{what[1:]}. Each change is recorded in .domus/edits/, "
                     "and domus site undo undoes it.", what)
        if action == "regrade":
            ed.add_argument("clouds", help="the cloud: <tag>-dense or <tag>-site in data/video, or several, comma-separated")
        ed.add_argument("spec", help="what to change: a JSON file of intents (docs/changes.md)" if action == "edit" else
                        "what to change: a JSON file (domus.project.edits says what goes in it)")
        ed.add_argument("backup", nargs="?", help="a fresh folder for the files as they were (default: beside the change's "
                                                  "record in .domus/edits/)")
        ed.add_argument("--apply", action="store_true", help="write the changes (without it, only say what they'd be)")
        ed.add_argument("--why", help="why: recorded with the change" + ("" if action == "edit" else
                                                                         " (default: the spec's basis)"))
        ed.add_argument("--who", help="who's making it, recorded with it (default: this computer's user)")
    un = command(actions, "undo", _undo, "Undo an edit: put back what it changed, if nothing has changed those files "
                 "since. The undo is an edit too, so it can be undone in turn.", "undo an edit, if nothing has changed it since")
    un.add_argument("edit", help="the edit's number, as domus site edits lists it")
    un.add_argument("--why", help="why: recorded with the undo (default: that it undoes the edit)")
    un.add_argument("--who", help="who's undoing it (default: this computer's user)")
    un.add_argument("--build", action="store_true", help="build the model afterwards, as domus build does")
    command(actions, "edits", _edits, "List the edits made to the site's data, oldest first.",
            "list the edits made to the site's data")
    pr = command(actions, "proposals", _proposals, "List the changes proposed for a person to accept or reject.",
                 "list the changes proposed for a person to accept or reject")
    pr.add_argument("--all", action="store_true", help="also those accepted or rejected already")
    ac = command(actions, "accept", _accept, "Accept a proposed change: apply it, recorded as an edit whose basis is the "
                 "proposal.", "accept a proposed change")
    ac.add_argument("proposal", help="the proposal's number, as domus site proposals lists it")
    ac.add_argument("--who", help="who's accepting it (default: this computer's user)")
    ac.add_argument("--build", action="store_true", help="build the model afterwards, as domus build does")
    rj = command(actions, "reject", _reject, "Reject a proposed change, saying why: the site's data stays as it is.",
                 "reject a proposed change, saying why")
    rj.add_argument("proposal", help="the proposal's number, as domus site proposals lists it")
    rj.add_argument("--why", required=True, help="why it's rejected")
    rj.add_argument("--who", help="who's rejecting it (default: this computer's user)")

    command(commands, "sketch-worker", _sketch_worker, "For the server: a sketch from the viewer per line on stdin, and "
            "what it comes to on the site per line on stdout, as JSON.")
    command(commands, "sketch-option", _sketch_option, "For the server: a sketch from the viewer and its name on stdin, "
            "as JSON, and the wall option's TOML on stdout, as JSON.")
    return ap


def read(argv):
    """The command line's arguments, read: what `run(args, site, named)` runs is `run`."""
    argv = list(argv)
    # A New Zealand latitude starts with a minus, which argparse would read as another option.
    if "--centre" in argv and argv.index("--centre") + 1 < len(argv):
        k = argv.index("--centre")
        argv[k:k + 2] = [f"--centre={argv[k + 1]}"]
    ap = parser()
    args, extras = ap.parse_known_args(argv)
    if args.handed:     # what follows a step handed on is its own, --help included
        args.rest = [*extras, *args.rest]
    elif extras:
        ap.error(f"unrecognized arguments: {' '.join(extras)}")
    return args


def call(argv, site, named=None):
    """A command on `site` (domus.project.site's Site) in this process, its arguments `argv` read as the command line
    reads them: a DomusError comes as it is, where the command line ends with its message. Gives what the command
    gives, its exit code if it has one."""
    args = read(argv)
    return args.run(args, site, named)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):     # NZ addresses have macrons, which a Windows console's encoding can't print
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = read(sys.argv[1:] if argv is None else argv)
    from ..project import site as sites

    if args.site:
        choose_site(args.site)          # for the processes a command starts, which read the environment
        site = sites.start(args.site)
    else:
        site = sites.started()
    named = site if args.site or os.environ.get("DOMUS_SITE") else None
    from ..errors import as_command

    return as_command(lambda: args.run(args, site, named))


def opened(site):
    """The site the command line was given, through the public API: a folder that isn't a site project says so."""
    from .. import api

    return api.open_site(site.path)


def _create(args, site, named):
    """`domus create ADDRESS [PATH]`: into PATH, else the site the command line was given (`named`, by --site or
    DOMUS_SITE), else a folder named from the address."""
    from .. import api

    api.create(args.address, args.path or (named.path if named is not None else None))


def _new(args, site, named):
    if bool(args.boundary) == bool(args.centre):
        args.parser.error("give one of --boundary or --centre")
    from .. import api

    centre = tuple(float(v) for v in args.centre.split(",")) if args.centre else None
    api.new(args.path, args.name, args.boundary or None, centre, args.size, args.epsg)


def _demo(args, site, named):
    from .. import api

    api.demo(args.path)


def _fetch(args, site, named):
    opened(site).fetch()


def _canopy(args, site, named):
    opened(site).map_canopy()


def _marks(args, site, named):
    opened(site).fetch_marks(args.height_mark)


def _title(args, site, named):
    opened(site).fetch_title()


def _points(args, site, named):
    opened(site).fetch_points(args.ground)


def _build(args, site, named):
    opened(site).build(args.fresh)


def _export(args, site, named):
    """`domus export PATH [--option ID] [--format glb|ifc]`: the site's built model for other software. See
    docs/export.md."""
    path = opened(site).export(args.path, format=args.format, option=args.option)
    print(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB)")


def _scan(args, site, named):
    from ..capture import scan

    if not scan.good_tag(args.tag):
        args.parser.error("--tag takes letters, digits, - and _")
    opened(site).scan(args.file, args.tag, args.control, args.epsg, args.up)


def _shots(args, site, named):
    from ..errors import DomusError

    if not (math.isfinite(args.timeout) and args.timeout > 0):
        raise DomusError("--timeout is a number of seconds, more than 0")
    opened(site).shots(args.spec, args.timeout)


def _upgrade(args, site, named):
    opened(site).upgrade(args.dry_run)


def _trust(args, site, named):
    opened(site).trust(args.revoke)


def _edit(args, site, named):
    """`domus site shift|edit SPEC [BACKUP] [--apply]` and `domus site regrade CLOUDS SPEC [BACKUP] [--apply]`."""
    s = opened(site)
    if args.action == "regrade":
        s.regrade(args.clouds.split(","), args.spec, args.backup, args.apply, why=args.why, who=args.who)
    elif args.action == "shift":
        s.shift(args.spec, args.backup, args.apply, why=args.why, who=args.who)
    else:
        s.edit(args.spec, args.why, args.who, backup=args.backup, apply=args.apply)


def _when(stamp):
    return stamp.replace("T", " ").replace("Z", " UTC") if isinstance(stamp, str) else ""


def _undo(args, site, named):
    s = opened(site)
    s.undo(args.edit, args.who, args.why)
    if args.build:
        s.build()


def _edits(args, site, named):
    """`domus site edits`: each edit, oldest first, with who made it, when, why and what it changed."""
    records = opened(site).edits()
    if not records:
        print("No edits have been made to this site's data through its record yet (domus site edit makes one).")
    for r in records:
        state = f"  [undone by {r['undone_by']}]" if not r["in_effect"] else ""
        undoes = f"  [undoes {r['undoes']}]" if r.get("undoes") else ""
        print(f"{r['id']}  {_when(r['when'])}  {r['who']}  {r['why']}{undoes}{state}")
        for line in r.get("summary", []):
            print(f"      {line}")


def _proposals(args, site, named):
    """`domus site proposals [--all]`: the changes waiting for a person, or all of them."""
    found = opened(site).proposals(None if args.all else "open")
    if not found:
        print("No proposed changes" + ("." if args.all else " wait for a person (--all lists those accepted or rejected)."))
    for p in found:
        done = p.get(p["state"], {})
        after = (f" by {done.get('who')}" + (f", edit {done['edit']}" if done.get("edit") else "") +
                 (f": {done['why']}" if done.get("why") else "")) if done else ""
        print(f"{p['id']}  {p['state']}{after}  {p['source']}: {p['why']}")
        for line in p.get("summary", []):
            print(f"      {line}")


def _accept(args, site, named):
    s = opened(site)
    s.accept(args.proposal, args.who)
    if args.build:
        s.build()


def _reject(args, site, named):
    opened(site).reject(args.proposal, args.why, args.who)


def _serve(args, site, named):
    from . import serve

    return serve.main(args.rest)


def _sketch_worker(args, site, named):
    """The server's worker for the site, so its ground is read once: a sketch from the viewer per line on stdin,
    {"items": [...]}, and what it comes to per line on stdout, or {"error": why}."""
    from ..errors import DomusError

    for line in sys.stdin:
        try:
            answer = opened(site)._sketch(json.loads(line)["items"])
        except DomusError as e:     # the site's settings or data need putting right: the message says what
            answer = {"error": str(e)}
        except Exception as e:      # noqa: BLE001 - a sketch that can't be worked out is an answer, not the end of the worker
            answer = {"error": f"{type(e).__name__}: {e}"}
        print(json.dumps(answer, default=float), flush=True)


def _sketch_option(args, site, named):
    """For the server: {"name", "items"}, a sketch from the viewer, on stdin, and on stdout {"toml": the wall option's
    file} or, when the site's settings or data need putting right, {"error": what}."""
    from ..errors import DomusError

    asked = json.load(sys.stdin)
    try:
        answer = {"toml": opened(site)._sketch_option(asked["items"], asked["name"])}
    except DomusError as e:
        answer = {"error": str(e)}
    sys.stdout.write(json.dumps(answer))


if __name__ == "__main__":
    sys.exit(main())
