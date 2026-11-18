// The viewer's own Delaunay triangles (sketch-core.js's delaunay), for tests/test_sketch_preview.py to hold to scipy's:
//   node delaunay_check.mjs CORE_JS SETS_JSON
// SETS_JSON lists point sets as {"pts": [[e, n], ...], "z": [...]}; each gives a line of JSON, its triangles as
// corner indices or null.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [core, setsPath] = process.argv.slice(2);
const { delaunay } = await import(pathToFileURL(core).href);
for (const { pts, z } of JSON.parse(readFileSync(setsPath, "utf8"))) console.log(JSON.stringify(delaunay(pts, z)));
