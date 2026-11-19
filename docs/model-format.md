# The model's format

`domus build` writes the model into the site's `viewer/data/` (or `DOMUS_OUTPUT`): `model.json`, the pictures it
names, `summary.json` and `build.json`. The viewer reads `model.json` and nothing else from the site, so this is the
whole contract between the build and the viewer. Its `format` is 1; a change the viewer can't read as before gets a
new number, and `build.json` records it with the Domus that built the model.

Positions are in viewer metres: x east of the site's origin, y up from its height, and z south of it, so a point
`(E, N, H)` on the site's grid is `(E - E0, H - H0, -(N - N0))` for the origin `(E0, N0, H0)`.

## model.json

| Key | What it is |
|---|---|
| `format` | The model's format, 1 |
| `title`, `heading`, `subtitle`, `about`, `attribution` | The viewer's text, from `[site]` |
| `built`, `commit` | When it was built (UTC), and the site folder's git commit when it's in git |
| `crs`, `vertical`, `datums` | The site's grid and height datum by name, and its other height datums (`key`, `label`, `offset_m`) |
| `origin` | `[E0, N0, H0]`: the viewer's origin on the site's grid |
| `extent` | `[E min, N min, E max, N max]`: the area modelled, on the site's grid |
| `textures` | The ground's drapes: `{key: {label, file, width, height}}`; the first is shown first |
| `layers` | What's drawn, in order (below) |
| `summary` | The walls (`walls`), the wall options and how each comes out (`options`, each with its materials list, `materials`: [docs/options.md](options.md#what-an-option-takes-its-materials-list), and what the build left out of its drawn ground as sheets standing up out of it, `left_out`: [docs/options.md](options.md#what-the-build-works-out); `options_site`), the observations, each read against the model (`observations`: [docs/observations.md](observations.md)), and what each layer kind found |
| `lot` | `{area_m2}` of the main lot |
| `views` | The site's own view buttons: `{name: {pos, target}}` in viewer metres |
| `snap` | Lines and points the drawing tools snap to |
| `enrich` | The "Improve this model" checklist and the suggested walk, unless the site turns them off |
| `regrade` | What the viewer works a sketch out with while it's drawn, as the build works an option out, on the `ground` layer: `area`, the regrade area's rings (x and z, even-odd: the house is a hole); `walls`, each existing wall's `id`, `line`, `face` and `back` (x and z), its `plan` (the rings of the ground it stands on, as the soil rule takes it: from its face to its back, put right where a leaning wall's foot folds round a tight corner; x and z, even-odd), and along it `chain` (metres from its first point) and `toe` (the soil's level under it, y: the wall itself isn't soil); `defaults` (`upstand_m`, `batter`, `thickness_m`, `paving_m`, and how a wall is built: `depth_m`, `embed_m`, `embed_per_m`, `embed_courses`, `bedding_m`, `base_m`, `trench_m` and `seal_m`, null where the site leaves it to the rule; from `options/_site.toml`); and `rules`, the lengths the options' rules take (`reach_m`, how far a batter reaches; `held_m`, how far past a wall's back a terrace's edge is held; `wall_step_m`, `toe_probe_m` and `back_probe_m`, how a wall's levels are read; `sketch_back_m`, how deep the level ground behind a sketched wall is). Only on a site whose options have a `[regrade]` |

## Layers

Every layer has an `id`, unique in the model, a `type`, a `name` for the layers panel, and may have a `group` (its
heading in the panel), `colour`, `visible` (false: off until switched on) and `info` (shown when it's picked).

| Type | Its own keys |
|---|---|
| `mesh` | `positions` (x, y, z in threes), `indices` (triangles, in threes); `uv` with `texture` (a file) and `repeat`, `opacity`, `role` and `option` (below), `tint`, `mask`; on an option's wall of a named block whose units are deeper than its capping units, `soil_over`, the triangles of the body's top behind the cap, which the soil covers as the wall is built, and `soil_over_m`, how far it comes up over them (to the cap's top); on a wall of a named block (an option's or one that's there) whose face shares a length with another such wall's, `shared`, the triangles of that length, which close the wall's solid but which the viewer leaves to the other wall, as both would show through each other |
| `lines` | `segments`: each segment's two ends, six numbers; `labels`: `{text, at}` along them |
| `labels` | `items`: `{text, at, kind}`, `at` in viewer metres; an observation's (`kind` `observation`) has a `tone`, `ok`, `near`, `off` or `lost`, which colours it ([observations.md](observations.md)) |
| `points` | `file`: a JSON of `positions` (base64 little-endian int16, in `step` metres) and `colours` (base64 bytes); `size` |
| `vegetation` | `plants` (each one's name and info) and what draws them: `cards`, `limbs` or `drape` |
| `pipes` | A drain or service, drawn at its depth, with its levels |

## Ids the viewer relies on

| Id | What the viewer does with it |
|---|---|
| `ground` | The ground: it takes the drapes in `textures`, and fades with the opacity slider |
| `contours-major`, `contours-minor` | The ground's contours; the minor ones start hidden, and inside a shown option's `mask` they give way to the option's own |
| `fix-<id>` | The outline of ground that isn't from the ground model: hidden while an option is shown |
| `wall-<id>` | An existing wall: hidden while an option shown doesn't keep it |
| `house-roof...` | Drawn with flat shading |
| `dsm` | The surface model: casts no shadow |
| `veg-beds`, `veg-bed-floor`, `stumps` | Hidden while an option is shown |
| `option-<option id>-...` | A wall option's layers, with `option` set and `visible` false: shown with their option. Its designed ground has `role = "ground"` and a `mask` (the box of the ground it replaces, as fractions of the extent); its lawns and beds `role = "skin"` |
| `site-...` | A site's own layer (below) |

## A site's own layers

When a site needs something drawn that no kind in Domus draws, its tool writes it as data, in
`data/layers/<name>.json`, and the build places it in the model as it is. The file holds `{"layers": [...]}`, each
layer with an `id` that starts `site-`, a `type` of `mesh`, `lines` or `labels`, and its points on the site's grid
(east, north, height), which the build moves into viewer metres:

```json
{"layers": [
  {"id": "site-peg", "type": "mesh", "name": "Survey peg", "colour": "#d23f2f",
   "positions": [[1500010.0, 5500020.0, 41.2], [1500010.2, 5500020.0, 41.2], [1500010.1, 5500020.2, 41.6]],
   "indices": [0, 1, 2]},
  {"id": "site-setout", "type": "lines", "name": "Set-out line", "colour": "#1f6fd2",
   "lines": [[[1500004.0, 5500018.0, 41.0], [1500018.0, 5500018.0, 41.0]]]},
  {"id": "site-notes", "type": "labels", "name": "Notes", "items": [{"text": "Peg found here", "at": [1500010.0, 5500020.0, 41.8]}]}
]}
```

Each may also have `group` (its heading, "The site's own" unless given), `visible`, `info` and, for a mesh,
`opacity`.
