# Observations

An observation is a reading from a video, kept as data apart from the model and linked to it by ids. It says what
was read, what part of the model it checks and how sure the reading is. At every build Domus reads both sides afresh:
the measured value from the source (the clip's latest solved picks) and the expected value from the model as it is
now, by the element's id. It shows the difference in the viewer, coloured against the uncertainty. When the model
changes the expected value follows; when a clip is solved again the reading follows; and when a target is renamed or
removed, the build says which observation lost it.

An observation that disagrees with the model can be proposed: Domus proposes setting the target to what was read, and
a person accepts or rejects it through the change route ([changes.md](changes.md)). Nothing applies itself.

The examples here are on the made-up demo (`domus demo`), with made-up picks.

## The file

A site keeps its observations in `data/observations.json`:

```json
{"observations": [
  {"id": "w1-top", "kind": "level", "name": "W1's top, east end",
   "source": {"picks": "walk", "mark": "w1-east-top"},
   "target": {"layer": "walls-existing", "id": "W1", "what": "top", "vertex": 1},
   "uncertainty_m": 0.03},
  {"id": "corner", "kind": "point", "name": "House corner",
   "source": {"picks": "walk", "mark": "corner"},
   "target": {"layer": "house", "id": "house-walls", "what": "position", "vertex": 0},
   "uncertainty_m": 0.05},
  {"id": "walk-up", "kind": "placement", "name": "The walk, 0.3 m higher",
   "source": {"clouds": ["walk-colmap-site"], "pivot": [1500011.0, 5500022.0], "dz_m": 0.3}}]}
```

| Key | What it is |
|---|---|
| `id` | Its own id: letters, digits, `.`, `-` and `_` |
| `kind` | `level`, a height; `point`, a position; or `placement`, a reconstruction placed another way, shown beside the model |
| `source` | What was read. For a level or a point, a mark in a clip's picks: `{"picks": "walk", "mark": "w1-east-top"}`, read from `data/video/picks/walk-site.csv` as `domus video pick` last solved it (`picks` can also name another CSV there). For a placement, its clouds in `data/video/` and how they're moved: `{"clouds": [...], "dz_m", "turn_deg", "pivot": [E, N]}` |
| `target` | What it checks, below, or `null` for a reading shown by itself. A placement has none |
| `uncertainty_m` | How sure the reading is, in metres |
| `name`, `note` | Its label, and anything else to say about it |
| `lift_m` | How far above the reading its label stands: 0.15 unless given |

### Targets

| Target | For | The model's value |
|---|---|---|
| `{"setting": "house.fl_upper"}` | a level | A level the settings give, such as a floor's (`house.fl_lower` is `fl_upper` when the site leaves it out) |
| `{"layer": "walls-existing", "id": "W1", "what": "top", "vertex": 1}` | a level | A vertex's height: `top` for a wall's top, `level` for any other feature's, such as a ground-fix point; vertex 0 unless given |
| `{"layer": "walls-existing", "id": "W1", "what": "toe", "vertex": 1}` | a level | An existing wall's toe at a vertex: its `toe_m` there, else the ground in front of its face, as the build takes it |
| `{"layer": "house", "id": "house-walls", "what": "plinth", "face": "north"}` | a level | The plinth's top on a face of the house, as the model draws it: the face's `plinth_top`, else the lowest ground along it |
| `{"what": "ground"}` | a level | The ground as the build draws it, under the reading |
| `{"layer": "house", "id": "house-walls", "what": "position", "vertex": 0}` | a point | A vertex's place in plan |

## What a build makes of them

Each observation is read afresh and given a state:

| State | What it means | Label |
|---|---|---|
| `ok` | The reading is within its uncertainty of the model's value | green |
| `near` | Within twice its uncertainty | amber |
| `off` | Further than twice its uncertainty | red |
| `compared` | It gives no uncertainty, so only the difference is shown | plain |
| `reading` | It has no target, so its reading is shown by itself, with its note | plain |
| `lost target` | Its target isn't in the model any more, or has no value there; the build says which | grey, dashed |
| `no reading` | Its picks or its mark aren't there, or the mark wasn't solved; the build says which | none |
| `placement` | A reconstruction placed another way | a cloud |

For a level the difference is the reading less the model's value. For a point it's the distance in plan, with its
east and north parts. The viewer's layer group **Observations** holds a label for each reading (hidden until it's
switched on), named by `[labels] readings`, such as `W1's top, east end: 42.12 (model 42.07, +0.05 m, ±0.03)`, and
each placement's cloud. `domus video export` writes the placements' clouds to `data/video/compare.npz`, moved as they
say. `summary.json` reports each observation under `observations`: its state, `measured`, `expected`,
`difference` (and a point's `offset`), `uncertainty_m`, `target` and `source`. A build that finds a lost target says
so:

```text
observation w1-top: lost target: walls-existing has no W1
```

`domus prep check observations` lists them all (`site.check(["observations"])`). It warns for one that's `near`,
lost its target or has no reading, and fails for one that's `off`.

## Accepting what was read

```sh
domus site propose w1-top
domus site accept 1
```

`domus site propose` (`site.propose_observation(id)`) proposes setting the observation's target to what it read,
through the change route:

| Target | The proposal |
|---|---|
| A setting | `setting`: the level read |
| A vertex's height | `level`: the vertex at the height read, with the observation in its basis |
| A wall's toe | `props`: the wall's `toe_m`, each vertex's toe as the model has it, with that one at the height read |
| A plinth | `props`: the house's `plinth_top` with that face at the height read |
| The ground | `add`: a ground-fix point at the reading, `observation-<id>` (or `coords` for that point, if it's there) |
| A vertex's place | `coords`: the vertex moved to the reading in plan, keeping its height |

A person accepts it (`domus site accept`, or the viewer's **Site** tab) or rejects it, saying why. Accepted, it's an
edit like any other, and the observation then reads against the changed model: `domus site undo` puts it back. An
observation with no target, or one that lost it, has nothing to propose.

## Sites from before

A site in format 4 or before said two kinds of these in `data/site.toml`. Domus reads them as observations, in
memory (format 5's step):

- `[[video.question_marks]]`, a clip's reading of the house's levels, is a `level` with no target yet. Its `text` is
  its name, its picks and mark its source (a mark read in more than one clip's picks gives each observation the picks'
  name after the mark's id), its `drawn` level its note, and its `lift_m` its own.
- `[[video.compare]]`, two placements of a clip side by side, is a `placement`, with its `clouds`, `pivot`, `dz_m`
  and `turn_deg` as its source.

`domus site upgrade` writes them into `data/observations.json` and takes them out of `data/site.toml`. Give each
question mark its target, such as `{"setting": "house.fl_upper"}` for a door sill read against the floor, to see how
far the model is from it.
