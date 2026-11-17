# Wall options

One TOML file per retaining-wall scheme. `domus build` reads every
`options/*.toml` (names starting with `_` are skipped) and turns each into designed ground, walls
with levels, lawns and beds, and a row in the viewer's options table (`src/domus/design.py`).
`_site.toml` holds what they share: the site frame and `[regrade]`, the part of the lot options may regrade
(the whole lot unless `s_max` or `polygon_so` narrows it).

The quickest way to make one is to sketch it in the viewer: draw the wall, set which side it retains, and press **Make it an option**. That writes `options/sketch-<name>.toml`, which you can then edit. Or copy a file, rename it, change a few numbers, rebuild, and pick it in the viewer.

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
keep_existing = []                        # ids of existing walls it keeps; the rest are removed
remove_features = []                      # ids of site features it takes out, such as old steps (data/site/features-*.geojson)

[[walls]]
id = "L2"
name = "Inner wall and its return along the bank"
material = "timber"     # timber | block | segmental | gabion | crib | stone | concrete
line_so = [[5.0, 2.4], [-1.5, 2.4], [-1.5, 6.5]]   # its face
# Optional: thickness_m (0.25: what shows of its top, so for a wall with a capping course the
# capping unit's depth), upstand_m (0.1, how far its top stands above the ground it
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

[[terraces]]
id = "lawn"
name = "Lawn"
kind = "lawn"           # lawn | planting (sets the look) | ground (a regrade, drawn as the ground round it)
level = 42.55           # NZVD2016 at `at_so`
at_so = [7.5, 5.0]
fall = "1:25"           # or "2%" or 0.02; flat lawn is counted up to 1:20
fall_to = "north"       # north | east | south | west on the site's grid, or a bearing in degrees
batter = 2.5            # edges not on a wall slope back to the ground at 1 : batter
polygon_so = [[-1.5, 2.4], [5.0, 2.4], [5.0, 0.3], [7.5, 0.3], [7.5, 10.9], [3.0, 10.9], [-1.5, 6.5]]

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
corner, linear between them: a planting strip that rises beside a sloping drive is drawn this way.
Draw a terrace's edge on the wall's face line where a wall holds it.

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
- Its capping course is laid in capping units as long as its blocks and as deep as `thickness_m`. Each length
  of top, from a corner, a step or an end to the next, starts and ends with a whole unit. At a corner the unit
  of one face runs through to the far side and the other face's butts against it. Where whole units do not
  reach, the one cut to fit is in the middle of the length, or two share it so that neither is under 150 mm.
  Where the top steps one course along a straight stretch, and two capping units make a course, two capping
  units are stacked beside the end of the higher course and the higher capping course laps half a unit over them.
- Where something does not fit, a block is drawn cut at the corner, or a course under the cap, and the wall's
  checks say how far to move the corner or the top.

Heights are in the site's vertical datum, NZVD2016 by default.

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
entries win. One that changes `material` drops the block the base named (`unit_m`, `cap_m`, `colour`). A
`restyle_stairs` entry does the same for the steps. Change the scheme itself in the file it is based on, and every
style of it follows.

Each material is drawn with a picture of it: the site's own in `data/walls/` where
`domus prep wall-textures` has cut one from its photos (timber, block and stone), and otherwise one the
build paints.

## What the build works out

- The designed ground: each terrace's level inside it, batters from its free edges out to the
  existing ground, all inside the regrade area (the lot, or the part of it `[regrade]` names, less any house
  and a margin round it). A batter that cannot reach the ground there is reported as a step at the area's edge.
- Each wall's top, toe, exposed and retained heights, length and face area. Behind a wall the ground is
  the designed ground it holds, below the wall's top where the wall stands above it.
- Each flight's risers and goings, flagged when they are steep (a riser over 0.19 m or a going under 0.25 m).
- Clean edges in the drawn ground: at each wall, round each flight of steps, and where the ground meets the
  house. The ground keeps its level up to the foot of a wall's face and up to its back, and a lawn or a bed
  runs right up to the wall, and to another bed or lawn of the scheme that lies against it. It stops at the
  walls and steps that stand in it, and the ground runs on under a bay window. Nothing of the ground should
  show through a wall or a tread.
- The lawns that are there (`kind` lawn in `data/site/trees.geojson`): where an option leaves a lawn alone it
  stays drawn, on the option's ground, less the option's own lawns and beds, its walls and steps, and
  any sliver under 0.3 m wide that a wall cuts off. It is drawn as its grass without the bare patches it has
  today, and so is a new lawn within a metre of it, so the two read as one lawn. Beds, their plants and
  stumps are not drawn while an option is shown.
- Cut, fill and the area disturbed, with a range from the LiDAR's tolerance.
- Flat lawn: ground in the regrade area no steeper than 1:20 over 1 m, away from the walls, before and
  after.
- What to look at: cover over the drains after a cut and walls crossing drains; footings near posts,
  poles, marks and lids; ground lowered next to the house, or raised against it.
