# Pictures of the viewer

`domus shots SPEC.json` saves pictures of a site's model as the viewer draws it: the site as it is or a wall option,
from a view button or from a camera you place. They're for comparing options side by side, showing someone a scheme,
or setting the model beside a photo taken from the same spot.

```sh
uv sync --extra shots                   # Playwright, an optional extra
uv run playwright install chromium      # the browser it drives, once on each machine
uv run domus --site sites/demo shots sites/demo/shots.json
```

The site has to be built first (`domus build`). For the run, Domus serves the viewer and the built model on a free
port of this machine's own address (127.0.0.1), opens them in Playwright's own Chromium with no window on screen, and
takes the shots in turn. It stops the server and the browser when it's done, or when anything goes wrong, and the whole
run has a time limit: 600 seconds, or what `--timeout` gives. It prints each picture as it writes it, with what the
viewer said it was showing.

On Linux, `uv run playwright install --with-deps chromium` also installs the libraries Chromium needs. The container
image doesn't include Playwright: take pictures on a machine that has it, with `--site` naming the site's folder.

## A spec

On the made-up demo site (`domus demo sites/demo`, then `domus --site sites/demo build`), this takes the site as it
is, each of its two wall options from above, option A from beyond the lot's north side, and option B in plan and from
beyond its north-west corner:

```json
{
  "window": [1280, 720],
  "scale": 1,
  "hide": [],
  "out": "pictures",
  "shots": [
    {"name": "now", "view": "Overview"},
    {"name": "a", "option": "a-one-wall", "view": "Overview"},
    {"name": "b", "option": "b-two-tiers", "view": "Overview"},
    {"name": "a-from-the-north", "option": "a-one-wall", "pos_so": [-6, 11, 46], "target_so": [12, 11, 41]},
    {"name": "b-plan", "option": "b-two-tiers", "view": "Plan"},
    {"name": "b-from-the-north-west", "option": "b-two-tiers", "pos_en": [1499988, 5500046, 50], "target_en": [1500012, 5500020, 41]}
  ]
}
```

Saved as `sites/demo/shots.json`, it writes `sites/demo/pictures/now.png` and the rest. Everything but `shots` can be
left out:

| Key | What it is |
|---|---|
| `window` | The window's width and height in CSS pixels: `[1280, 720]` unless given. Each picture is the whole window |
| `scale` | Device pixels to a CSS pixel: 1 unless given. 2 makes pictures twice as wide and high, with sharper labels; the viewer draws the model itself at up to twice the window's size, so above 2 only the labels get sharper |
| `hide` | Layer groups to switch off in every shot, by their names in the viewer's Layers list: `Vegetation` (the plants and lawns), `Ground` (the contours, not the ground itself), `Site features` and so on. A wall option's own layers come and go with it |
| `out` | The folder for the pictures, from the spec's own folder: that folder unless given |
| `shots` | The pictures, in order |

Each shot:

| Key | What it is |
|---|---|
| `name` | The picture's file name without `.png`: letters, digits, `-` and `_`. A picture already there is replaced |
| `option` | The wall option shown, by its id: its file's name in `options/` without `.toml`, such as `a-one-wall`. `""`, or none, is the site as it is |
| `view` | A view button's name: `Overview`, `Plan` or one of the site's own, `[views]` in `data/site.toml` |
| `pos`, `target` | Where the camera stands and what it looks at, in viewer metres: x east, y up and z south of the model's origin, as `[views]` gives them |
| `pos_so`, `target_so` | The same in the wall options' frame: s along the frame's boundary, o in from it, and a height on the site's datum |
| `pos_en`, `target_en` | The same on the site's grid: east, north and a height |

A shot has a `view`, or a camera (`pos` in any of its three forms, and `target` in any of its three), or neither,
which is the Overview. The spec is checked against the built model before the browser starts, so a wall option, a
view or a layer group the model doesn't have is named straight away, with the ones it does have.

## Placing a camera

- **In the options' frame**, a camera reads like the options themselves: `"pos_so": [-6, 11, 46]` stands 6 m before
  the frame's start, 11 m in from its boundary, at a height of 46 m. On the demo the frame runs south along the lot's
  east side, so that's beyond the lot's north side, looking south at the walls. [options.md](options.md) describes the
  frame.
- **On the grid**, the viewer's Select tool reads a point's east, north and height: click where the camera should look,
  and use that as `target_en`.
- **In viewer metres**, a view you've found by moving around the viewer, with its vertical scale at 1×, can be read
  from the browser's console: `siteViewer.camera.position.toArray()` is its `pos`, and
  `siteViewer.controls.target.toArray()` its `target`. They're what `[views]` takes too, so a view you'll want again
  can become a button. The model's origin, which they're measured from, is `origin` in its `model.json`.

The camera keeps the viewer's field of view, 45 degrees from the top of the picture to the bottom, and the model its
true vertical scale.

## What a picture shows

Each picture is the whole window, showing the model alone: the panel, the title block, the navigation pad and the
option buttons are hidden, and the compass stays in the top right corner. Everything else is as the viewer draws it,
in its light colours: the ground with its first drape, usually the aerial photo, the labels on the model, the layers
it shows at first but for the groups `hide` names, and for an option, its walls, lawns, beds and contours. Sketches
drawn in the viewer aren't shown. Each shot is taken once the viewer has loaded every picture and point cloud the model
names and has drawn the model since, not after a fixed wait.

A picture is a PNG with the image and nothing else: no text, time, EXIF or colour profile. Chromium writes none of
these today, and Domus keeps only the image's own chunks (`IHDR`, `PLTE`, `tRNS`, `IDAT` and `IEND`) in case it ever
does.

## For scripts of your own

The viewer's page has a hook for scripts that drive it, such as a test of your own or a tool that lines the model up
with a photo: `window.siteViewer`. `siteViewer.setCamera(pos, target)` moves the camera at once, in viewer metres, and
`await siteViewer.drawn()` waits until every picture and point cloud the page has asked for has arrived, any camera
move has finished, and the model has been drawn since. `domus shots` uses both, and clicks the viewer's own buttons
for options, views and layer groups.
