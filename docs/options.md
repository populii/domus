# Wall options

One TOML file per retaining-wall scheme. `domus build` reads every
`options/*.toml` (names starting with `_` are skipped) and turns each into designed ground, walls
with levels, lawns, beds and paving, and a row in the viewer's options table (`src/domus/options/`).
`_site.toml` holds what they share: the site frame and `[regrade]`, the part of the lot options may regrade
(the whole lot unless `s_max` or `polygon_so` narrows it).

The quickest way to make one is to sketch it in the viewer: draw the wall, set which side it retains, and press **Make it an option**. That writes `options/sketch-<name>.toml`, which you can then edit. Or copy a file, rename it, change a few numbers, rebuild, and pick it in the viewer.

A sketched area's outline, and the ground a sketched wall holds (its line and the line behind it), can't cross themselves. The viewer won't close an outline that would, keeps a dragged or typed corner where the outline last didn't, and undoes a change to a wall's side or depth that would, each time saying why; the server won't save such a sketch as an option, with the same message.

## The site frame

Options are drawn in metres along and in from one boundary of the lot, named in `[frame]` in
`options/_site.toml`:

- `s` along the boundary from `origin` towards `toward`.
- `o` in from the boundary line, into the lot (on the right looking along it, or the left with
  `side = "left"`).

`domus new` starts this on the lot's longest edge. Terraces are mostly rectangles when the
boundary is square to the house. Any line or polygon can be given in grid coordinates instead
(`line`, `polygon`).

A lawn's `fall_to` is a compass word on the site's grid, `north`, `east`, `south` or `west`, as in sketches and
house parts, or a bearing in degrees clockwise from north, such as `200`. In a site of format 1, before Domus
2026.11.27, the words ran along the frame instead: `south` along the boundary, `north` back along it, `west` in from
it and `east` towards it. Domus still reads them that way there, and `domus site upgrade` writes them as words or
bearings on the grid.

## Format

```toml
name = "B. Two low tiers"                 # the viewer's heading for it
short = "B. Two low tiers"                # its button
description = "..."
keep_existing = []                        # ids of existing walls it keeps; the rest go, face and all (What the build works out)
remove_features = []                      # ids of site features it takes out, such as old steps (data/site/features-*.geojson)

[[walls]]
id = "L2"
name = "Inner wall and its return along the bank"
material = "timber"     # timber | block | segmental | gabion | crib | stone | concrete
line_so = [[5.0, 2.4], [-1.5, 2.4], [-1.5, 6.5]]   # its face
# Optional: thickness_m (0.25: the thickness its set-out and corners take, and unless depth_m says
# otherwise the depth drawn), upstand_m (0.1, how far its top stands above the ground it
# retains), retained_side ("left"/"right" looking along the line; found from the ground if left
# out). Its top is the designed ground behind it plus the upstand, its toe the designed ground in
# front, unless the line has a third number per point (a fixed top, NZVD2016), or top = 42.6, or
# height_m = 0.9, or tops = [42.6, 42.3] (a level for each stretch of the line: see below).
# picture = "painted" draws it with the painted picture of its material, where the site's own photo
# is of an old wall a new one won't look like.
# unit_m = [0.457, 0.2] draws it in a block you name, at its true size: one block's face, its length
# and its course height, split-faced and set out as it would be built (see below). With it,
# cap_m = 0.1 adds a capping course that high along the top, and colour = "#7f868b" gives the blocks
# their colour.
# depth_m, cap_depth_m, cap_overhang_m, embed_m, embed_per_m, embed_courses, bedding_m, base_m,
# trench_m and seal_m say how it's built, below and behind its face: see below.

[[terraces]]
id = "lawn"
name = "Lawn"
kind = "lawn"           # lawn | planting | paving (each its look) | ground (a regrade, drawn as the ground round it)
level = 42.55           # NZVD2016 at `at_so`
at_so = [7.5, 5.0]
fall = "1:25"           # or "2%" or 0.02; flat lawn is counted up to 1:20
fall_to = "north"       # north | east | south | west on the site's grid, or a bearing in degrees
batter = 2.5            # edges not on a wall slope back to the ground at 1 : batter
polygon_so = [[-1.5, 2.4], [5.0, 2.4], [5.0, 0.3], [7.5, 0.3], [7.5, 10.9], [3.0, 10.9], [-1.5, 6.5]]
# A paved terrace (kind = "paving", what a path sketched in the viewer becomes) is drawn as pavers, and has its
# level at the paving's top. paving_m = 0.2 says how deep its paving is, pavers, bedding and any basecourse: the
# soil is that far under its level. Without it, [defaults] paving_m in _site.toml, else 0.2.

[[stairs]]
id = "steps"
name = "Steps up to the lawn"
line_so = [[3.8, 1.2], [5.0, 1.2]]   # its centre line, from one end to the other
width_m = 1.0
# Optional: material ("concrete", or "sleepers" for two timber sleepers to a tread), risers (worked
# out at about 0.17 m each if left out), and foot and
# head, the levels at the line's first and last points (else the designed ground just beyond each end).
# foot = [40.3, 40.0] is for a flight whose foot stands on ground that slopes across it, such as a
# driveway climbing past: the ground at the left and at the right end of the foot line, facing up the flight.
```

A terrace polygon with three numbers per corner (`[s, o, level]`) takes its own level at each
corner, linear between them: a planting strip that rises beside a sloping drive is drawn this way. Between them is
on the corners' Delaunay triangles, but where four corners lie within 5 mm of one circle, as a rectangle's do, either
diagonal would be Delaunay, and the terrace is cut along the one whose ends are closer in height (where both are as
close, the one from the corner given first).
Draw a terrace's edge on the wall's face line where a wall holds it. A terrace's outline mustn't cross or touch
itself: the build takes such an outline as shapely's `buffer(0)` reshapes it, which can keep only part of what it
closes round, and says so in a warning that names the option and the terrace.

A flight of steps is straight, and joins the ground at its two ends: move a lawn's level and its steps
follow. The ground under it is a ramp between them. Leave a gap in a wall where steps pass through it, and
use two flights with a small terrace between them for a turn. Without `[[stairs]]`, `steps = 1` still just
counts a flight for the table.

On ground that slopes across its foot (`foot` with two levels), a flight keeps level treads and a straight foot
line, and every tread runs the full width. Its lowest tread is level with the ground at the high end of the foot
line: its riser is nothing there and the whole of the ground's rise at the low end. The risers above it are equal,
and `risers` counts that lowest one too. The ground the flight stands on follows the slope, and a wall up its
side is measured from that ground at the wall's own face.

A wall down a slope can keep a level top and step: give `tops`, one level for each stretch of its
line, and it drops straight down at the points between them. Its ground stays the ground on each
side, so a bay stands above the ground behind it at its low end. A timber wall is drawn with a post
at every point of its line and about every 1.2 m between, so put the steps where posts go.

A wall of a named block (`unit_m`) is set out block by block:

- Its courses are level, on one grid of levels. They run through where its top steps and where it stands against
  another wall of the same block, and each top carries its own capping course. So give such walls tops a whole
  number of courses apart: a top terrace's wall three courses above the lower one's, not two and a half.
- Along each straight stretch the blocks are in running bond from the corners. Where the face turns round a
  corner a corner block stands, showing a whole block's length on one face and half a block's on the other,
  turn about from course to course. So make each stretch between two such corners a whole number of half blocks
  long. Blocks are drawn up to 1% long or short to make up a small difference.
- Where the face turns in at a corner the blocks of the two faces butt, turn about: in one course the one face's
  block runs half its length on behind the other face, whose block stops against it, and in the next course the
  other way round. So one face shows a whole block at the corner and the other half of one, and the stretches
  each side of it want to be whole numbers of half blocks long too. Where one is not, the outside corner beyond
  keeps its whole blocks and the cut block stands in the inside corner.
- A wall that carries on along the face of one before it in the file takes up that wall's bond, and the length of
  face the two share is drawn once.
- Its capping course is laid in capping units as long as its blocks, set out on `thickness_m` and drawn
  `cap_depth_m` deep (below). Each length of top, from a corner, a step or an end to the next, starts and ends with
  a whole unit. At a corner the unit of one face runs through to the far side and the other face's butts against it. Where whole units do not
  reach, the one cut to fit is in the middle of the length, or two share it so that neither is under `cut_min_m`.
  Where the top steps one course along a straight stretch, and two capping units make a course, two capping
  units are stacked beside the end of the higher course and the higher capping course laps half a unit over them.
- No block is cut shorter than `cut_min_m`: 0.15 m, the 6 inches makers give, unless the wall or `[defaults]` says
  otherwise. Where a course would end in a shorter piece, at an end of the wall, a corner or a step in its top, that
  piece and the whole block beside it become two equal cut blocks instead, as makers' manuals do it (a 46 mm piece
  beside a 457 mm block: two blocks of 251 mm), so nothing moves. The courses of the other bond at that end do the
  same, so their joints stay a quarter of a block from these: the short course's new joint is half a block and half
  the piece in from the end, the other's three quarters of a block and half the piece, where the plain bond would
  put them only half the piece apart. It's drawn so, counted so in the materials list, and `domus prep check blocks`
  reports the two equal cut blocks. Where no whole block is beside the piece, as on a stretch shorter than that, the
  piece stays, and the check says it's under the minimum.
- Where something does not fit, a block is drawn cut at the corner, or a course under the cap, and the wall's
  checks say how far to move the corner or the top.

A wall can say how it's built, below and behind its face, as its maker's guide does. Each of these can be given for
every wall in `[defaults]` in `_site.toml` too, and none changes a wall that doesn't give it:

- `depth_m`: its body, from its face to its back, which is drawn and isn't soil. Unless given, `thickness_m`. A wall
  of a named block set out on half a block's `thickness_m`, so whole capping units meet at its corners, gives its
  units' real depth here; its set-out and its corners keep to `thickness_m`.
- `cap_depth_m`: its capping units' depth, from their front to their back. Unless given, `depth_m`.
- `cap_overhang_m`: how far its capping units stand out in front of its face: "about 25 mm" is `0.025`. Unless
  given, 0: flush with it.
- `embed_m`, `embed_per_m` and `embed_courses`: its first course goes into the ground at its toe, at least
  `embed_m`, `embed_per_m` for each metre the wall stands above the ground there, and `embed_courses` whole courses
  of a wall of named blocks, whichever is most. "At least 100 mm, or 25 mm for every 200 mm course of wall" is
  `embed_m = 0.1` and `embed_per_m = 0.125`; "one unit below the ground" is `embed_courses = 1`. A wall of named
  blocks stands on the course of its grid at or below that depth, so its first course, and its trench under it,
  steps down with the ground a course at a time. It's drawn that far down, and the ground in front stays where it
  is.
- `bedding_m` and `base_m`: a bedding layer and a compacted base course under its first course.
- `trench_m`: its trench's width from its face, which the base course, the bedding, the wall and any drainage
  aggregate behind it fill from the trench's bottom. Unless given, `depth_m`.
- `seal_m`: soil over the drainage aggregate behind its units, at the top of its trench, under the finished ground
  ("the top 200 mm in low permeability soil" is `0.2`). Unless given, 0: the aggregate reaches the finished ground.

None of that is soil but the seal, so the trench is cut from the ground down to its bottom, the wall's toe less its
embedment, bedding and base; the seal is soil brought back in over the aggregate, so it counts as fill; and behind
the trench the soil is filled to the designed ground as it is behind any wall. A wall that gives none of it stands
on the ground at its toe, as before. What a guide adds at some places along a wall, such as more drainage aggregate
behind an outside corner, isn't counted.

Heights are in the site's vertical datum, NZVD2016 by default.

## What an option takes: its materials list

Each option has a list of what it takes to build, in `summary.json` (`materials`) and in the viewer under the
options' comparison, a small table for each option. It's worked out from the option as the build works it out, and
gives net quantities, and beside each the quantity to order: the net with `allowance_pct` per cent added, as
`[materials]` in `_site.toml` says (none unless it does). There are no costs.

- **Units**, for a wall of a named block (`unit_m`): straight, corner and capping units, whole and cut, counted
  course by course from its set-out, as it's drawn and as `domus prep check blocks` sees it. A corner unit stands at
  each exposed end of a course: at an outside corner (its length on one face, its end on the other), at the wall's
  ends, and where the top steps down. In an inside corner a whole or half block's piece is a whole unit, half of it
  behind the other face. Where the wall's foot steps down a course, the course under the step is counted in whole
  units, the step at a joint. Any other piece is a unit cut to fit. Beside them, as a check, the units the face takes
  by area, from the first course up to the capping units.
- **Pins** (`pins_per_unit` for each unit under another course) and their boxes (`pins_per_box`), and the drainage
  aggregate in the units' **cores** (`core_void_m3` a unit): only where they're given. Adhesive for the capping,
  corner and cut units, as needed.
- **Aggregate**, where a wall says how it's built: the base course and the bedding along its trench (`trench_m` wide,
  its length long); the drainage aggregate behind its units, from its body's back to its trench's and from its first
  course up to the soil over it (`seal_m`), which is listed as low-permeability soil. All of it in cubic metres,
  compacted, and in tonnes where `[materials] density_t_m3` gives a density (`base`, `bedding`, `drainage`,
  `paving_bedding`, `paving_base`).
- **Drainage**, where a wall gives it: a perforated pipe along its foot (`pipe_m` wide), in a filter sock, with an
  outlet for each `outlet_spacing_m` (15 m unless it says) of it or part of it, at least one a wall: put them at the
  low points. Filter fabric over its face (`geotextile = true`), its length by its height from its first course.
- **Other walls**: their length and face by material, and a timber wall's posts where it gives their spacing
  (`post_spacing_m`): one at each end and each point of its line, as it's drawn, and between them no further apart
  than the spacing.
- **Steps**: a concrete flight's concrete as it's drawn, each tread down to 0.3 m under the one before; a flight of
  sleepers' two sleepers across each tread, and how many sleepers where `[materials] sleeper_m` gives one's length.
- **Paving**: the paved terraces' area, and the bedding sand and basecourse under it where a terrace or `[defaults]`
  gives their depths (`paving_bedding_m`, `paving_base_m`, parts of `paving_m`).
- **Soil**: the cut, the fill and the fill less the cut, as the option counts them over the regrade area. The soil
  over the drainage aggregate is part of the fill.

The units' and the aggregates' settings go with the wall, as how it's built does: on a wall, or for every wall in
`[defaults]`, and a restyle to another material drops them with the block.

```toml
# options/_site.toml
[materials]
allowance_pct = 10                          # added to what's ordered; the list gives the net beside it
density_t_m3 = { base = 2.0, drainage = 1.6 }
sleeper_m = 2.4                             # to count the sleepers of sleeper steps
```

## The same scheme in another style

To compare how a scheme looks in different walls, base one option on another and restyle it:

```toml
name = "B2. Two low tiers in gabion"
short = "B2. Gabion"
description = "..."
based_on = "b-two-tiers"     # the option in b-two-tiers.toml: its walls, steps and ground

[[restyle]]                  # every wall, or the ones listed in `walls`
material = "gabion"
thickness_m = 0.5

[[restyle]]
walls = ["L2"]
material = "stone"

[[restyle_stairs]]           # every flight of steps, or the ones listed in `stairs`
material = "sleepers"
```

A `restyle` entry sets anything a wall takes (`material`, `thickness_m`, `upstand_m`, `picture`, `unit_m`), and later
entries win. One that changes `material` drops the block the base named (`unit_m`, `cap_m`, `colour`) and how it's
built (`depth_m`, `cap_depth_m`, `cap_overhang_m`, `embed_m`, `embed_per_m`, `embed_courses`, `bedding_m`,
`base_m`, `trench_m`, `seal_m`, `cut_min_m`, and for its materials `pins_per_unit`, `pins_per_box`, `core_void_m3`, `pipe_m`,
`outlet_spacing_m`, `geotextile`, `post_spacing_m`). A
`restyle_stairs` entry does the same for the steps. Change the scheme itself in the file it is based on, and every
style of it follows.

Each material is drawn with a picture of it: the site's own in `data/walls/` where
`domus prep wall-textures` has cut one from its photos (timber, block and stone), and otherwise one the
build paints.

## What the build works out

- The designed ground: each terrace's level inside it, batters from its free edges out to the
  existing ground, all inside the regrade area (the lot, or the part of it `[regrade]` names, less any house
  and a margin round it). A batter that cannot reach the ground there is reported as a step at the area's edge.
  Each existing wall the option takes away (one not in its `keep_existing`) goes, face and all. Its footprint, from
  its face's foot with any batter to its back, joins that option's regrade area as far as the lot and the part
  `[regrade]` names go (into the house's margin too, but never the house), and there the option starts from the
  soil under the wall, at its toe, not from its old face. The terraces and their batters reach across the footprint
  as anywhere else, and where none of them reaches, the ground is left at the toe.
- The existing ground is today's best: the ground as the build draws it, with the ground fixes from measurements
  and placed walks where there are some, the existing walls' faces and tops, and the LiDAR only where nothing
  better is known. Options are designed on it (where batters meet the ground, the flat lawn before, the cover over
  the drains, the step at the regrade area's edge), but for the footprints of the walls they take away (above), and
  measured from it.
- Each wall's top, toe, exposed and retained heights, length and face area. Behind a wall the ground is
  the designed ground it holds, below the wall's top where the wall stands above it.
- Each flight's risers and goings, flagged when they are steep (a riser over 0.19 m or a going under 0.25 m).
- Clean edges in the drawn ground: at each wall, round each flight of steps, and where the ground meets the
  house. The ground keeps its level up to the foot of a wall's face and up to its back, and a lawn, a bed or
  paving runs right up to the wall, and to another of the scheme's lawns, beds or paving that lies against it. It stops at the
  walls and steps that stand in it, and the ground runs on under a bay window. Nothing of the ground should
  show through a wall or a tread.
- The lawns that are there (`kind` lawn in `data/site/trees.geojson`): where an option leaves a lawn alone it
  stays drawn, on the option's ground, less the option's own lawns and beds, its walls and steps, and
  any sliver under 0.3 m wide that a wall cuts off. It is drawn as its grass without the bare patches it has
  today, and so is a new lawn within a metre of it, so the two read as one lawn. Beds, their plants and
  stumps are not drawn while an option is shown.
- Cut, fill and the area disturbed, with a range from the LiDAR's tolerance. Cut and fill are soil, measured as
  solids: the shapes of the soil the option takes away and brings in (`domus.options.solids`), which the materials
  list's soil takes too. The area disturbed and the deepest cut and fill are read on a 0.1 m grid. The soil a
  new wall holds back counts, but the wall itself, from its face to its back and from its toe to its top, doesn't:
  the soil under it is at its toe. A wall that says how it's built stands in its trench, and the soil stops at the
  trench's bottom, but for the soil over its drainage aggregate (`seal_m`), which is fill. Nor does paving: under a
  paved terrace the soil is `paving_m` below its level.
  An existing wall isn't soil either, so taking one out and filling where it stood fills from its toe; where an
  option leaves that ground at the toe, and where it keeps a wall, it moves no soil.
- Flat lawn: ground in the regrade area no steeper than 1:20 over 1 m, away from the walls (0.3 m past
  each wall's body, `depth_m` deep), before and after.
- What it takes to build, to order from: its materials list (below).
- What to look at: cover over the drains after a cut and walls crossing drains; footings near posts,
  poles, marks and lids; ground lowered next to the house, or raised against it.
