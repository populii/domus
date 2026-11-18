"""`domus prep <step>`: each step's arguments, read here, and its call into the public API, a site's `prep` (domus.api's
Prep, provisional), or for `check`, the site's own `check()`. docs/from-documents.md says what each is for."""

import time
from pathlib import Path

STEPS = {
    "plan-pages": "Pull the page scans out of a PDF",
    "plan-georef": "Pin scanned sheets to the grid by their grid crosses",
    "plan-trace": "Trace a vector PDF's linework onto the grid",
    "ifc": "A designer's IFC model on the grid",
    "feature-textures": "Cut fence, shed and paving textures from photos",
    "wall-textures": "Cut wall textures from photos",
    "house-textures": "Rectify house walls from photos and placed video",
    "boards": "The weatherboards' spacing and lap lines' heights on a rectified wall photo",
    "check": "Adversarial checks of the model against its data, the LiDAR and placed video",
    "sections": "Cross-sections through the point clouds",
    "profile": "The ground along a line, and either side of it",
    "cloud": "A placed cloud's ground against the model's or the LiDAR's, cell by cell",
    "cloud-plan": "A placed cloud from above, in its own colours",
    "face-fit": "Fit vertical faces in a placed cloud, against the model's",
    "frame-points": "Where a placed frame's pixels meet a wall's plane or a level",
    "overlay": "The model's parts drawn over frames of a placed clip, to check their places",
    "canopy-plot": "A plan of candidate tree crowns over the aerial",
}


def add(steps, command):
    """The prep steps, under `steps` (the subparsers of `domus prep`), each made by the command line's `command`."""

    def step(name, run, description):
        return command(steps, name, run, description, STEPS[name])

    def reads_prep_json(name, run, what, section):
        step(name, run, f'{what}, as "{section}" in data/prep.json says (docs/from-documents.md).')

    reads_prep_json("plan-pages", _plan_pages, "The embedded page scans out of a PDF, without re-encoding", "plans")
    reads_prep_json("plan-georef", _plan_georef, "A scanned sheet's affine fit from pixels to the grid, from the grid "
                    "crosses printed on it", "plans")
    reads_prep_json("plan-trace", _plan_trace, "A vector sheet's linework and text, as GeoJSON on the grid", "plans")
    reads_prep_json("ifc", _ifc, "A designer's IFC model of the house on the site grid", "ifc")

    sub = step("feature-textures", _feature_textures, "Cut fence, shed and paving textures from photos.")
    sub.add_argument("--check", help="directory for the sources with their quads drawn on")

    sub = step("wall-textures", _wall_textures, "Cut wall textures from photos.")
    sub.add_argument("--check", help="directory for the frames with their quads drawn on")

    sub = step("house-textures", _house_textures, 'Rectify house walls from photos and placed video, as "house_textures" in '
               "data/prep.json says (docs/from-documents.md).")
    sub.add_argument("groups", metavar="group", nargs="*", help="the sources to rectify, by name (default: all of them)")

    sub = step("boards", _boards, "The weatherboards' spacing on a rectified photo of a wall: how far apart their lap lines "
               "are up the wall, and the lap lines' heights. A check of [house] board_m, and of where the boards are "
               "counted from.")
    sub.add_argument("faces", nargs="*", help="faces in data/house/faces.json (all of them unless named)")
    sub.add_argument("--along", type=float, nargs=2, metavar=("U0", "U1"),
                     help="the part of the face to read, in metres along it (all of it unless given)")
    sub.add_argument("--band", type=float, default=0.5, metavar="WIDTH",
                     help="the bands' width along the face, in metres (0: one band)")
    sub.add_argument("--heights", type=float, nargs=2, metavar=("Z0", "Z1"),
                     help="the heights to read between (the photo's whole height unless given)")
    sub.add_argument("--spacing", type=float, nargs=2, default=[0.08, 0.25], metavar=("MIN", "MAX"),
                     help="the range of spacings to try, in metres")
    sub.add_argument("--step", type=float, default=0.0002, help="metres between the spacings tried")
    sub.add_argument("--above", type=float, metavar="HEIGHT", help="also give the first lap line at or above this height")

    sub = step("check", _check, "Adversarial checks of a site's model against its own data, the LiDAR and the placed "
               "videos.")
    sub.add_argument("checks", nargs="*", help="some of sanity, roof, plumb, clouds, through, fixes and blocks (default: all)")
    sub.add_argument("--tags", nargs="*", help="placed reconstructions for plumb and clouds (default: all)")
    sub.add_argument("--plots", action="store_true")

    sub = step("sections", _sections, "Cross-sections through the point clouds along a reference line, for reading by eye.")
    sub.add_argument("clouds", nargs="+")
    sub.add_argument("--step", type=float, default=1.0)
    sub.add_argument("--from", dest="s_from", type=float, default=-6.0)
    sub.add_argument("--to", type=float, default=16.0)

    sub = step("profile", _profile, "The ground along a line, and either side of it: for reading levels off the model "
               "without the viewer.")
    sub.add_argument("points", help='the line\'s points, "s,o s,o ..." (or "east,north ..." with --grid)')
    sub.add_argument("--offsets", type=float, nargs="+", default=[0.0], help="metres across the line (left negative)")
    sub.add_argument("--step", type=float, default=0.5, help="metres between stations (0: the line's points only)")
    sub.add_argument("--grid", action="store_true", help="the points are on the site's grid, not the options' frame")
    sub.add_argument("--csv", help="also write the table to this file")

    sub = step("cloud", _cloud, "A placed cloud's ground against the model's and the LiDAR's, cell by cell, and the cloud "
               "seen from above.")
    sub.add_argument("clouds", help="the clouds, <tag>-site or <tag>-dense in data/video (or .npz files), comma-separated")
    sub.add_argument("box", help='the box\'s two corners, "s,o s,o" (or "east,north east,north" with --grid)')
    sub.add_argument("--cell", type=float, nargs="+", default=[1.0], help="the cells' size in metres (or along, across)")
    sub.add_argument("--pct", type=float, default=20.0, help="the percentile of each cell's points taken as its ground")
    sub.add_argument("--min", type=int, default=12, dest="nmin", help="the fewest points a cell needs")
    sub.add_argument("--by", choices=("points", "level"), default="points",
                     help="each point's height over the ground, or the cell's level over the ground under its lowest points")
    sub.add_argument("--against", choices=("model", "lidar"), default="model",
                     help="the model's ground with the site's fixes, or the LiDAR's ground model alone")
    sub.add_argument("--all", action="store_true", help="every point of a -site cloud, not only those three cameras saw")
    sub.add_argument("--grid", action="store_true", help="the box is on the site's grid, not the options' frame")
    sub.add_argument("--csv", help="also write the table to this file")

    sub = step("cloud-plan", _cloud_plan, "A placed cloud from above, in its own colours.")
    sub.add_argument("clouds", help="the clouds, <tag>-site or <tag>-dense in data/video (or .npz files), comma-separated")
    sub.add_argument("box", help='the box\'s two corners, "s,o s,o" (or "east,north east,north" with --grid)')
    sub.add_argument("out", help="the picture to write (.png or .jpg)")
    sub.add_argument("--px", type=float, default=0.02, help="metres a pixel covers")
    sub.add_argument("--mode", choices=("top", "low", "height"), default="top",
                     help="each pixel's highest point's colour, its lowest's, or grey by its height over the ground")
    sub.add_argument("--above", type=float, help="leave out points more than this many metres over the ground")
    sub.add_argument("--lines", action="store_true", help="draw the boundary, the existing walls and the house")
    sub.add_argument("--all", action="store_true", help="every point of a -site cloud, not only those three cameras saw")
    sub.add_argument("--grid", action="store_true", help="the box is on the site's grid, not the options' frame")

    sub = step("face-fit", _face_fit, "Fit vertical faces in a placed cloud.")
    sub.add_argument("cloud", help="the cloud: <tag>-dense or <tag>-site in data/video, or an .npz file")
    sub.add_argument("spec", help="the faces: a JSON file of name: {box, along, model}")
    sub.add_argument("--all", action="store_true", help="every point of a -site cloud, not only those three cameras saw")

    sub = step("frame-points", _frame_points, "Where a placed frame's pixels meet a plane.")
    sub.add_argument("tag", help="the placed reconstruction, data/video/<TAG>-sfm.npz and <TAG>-georef.json")
    sub.add_argument("spec", help="the frames: a JSON list of {frame, plane, points}")
    sub.add_argument("--georef", help="another placement of it than data/video/<TAG>-georef.json")

    sub = step("overlay", _overlay, "The model's parts drawn over frames of a placed clip, through the clip's own cameras, "
               "to check each one's place.")
    sub.add_argument("tag", help="the placed reconstruction, data/video/<TAG>-sfm.npz")
    sub.add_argument("out", help="the folder to write the frames to")
    sub.add_argument("frames", nargs="+", help="frames to draw on, such as t012.500")
    sub.add_argument("--frames-dir", "--frames", dest="frames_dir", help="where the clip's frames are (default "
                     "sources/raw/frames6/<clip>/)")
    sub.add_argument("--georef", help="another placement of the reconstruction than data/video/<TAG>-georef.json")
    sub.add_argument("--only", help="some of house, walls, features, ground, boundary, plants, comma-separated")
    sub.add_argument("--option", help="a wall option to draw too, by its file's name in options/")
    sub.add_argument("--see-through", action="store_true", help="draw the house's faces turned away from the camera too")
    sub.add_argument("--suffix", default="", help="added to each picture's name")

    sub = step("canopy-plot", _canopy_plot, "A plan of the LiDAR's candidate tree crowns over the aerial, for curating "
               "data/site/trees.geojson.")
    sub.add_argument("out", nargs="?", default="data/sources/canopy.jpg",
                     help="the picture to write, in the site's folder (default: data/sources/canopy.jpg)")


def listed(args, site, named):
    """`domus prep` with no step: the steps."""
    print("usage: domus prep <step> [args]\n\nSteps:")
    for name, what in STEPS.items():
        print(f"  {name:18} {what}")
    return 0


def prep(site):
    """The steps on the site the command line was given, through the public API."""
    from .cli import opened

    return opened(site).prep


def _plan_pages(args, site, named):
    prep(site).plan_pages()


def _plan_georef(args, site, named):
    prep(site).plan_georef()


def _plan_trace(args, site, named):
    prep(site).plan_trace()


def _ifc(args, site, named):
    prep(site).ifc()


def _feature_textures(args, site, named):
    prep(site).feature_textures(check=args.check)


def _wall_textures(args, site, named):
    prep(site).wall_textures(check=args.check)


def _house_textures(args, site, named):
    prep(site).house_textures(groups=args.groups)


def _boards(args, site, named):
    prep(site).boards(faces=args.faces, along=args.along, band=args.band, heights=args.heights, spacing=args.spacing,
                      step=args.step, above=args.above)
    return 0


def _check(args, site, named):
    """`domus prep check`: the site's checks and what they found, failing (1) when one fails."""
    from .cli import opened

    report = opened(site).check(args.checks, args.tags, args.plots)
    for f in report["findings"]:
        print(f"{f['check']:7s} {f['level']:9s} {f['object']:40s} {f['detail']}")
    for name, why in report["skipped"].items():
        print(f"{name:7s} skipped   {why}")
    print(f"[{time.strftime('%H:%M:%S')}] failures by check: {report['failures']}; wrote "
          f"{Path('.domus') / 'checks' / 'check_model.json'}", flush=True)
    return 1 if any(report["failures"].values()) else 0


def _sections(args, site, named):
    prep(site).sections(args.clouds, step=args.step, from_=args.s_from, to=args.to)


def _profile(args, site, named):
    prep(site).profile(args.points, offsets=args.offsets, step=args.step, grid=args.grid, csv=args.csv)
    return 0


def _cloud(args, site, named):
    prep(site).cloud(args.clouds, args.box, cell=args.cell, pct=args.pct, min_=args.nmin, by=args.by, against=args.against,
                     all_=args.all, grid=args.grid, csv=args.csv)
    return 0


def _cloud_plan(args, site, named):
    prep(site).cloud_plan(args.clouds, args.box, args.out, px=args.px, mode=args.mode, above=args.above, lines=args.lines,
                          all_=args.all, grid=args.grid)
    return 0


def _face_fit(args, site, named):
    prep(site).face_fit(args.cloud, args.spec, all_=args.all)
    return 0


def _frame_points(args, site, named):
    prep(site).frame_points(args.tag, args.spec, georef=args.georef)
    return 0


def _overlay(args, site, named):
    prep(site).overlay(args.tag, args.out, args.frames, frames_dir=args.frames_dir, georef=args.georef, only=args.only,
                       option=args.option, see_through=args.see_through, suffix=args.suffix)
    return 0


def _canopy_plot(args, site, named):
    prep(site).canopy_plot(args.out)
