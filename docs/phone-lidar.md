# Phone and tablet LiDAR scans

iPhone Pro and Pro Max and iPad Pro have a LiDAR sensor, good for detail close up: walls, steps, banks under trees. It
measures depth directly, so a scan is in true metres and doesn't need the reconstruction a video does. It reaches about
5 m, so walk close to what you want.

iOS doesn't save LiDAR data by itself. Scan with an app such as Polycam, Scaniverse or 3d Scanner App, and export as
**PLY or LAS**. OBJ works too, using a mesh's corners as points. Compressed LAZ, E57 and USDZ aren't read.

## Placing a scan

Pick points you can see in both the scan and the model, such as house corners or survey marks, and list them in a CSV.
Read `x,y,z` off the scan in a viewer like [CloudCompare](https://www.cloudcompare.org), and `E,N,H` off the site's
viewer with **Select**:

```csv
x,y,z,E,N,H
0.12,0.03,-1.40,1570000.20,5180010.50,42.31
4.80,0.05,-1.37,1570004.10,5180013.20,42.33
```

Then:

```sh
domus scan lawn.ply --tag lawn --control lawn-control.csv --up y
domus video export
domus build
```

It prints how far off each control point fits. `--up` names the scan's vertical axis, usually `y` from a phone app and
`z` in LAS; check with a viewer if unsure. The phone's sensors keep vertical well, so with `--up` two points are enough,
and the further apart the better. Leave `--up` out to fit the tilt too, from three or more points not in a line. A file
already on a real grid takes `--epsg 2193` instead of control points.

The scan appears in the viewer's point cloud layer with the others, thinned to one point per 2 cm.

## How far to trust it

Long scans drift, since the phone tracks itself as you walk: several short scans placed separately beat one long one.
Check by measuring a known distance in the viewer. Glass, water, dark surfaces and bright sun all give poor returns.
