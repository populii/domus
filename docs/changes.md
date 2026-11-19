# Changes to a site's data

A change to a site's data goes one way. The viewer, an agent, a site's tool or a check says what to change as an
**intent**, such as "move wall W1 0.5 m east" or "set this level to 42.10". Domus checks it against the site, applies
it to the file it changes, records it in `.domus/edits/` with who made it, when and why, and says what to build again.
Every edit can be undone. A check's or a tool's findings come as **proposals**, which a person accepts or rejects:
nothing applies itself.

The files it changes are a site's layers (`data/site/*.geojson`), its settings (`data/site.toml` and
`options/_site.toml`), its wall options' files (`options/<id>.toml`), and the house kept as an IFC file
(`data/house.ifc`, [below](#the-houses-ifc)). `domus marks` and `domus title` write their layers on this route, and the
viewer's **Make it an option** adds its option's file on it, so each is recorded and can be undone. The other commands
that bring public data in (`domus fetch`, `canopy` and `points`), `domus scan` and `domus site upgrade` write their own
files, outside this route. The examples here are on the made-up demo (`domus demo`), but for the house's IFC, on the
richer made-up site.

The route is part of the public API's promise ([api.md](api.md#every-change-one-way)): what it takes and records keeps
working from one release to the next, so a site's tools and agents can build on it.

## Intents

An intent is a small JSON object with its `kind`. One on a layer names the layer by its file's name in `data/site/`
without `.geojson`, and a feature by its `id`.

| Kind | What it does | Example |
|---|---|---|
| `move` | Moves the named features by one vector, `[dE, dN]` or `[dE, dN, dH]` to move their heights too, to the millimetre. `"ids": "*"` moves them all | `{"kind": "move", "layer": "walls-existing", "ids": ["W1"], "by": [0.5, 0]}` |
| `coords` | Sets a feature's vertices: `so` or `soz` in the wall options' frame, `en` or `enz` on the site's grid, or `geometry`, a whole GeoJSON geometry. `so` and `en` keep each vertex's own height when the feature has heights and the number of its vertices doesn't change; a polygon's ring is given open or closed and written closed. With `vertex`, one point sets that vertex alone | `{"kind": "coords", "layer": "house", "id": "window", "so": [[14.5, 9.0], [14.5, 7.0]]}` |
| `level` | Sets a vertex's height, counted from 0 along a line or a polygon's outer ring; without `vertex`, every vertex's; `z` as a list gives each vertex its own | `{"kind": "level", "layer": "walls-existing", "id": "W1", "vertex": 1, "z": 42.12}` |
| `props` | Sets and drops properties | `{"kind": "props", "layer": "walls-existing", "id": "W1", "set": {"confidence": "measured"}, "drop": ["note"]}` |
| `add` | Adds a feature, on the site's grid, at the end of its layer or at `at` in it. A layer that isn't there yet is started | `{"kind": "add", "layer": "ground-fixes", "feature": {"type": "Feature", "properties": {"id": "fix-1"}, "geometry": {"type": "Point", "coordinates": [1500010.0, 5500020.0, 41.5]}}}` |
| `delete` | Deletes a feature | `{"kind": "delete", "layer": "features-demo", "id": "fence-west"}` |
| `put` | Sets a feature whole, as given: how an undo puts one back as it was | `{"kind": "put", "layer": "walls-existing", "id": "W1", "feature": {...}}` |
| `layer` | Writes a layer whole, as given: a GeoJSON FeatureCollection, each of its features with an id. A layer that isn't there yet is started. It's how `domus marks` and `domus title` write theirs, and how an undo puts one back as it was | `{"kind": "layer", "layer": "features-pegs", "collection": {"type": "FeatureCollection", "features": [...]}}` |
| `setting` | Sets a setting, by its key as [site-format.md](site-format.md) names it, keeping the file's comments: in `data/site.toml`, or `"file": "options/_site.toml"`. `"drop": true` takes it out instead, and `toml` says how the value is written | `{"kind": "setting", "key": "house.fl_upper", "value": 43.45}` |
| `option` | Writes a wall option's file whole, `options/<id>.toml`, as given, which has to be TOML; `"drop": true` takes the file away instead. It's how the viewer's **Make it an option** adds one | `{"kind": "option", "id": "sketch-low-wall", "toml": "name = \"Low wall\"\n..."}` |

`move`, `coords` and `level` can also give a `basis`, a sentence added to the `basis` of each feature they change.

Each is checked against the site before anything is written: its layer and features are there, a feature it names has an
id no other has, its coordinates are numbers that make its geometry, a setting is one Domus knows, given a value of the
kind it holds, and an option's file is TOML, as is the one it writes over or takes away. A site in an older format has
its settings changed once `domus site upgrade` has written it up to date; its layers can be changed as they are.

## Making a change

```sh
domus site edit move-w1.json --why "Measured from the boundary peg with a tape." --apply
```

`move-w1.json` holds an intent or a list of them, applied as one edit. Without `--apply` it says what would change
and writes nothing. `--who` says who's making it, else it's this computer's user. A site's tool or an agent does the
same with `site.edit(intent, why, who=None, dry_run=False)`, where the intent can be a dict, a list or a JSON file:

```python
from domus import api

site = api.open_site("sites/demo")
site.edit({"kind": "move", "layer": "walls-existing", "ids": ["W1"], "by": [0.5, 0]},
          why="Measured from the boundary peg with a tape.", who="sam")
```

```text
   walls-existing.geojson: W1: (1500002.000, 5500022.000) -> (1500002.500, 5500022.000)
data/site/walls-existing.geojson: written; the file as it was is in .domus/edits/0001-move/data/site/walls-existing.geojson
Recorded as edit 0001 (.domus/edits/0001-move.json): domus site undo 1 undoes it.
Build the model again to see it (domus build): this changes the existing walls, the ground their faces and tops are cut into, and the wall options.
```

Each file it changes is written the safe way, all of them or none: a copy of the file as it was, read back; the new
file written to a temporary file in the old one's own layout (its indent, separators and final newline), read back,
and checked to say what was asked and nothing else; then put in place. One change is made at a time: each holds the
site's `.domus/edits/.lock` while it reads and writes.

`domus site shift`, `edit` with a spec in its older form, and `regrade` are edits on this route too: a shift is a
`move` for each file it names, a regrade a `level` for each ground fix, and an older edit's changes `coords`, `props`,
`add` and `delete`. They say what they always said, and their `BACKUP` folder is now optional: given, it holds the
copies of the files as they were, as before; left out, the copies are kept beside the edit's record. The reason
recorded is `--why`, else the spec's `basis`. [AGENTS.md](../AGENTS.md#86-change-layers-safely) has their specs.

`domus marks` and `domus title` are edits on this route as well: each writes its layer whole, a `layer` intent,
recorded with `--why` (else a reason of its own) and `--who`, so `domus site undo` puts back the layer as it was, or
takes away one it started. `site.fetch_marks()` and `site.fetch_title()` take `why` and `who` too. So is the viewer's
**Make it an option** ([below](#in-the-viewer)): it adds the option's file, an `option` intent, and undoing it takes
the option away.

## The record

Each edit is recorded in `.domus/edits/<sequence>-<kind>.json`, with the files as they were beside it in
`.domus/edits/<sequence>-<kind>/` (or the `BACKUP` folder):

```json
{"id": "0001", "kind": "move", "who": "sam", "when": "2026-11-18T08:59:43Z",
 "why": "Measured from the boundary peg with a tape.",
 "intents": [{"kind": "move", "layer": "walls-existing", "ids": ["W1"], "by": [0.5, 0]}],
 "summary": ["walls-existing: W1 moved by (+0.500, +0.000) m"],
 "files": [{"path": "data/site/walls-existing.geojson",
            "before": "15a793798e0be7faa0e8c632840d37dbddf2c29db7c44e5dc5788ed1d7c88ef1",
            "after": "fd59b9fcb10237b7c4a83fbbf563e687733f91a156dd5406ed78ffad8a4cae51",
            "copy": ".domus/edits/0001-move/data/site/walls-existing.geojson"}],
 "inverse": [{"kind": "put", "layer": "walls-existing", "id": "W1", "feature": {"...": "W1 as it was"}}],
 "rebuild": ["the existing walls, the ground their faces and tops are cut into, and the wall options"]}
```

| Field | What it is |
|---|---|
| `id`, `kind` | Its number, and its intents' kind (`edit` when they're of more than one, `undo` for an undo) |
| `who`, `when`, `why` | Who made it, when in UTC, and why |
| `basis` | What it rests on, when that's more than its reason: `{"proposal", "source"}` for an accepted proposal |
| `undoes` | For an undo, the edit it undoes |
| `intents`, `summary` | What was asked, and a line for each, for people |
| `files` | Each file it changed: its path, its SHA-256 before (`null` when the edit started it) and after, and its copy as it was |
| `inverse` | The intents that put back exactly what it changed: each feature whole, where it was in its layer, a layer written whole as it was, each setting as the file wrote it, an option's file as it was (or `"drop": true` to take away one the edit added), and the house's IFC as it was (`ifc-restore`, with its SHA-256, or `null` to take away one the edit started) |
| `rebuild` | What in the model is drawn from the files it changed |

`domus site edits` lists them, oldest first, and `site.edits()` gives them, each with `in_effect` and, once undone,
`undone_by`.

## Undo

```sh
domus site undo 1
```

An undo applies the edit's inverse, but only while each file the edit changed is still as the edit left it, the same
SHA-256: otherwise it could undo more than the edit did, and it says which later edits to undo first, or that the file
was changed outside this route. It gives each file back byte for byte, takes away a layer the edit started, and is
recorded as an edit of its own, so undoing the undo makes the change again. `--build` builds the model afterwards.
`site.undo(edit, who=None, why=None)` does the same.

## The house's IFC

A site whose house is kept as an IFC file (`data/house.ifc`, [site-format.md](site-format.md#the-house-as-an-ifc-file))
changes it on this route too, with intents on its elements. The examples are on the richer made-up site
(`tools/made_up_sites.py`), whose house has a chimney and a tank, once `domus house ifc` has written its file. Each
names an element by its GlobalId or its Domus id (`ref` in its `Domus` property set, which is its Tag too: a part's id
in `house.geojson`, a wall face's, `roof` or `eaves`), and a storey the same way (`floor`, or `lower` and `upper`).
IfcOpenShell's API applies them, and the file is written whole.

| Kind | What it does | Example |
|---|---|---|
| `ifc-move` | Moves an element by `[dE, dN]` or `[dE, dN, dH]`, metres on the site's grid; its parts move with it | `{"kind": "ifc-move", "element": "chimney", "by": [0.5, 0, 0]}` |
| `ifc-rotate` | Turns an element `degrees` anticlockwise, seen from above, about the upright through its own origin, or through `about`, `[E, N]` | `{"kind": "ifc-rotate", "element": "tank", "degrees": 90}` |
| `ifc-storey` | Sets a storey's elevation, a height on the site's datum; what stands on it moves with it | `{"kind": "ifc-storey", "storey": "floor", "elevation": 43.6}` |
| `ifc-attribute` | Sets an element's `Name`, `Description`, `ObjectType` or `Tag` (`null` clears it) | `{"kind": "ifc-attribute", "element": "chimney", "attribute": "Name", "value": "Brick chimney"}` |
| `ifc-property` | Sets a value in one of an element's property sets, made if it isn't there; `"drop": true` takes it out | `{"kind": "ifc-property", "element": "chimney", "pset": "Domus", "property": "source", "value": "measured"}` |
| `ifc-add` | Adds an element of a `class`, made from a `box` (`centre` `[E, N]`, `size` along, across and up, `base` height, `degrees` turned) or an `extrusion` (`outline` `[[E, N], ...]`, `base`, `height`), with a `name`, a `layer` (its class's unless given) and a `colour` (a palette key, one of the site's own or hex), on the highest storey whose floor is at or below its base. Its GlobalId comes from its `id`, as the house's own elements' do, and it's a `block` of the house (its outline, `base` and `top`) to everything that reads the house from the file | `{"kind": "ifc-add", "id": "shed", "class": "IfcBuildingElementProxy", "name": "Garden shed", "colour": "timber-grey", "box": {"centre": [1500002.0, 5500026.0], "size": [2.0, 1.5, 2.1], "base": 41.0}}` |
| `ifc-delete` | Deletes an element, and its parts | `{"kind": "ifc-delete", "element": "tank"}` |
| `ifc-write` | Writes the file whole, the house as `house.geojson` and `[house]` draw it today: what `domus house ifc` does. Its record keeps `house.geojson`'s SHA-256 and that of `[house]`'s settings of the house's shape (`drawn_from`), which a build warns against while the file is the house's source | `{"kind": "ifc-write"}` |

Each is checked against the file before anything is written: the element or storey it names is there, and only one
is called that. What's written is checked against the file as it was: every element and storey the intents didn't
name, nor move with one they did, is as it was, and a moved element moved by the vector asked.

IfcOpenShell writes the file whole, so the edit's copy of the file as it was
(`.domus/edits/<sequence>-<kind>/data/house.ifc`) is what puts it back: an edit's `inverse` is
`[{"kind": "ifc-restore", "sha": "<its SHA-256>"}]`, and an undo puts that copy back byte for byte, only while the
file still has the SHA-256 the edit left it with. Writing the file with `domus house ifc` is an edit too
(`ifc-write`), so writing it again over one that's there (`--force`) is undone the same way, and undoing the first
write takes the file away. A proposal on the house rests on a SHA-256 of each element it names, and is accepted only
while they're as they were.

## Proposals

A check, an observation or a tool that finds something to change proposes it, and a person decides:

```python
site.propose({"kind": "level", "layer": "walls-existing", "id": "W1", "vertex": 1, "z": 42.12},
             why="The clip reads the wall top 50 mm higher at its east end.", source="a check")
```

It's checked against the site as it is, and kept in `.domus/proposals/<id>.json`:

```json
{"id": "0001", "made": "2026-11-18T08:59:44Z", "source": "a check",
 "why": "The clip reads the wall top 50 mm higher at its east end.",
 "intents": [{"kind": "level", "layer": "walls-existing", "id": "W1", "vertex": 1, "z": 42.12}],
 "summary": ["walls-existing: W1's vertex 1 at 42.12"],
 "against": [{"file": "data/site/walls-existing.geojson", "id": "W1",
              "sha": "ffe0e56eee2c926cab55b63f47419c03ad8f6da2395e27b6d9354f2248ac648c"}],
 "state": "open"}
```

| Command | API | What it does |
|---|---|---|
| `domus site proposals [--all]` | `site.proposals(state=None)` | Lists those still open, or all of them |
| `domus site accept 1 [--build]` | `site.accept(proposal, who=None)` | Applies its intents through this route, as an edit whose `basis` is the proposal, and marks it `accepted` with who, when and the edit |
| `domus site reject 1 --why "..."` | `site.reject(proposal, why, who=None)` | Marks it `rejected` with who, when and why; the site's data stays as it is |

A proposal is accepted or rejected once. It's made against the site as it is then, and kept with what each of its
intents rests on (`against`: a SHA-256 of each feature it names and of each file it writes whole, and each setting's
value). It's accepted only while those still hold: if one has changed since, accepting it is refused and it stays open,
for a person to reject, and to propose again if it still holds.

## In the viewer

Served by `domus serve`, the viewer's **Site** tab lists the proposals still open, each with **Accept** and **Reject**
(which asks why), and the newest 50 edits, each in effect with **Undo**. Each runs as its command does, in a process of
its own, one at a time and not while the model is being built; one that changes the data builds the model again, and
the page reloads to show it. A sketch saved with **Make it an option** is an edit too, listed with the others: its
**Undo** takes the option away. Behind a login proxy (`DOMUS_TRUST_PROXY=1`, [self-hosting.md](self-hosting.md)) the
record names who the proxy says it is, and otherwise "the viewer". A page published without the server shows none of
it.

| Endpoint | What it does |
|---|---|
| `GET /api/changes` | The proposals, the newest 50 edits (newest first) and how many there are, and how the last change asked for is going |
| `POST /api/proposals/<id>/accept` | `domus site accept <id> --build` |
| `POST /api/proposals/<id>/reject` `{"why"}` | `domus site reject <id> --why ...` |
| `POST /api/edits/<id>/undo` | `domus site undo <id> --build` |
