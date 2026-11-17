# From site videos to a point cloud

LiDAR sees little under trees and nothing that changed since the survey. A walk round the site with a phone fills that
in: `domus video` reconstructs the footage in 3D with COLMAP, places it on the site's grid and adds it to the viewer
as a point cloud you can measure against. It needs no GPU, but it's slow on a CPU: under a minute for a few seconds of video, longer for a
long one.

Put videos in `sources/raw/` in the site project. Frames and caches stay there and in `data/video/`; keep them out of git
if they're large.

## Scanning from the app

Open your Domus site on your phone and tap **Scan with this phone** in the Improve this model panel. It needs https, since phones refuse
the camera otherwise.

- **Plain scan:** dots round you show where to point, from the phone's compass and tilt sensors, and warnings tell you to slow down or hold steady.
  Compass readings wander, so treat the dots as a guide.
- **AR tracking**, where the phone offers it (Android Chrome with ARCore): stand at stop 1, face along the route's first leg and start. The stops pin to
  real places, and the map shows where you've been against the route. Video is recorded at a few frames a second, with your tracked positions in
  `sources/raw/scan-<time>-poses.json`. If the phone won't share its camera with AR, it says so and saves no video.

The video lands in `sources/raw/scan-<time>.webm` or `.mp4`, and `domus video auto` starts by itself. It reconstructs the video, then with AR positions fits the
reconstruction to them, moves it to sit on the LiDAR ground, adds it to the model's point cloud and rebuilds. Without AR positions, it stops after
reconstructing, and you place the clip as below. This takes a while on a CPU.

That automatic placing is rough, because the AR track starts from where you said stop 1 was and drifts as you walk. Expect something like a metre, and check
it against the model. Control points with `domus video place` are how to pin it down. All of this hasn't had much use on real phones, so check your first
scan before relying on it.

## Filming

Walk slowly, keep the camera on the thing you care about, and turn gradually: a fast swing breaks the chain of views.
Keep to one lens if you can, and note which, since the reconstruction starts from its focal length in pixels (a phone's
main camera in 1080p is about 1100, its wide lens about 630).

## The steps

```sh
domus video frames sources/raw/walk.mp4     # the sharpest frame per 1/6 s
domus video reconstruct walk --focal 1100   # 3D from the frames: data/video/walk-colmap-sfm.npz
domus video place walk-colmap               # onto the grid, from data/video/walk-colmap-control.json
domus video dense walk-colmap --scale 1.0   # a denser cloud from stereo
domus video export                          # the clouds listed in data/site.toml
domus build
```

List the clouds to show in `data/site.toml`:

```toml
[video]
clouds = ["walk-colmap-dense", "walk-colmap-site"]
```

Every step has `--help`, and `domus video` lists the others.

## Placing a clip

`place` needs to know how the clip sits on the grid. `data/video/<tag>-control.json` names points marked in the frames
and their site coordinates, such as house corners from your `house.geojson` and ground heights from the LiDAR. The
docstring at the top of `src/domus/video/georef_sfm.py` gives every kind of control. Use ground the LiDAR sees
clearly, not lawn that has been regraded or anything under trees, and spread the points out: a short or nearly straight
line of control can leave the clip tilted about it, which the frames' own vertical (a `plumb` block) prevents.

## When a clip breaks up

- **It splits into several models** (`<tag>-m2` and so on): a fast turn or a blank stretch broke the chain. Reconstruct
  the parts and join them with `merge` where they share frames, or `link` and `link-picks` where they don't.
- **Two passes of the same place don't line up**: look for a false match between things that look alike, such as two sets
  of slots. `split` lets you keep the good parts apart.
- **It falls apart from the start**: check `--focal`. The wrong lens is the usual cause.
- **The global mapper joined parts of a long clip wrongly**: re-run with `--mapper incremental`. Features and matches are
  cached, so only the mapping runs again.

Check a placed clip with `domus video plot <tag>`, which draws it in plan over the aerial.
