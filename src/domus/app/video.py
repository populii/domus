"""`domus video <step>`: each step's arguments, read here, and its call into the public API, a site's `video` (domus.api's
Video, provisional). docs/video.md says what each is for, and each step's module in domus.capture how it works."""

import os

STEPS = {
    "auto": "From a scan to the model, with no hand work",
    "frames": "Pull the sharpest frames out of a video",
    "reconstruct": "Reconstruct a clip with COLMAP (the one to start with)",
    "reconstruct-chain": "Reconstruct a clip with the built-in chain of views",
    "two-lens": "Map a clip filmed on two lenses again, with a camera for each",
    "merge": "Join two reconstructions of a clip that share frames",
    "split": "Cut a time span out of a reconstruction",
    "link": "Find what carries one reconstruction into another's frame",
    "link-picks": "Link two reconstructions through points marked in both",
    "place": "Place a reconstruction on the site's grid",
    "pick": "Put points marked in frames on the grid",
    "dense": "Dense point cloud from a placed reconstruction",
    "export": "Combine the clouds in [video] clouds for the build",
    "plot": "Plan view of a cloud over the aerial, for checking",
    "sheet": "Contact sheets of a clip's frames, for finding the ones to mark",
    "crop": "A piece of a photo at full size, from a smaller copy's corners",
    "picks-edit": "Add, move or drop marks in a picks file, keeping a backup",
}


def add(steps, command):
    """The video steps, under `steps` (the subparsers of `domus video`), each made by the command line's `command`."""

    def step(name, run, description):
        return command(steps, name, run, description, STEPS[name])

    sub = step("auto", _auto, "From a scan to the model, with no hand work.")
    sub.add_argument("video")

    sub = step("frames", _frames, "Extract review and reconstruction frames from a site video.")
    sub.add_argument("video")
    sub.add_argument("--fps", type=float, default=6.0)

    sub = step("reconstruct", _reconstruct, "Reconstruct a site video with COLMAP (pycolmap) instead of domus.sfm.")
    sub.add_argument("clip")
    sub.add_argument("--step", type=int, default=1, help="use every n-th frame")
    sub.add_argument("--extra", default="", help="comma-separated clips whose frames are added")
    sub.add_argument("--extra-step", type=int, default=3, help="use every n-th frame of the extra clips")
    sub.add_argument("--focal", type=float, default=1100.0,
                     help="start focal length, px: about 1100 main camera, 630-650 wide lens at 1440 x 1080")
    sub.add_argument("--tag", default="", help="output name (default <clip>-colmap)")
    sub.add_argument("--threads", type=int, default=8)
    sub.add_argument("--overlap", type=int, default=12,
                     help="sequential matching: the next n frames, then the frames 2^k on")
    sub.add_argument("--retrieve", type=int, default=5,
                     help="revisit pairs per frame from image retrieval (0: none)")
    sub.add_argument("--max-features", type=int, default=8192,
                     help="SIFT features per frame at most (COLMAP may give one two orientations)")
    sub.add_argument("--first-octave", type=int, default=0,
                     help="0: features from the frame as it is (2900-6300 per 1440 x 1080 frame); -1 "
                          "(COLMAP's own default) also from the frame doubled in size: more points, "
                          "about three times the time (--max-features 4096 then keeps about 6800)")
    sub.add_argument("--mapper", choices=["global", "incremental"], default="global")
    sub.add_argument("--min-matches", type=int, default=30,
                     help="mapping uses only frame pairs with this many verified matches (video_sfm.py "
                          "also needs 30; with COLMAP's own 15 the global mapper joined clip VIDEO "
                          "to VIDEO by pairs of 15-23 matches, and two runs put it 2.5 m apart)")
    sub.add_argument("--seed", type=int, default=0)
    sub.add_argument("--fresh", action="store_true", help="rebuild the database from scratch")

    sub = step("reconstruct-chain", _reconstruct_chain, "Reconstruct a walk round the site in 3D from its video.")
    sub.add_argument("clip")
    sub.add_argument("--step", type=int, default=1, help="use every n-th frame")
    sub.add_argument("--window", type=int, default=6, help="match each frame with the next n")
    sub.add_argument("--focal", type=float, default=1100.0)
    sub.add_argument("--k1", type=float, default=0.0)
    sub.add_argument("--fix-intrinsics", action="store_true",
                     help="keep --focal and --k1 (from a well-conditioned clip) instead of refining them")
    sub.add_argument("--extra", default="", help="comma-separated clips whose frames are added to the map")
    sub.add_argument("--extra-step", type=int, default=3, help="use every n-th frame of the extra clips")
    sub.add_argument("--range", default="", help="first:last frame index of the main clip to use")
    sub.add_argument("--tag", default="", help="output name (default: the clip, plus the range if given)")
    sub.add_argument("--no-loops", dest="loops", action="store_false",
                     help="skip the search for frames that revisit a place")
    sub.add_argument("--loop-candidates", type=int, default=12,
                     help="most similar frames outside the window to match for each frame (on clip "
                          "VIDEO, 12 leave 1 of the old exhaustive search's 246 revisit pairs "
                          "without a found pair within 2 frames; 5 leave 9)")
    sub.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 4),
                     help="processes for features and matching")

    sub = step("two-lens", _two_lens, "Map a clip filmed on two lenses, from the workspace `domus video reconstruct` left "
               "behind.")
    sub.add_argument("clip")
    sub.add_argument("work_tag", help="the reconstruction's workspace, data/video/cache/colmap/<WORK_TAG>")
    sub.add_argument("t_main", type=float, help="the last second on the main camera")
    sub.add_argument("t_wide", type=float, help="the first second on the wide one")
    sub.add_argument("f_main", type=float, help="the main camera's focal length, in pixels")
    sub.add_argument("f_wide", type=float, help="the wide camera's")
    sub.add_argument("--stage", default="all", help="some of copy, cameras, verify, map, convert (default all)")
    sub.add_argument("--mapper", choices=("global", "incremental"), default="global")
    sub.add_argument("--threads", type=int, default=8)
    sub.add_argument("--min-matches", type=int, default=30)
    sub.add_argument("--every", type=int, help="map only every N-th frame, and those in --keep")
    sub.add_argument("--keep", help="a file of frame names to map whatever --every says, one a line")
    sub.add_argument("--ranges", help="map only the frames in these stretches of seconds: 0-240,470-575")
    sub.add_argument("--name", default="", help="a name for a mapping of part of the clip: its own folder and tags")
    sub.add_argument("--model", help="convert this model rather than the largest")
    sub.add_argument("--as", dest="as_", help="the name the converted model's files take, with --model")

    sub = step("merge", _merge, "Join two reconstructions of one clip that share frames, then re-adjust them as one.")
    sub.add_argument("clip", metavar="CLIP", help="the clip both were reconstructed from")
    sub.add_argument("a_tag", metavar="TAG_A", help="the reconstruction that keeps its frame")
    sub.add_argument("b_tag", metavar="TAG_B", help="the reconstruction carried into it")
    sub.add_argument("out_tag", metavar="OUT", nargs="?", help="the joined reconstruction's tag (default: CLIP-joined)")

    sub = step("split", _split, "Cut a time span out of a video reconstruction as its own model (same frame, cameras and "
               "points).")
    sub.add_argument("src", metavar="TAG", help="the reconstruction to cut from")
    sub.add_argument("new", metavar="NEW", help="the new reconstruction's tag")
    sub.add_argument("t0", metavar="T0", type=float, help="the span's start, seconds into the clip")
    sub.add_argument("t1", metavar="T1", type=float, help="its end, seconds into the clip")

    sub = step("link", _link, "Find the similarity that carries one video reconstruction into another's frame.")
    sub.add_argument("a_tag", metavar="TAG_A", help="the reconstruction whose frame the other is carried into")
    sub.add_argument("b_tag", metavar="TAG_B", help="the reconstruction carried into it")

    sub = step("link-picks", _link_picks, "Link two video reconstructions through points marked in both clips' frames.")
    sub.add_argument("a_tag", metavar="TAG_A", help="the reconstruction whose frame the other is carried into")
    sub.add_argument("a_picks", metavar="PICKS_A", help="points marked in its clip's frames, a picks file")
    sub.add_argument("b_tag", metavar="TAG_B", help="the reconstruction carried into it")
    sub.add_argument("b_picks", metavar="PICKS_B", help="the same points marked in its clip's frames")

    sub = step("place", _place, "Place a video reconstruction on the site grid.")
    sub.add_argument("tag")
    sub.add_argument("--via", help="a placed model this one is linked to (tools/link_sfm.py), instead of control")

    sub = step("pick", _pick, "Triangulate points marked in video frames and place them on the site grid.")
    sub.add_argument("picks", help="the picks file, data/video/picks/NAME.json")
    sub.add_argument("tags", metavar="TAG", nargs="*", help="the placed reconstructions to triangulate with, first first")

    sub = step("dense", _dense, "Dense point cloud from a placed video reconstruction, by two-view stereo on frame pairs.")
    sub.add_argument("tag")
    sub.add_argument("--every", type=int, default=2)
    sub.add_argument("--scale", type=float, default=0.5)

    step("export", _export, "Combine the georeferenced video point clouds into one file for the model and the viewer.")

    sub = step("plot", _plot, "Plan view of georeferenced video point clouds over the aerial, for checking.")
    sub.add_argument("tags", nargs="+")
    sub.add_argument("--out", default="")

    sub = step("sheet", _sheet, "Contact sheets of a clip's frames between two times, each frame's name on it, for finding "
               "the frames to mark.")
    sub.add_argument("frames", help="the clip's frames folder (t<seconds>.jpg)")
    sub.add_argument("prefix", help="the sheets' names: PREFIX-1.jpg, PREFIX-2.jpg, ...")
    sub.add_argument("t0", type=float, help="from this many seconds into the clip")
    sub.add_argument("t1", type=float, help="to this many")
    sub.add_argument("step", type=float, help="seconds between the frames taken")
    sub.add_argument("--cols", type=int, default=6)
    sub.add_argument("--width", type=int, default=480, help="each frame's width on the sheet, in pixels")
    sub.add_argument("--rows", type=int, default=3)
    sub.add_argument("--registered", help="a reconstruction (<tag>-sfm.npz): only the frames it placed")

    sub = step("crop", _crop, "A piece of a photo at full size, its corners read off a smaller copy, turned and enlarged to "
               "read detail.")
    sub.add_argument("photo")
    sub.add_argument("out")
    sub.add_argument("side", type=float, help="the long side, in pixels, of the copy the corners were read on")
    sub.add_argument("box", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"))
    sub.add_argument("--zoom", type=float, default=1.0)
    sub.add_argument("--rotate", type=float, default=0.0, help="degrees anticlockwise")

    sub = step("picks-edit", _picks_edit, "Add, move or drop the marks in a picks file (data/video/picks/<clip>.json), "
               "keeping its layout and a backup.")
    sub.add_argument("picks", help="the picks file, data/video/picks/<clip>.json")
    sub.add_argument("edits", help="what to change: a JSON file of feature id: {desc, set, drop}")


def listed(args, site, named):
    """`domus video` with no step: the steps."""
    print("usage: domus video <step> [args]\n\nSteps, each with its own --help:")
    for name, what in STEPS.items():
        print(f"  {name:18} {what}")
    return 0


def video(site):
    """The steps on the site the command line was given, through the public API."""
    from .cli import opened

    return opened(site).video


def anywhere(site):
    """The steps that work on the files they're given, which need no site project."""
    from .. import api

    return api.Site(site).video


def clips(text):
    """--extra's comma-separated clips."""
    return [c for c in text.split(",") if c]


def _auto(args, site, named):
    video(site).auto(args.video)


def _frames(args, site, named):
    video(site).frames(args.video, fps=args.fps)


def _reconstruct(args, site, named):
    video(site).reconstruct(args.clip, step=args.step, extra=clips(args.extra), extra_step=args.extra_step, focal=args.focal,
                            tag=args.tag, threads=args.threads, overlap=args.overlap, retrieve=args.retrieve,
                            max_features=args.max_features, first_octave=args.first_octave, mapper=args.mapper,
                            min_matches=args.min_matches, seed=args.seed, fresh=args.fresh)


def _reconstruct_chain(args, site, named):
    video(site).reconstruct_chain(args.clip, step=args.step, window=args.window, focal=args.focal, k1=args.k1,
                                  fix_intrinsics=args.fix_intrinsics, extra=clips(args.extra), extra_step=args.extra_step,
                                  range_=args.range, tag=args.tag, loops=args.loops, loop_candidates=args.loop_candidates,
                                  workers=args.workers)


def _two_lens(args, site, named):
    video(site).two_lens(args.clip, args.work_tag, args.t_main, args.t_wide, args.f_main, args.f_wide, stage=args.stage,
                         mapper=args.mapper, threads=args.threads, min_matches=args.min_matches, every=args.every,
                         keep=args.keep, ranges=args.ranges, name=args.name, model=args.model, as_=args.as_)


def _merge(args, site, named):
    video(site).merge(args.clip, args.a_tag, args.b_tag, out=args.out_tag)


def _split(args, site, named):
    video(site).split(args.src, args.new, args.t0, args.t1)


def _link(args, site, named):
    video(site).link(args.a_tag, args.b_tag)


def _link_picks(args, site, named):
    video(site).link_picks(args.a_tag, args.a_picks, args.b_tag, args.b_picks)


def _place(args, site, named):
    video(site).place(args.tag, via=args.via)


def _pick(args, site, named):
    video(site).pick(args.picks, args.tags)


def _dense(args, site, named):
    video(site).dense(args.tag, every=args.every, scale=args.scale)


def _export(args, site, named):
    video(site).export()


def _plot(args, site, named):
    video(site).plot(args.tags, out=args.out)


def _sheet(args, site, named):
    anywhere(site).sheet(args.frames, args.prefix, args.t0, args.t1, args.step, cols=args.cols, width=args.width,
                         rows=args.rows, registered=args.registered)


def _crop(args, site, named):
    anywhere(site).crop(args.photo, args.out, args.side, args.box, zoom=args.zoom, rotate=args.rotate)


def _picks_edit(args, site, named):
    anywhere(site).picks_edit(args.picks, args.edits)
