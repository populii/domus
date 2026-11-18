// Run by tests/test_sketch.py with node: the viewer's check of outlines that cross themselves (sketch-core.js's
// crossesItself and crossings), on cases the test holds to the server's (domus.options.sketch.crossings).
//   node sketch_crossing_check.mjs CORE_JS CASES_JSON
// CASES_JSON holds {"rings": [[[x, y], ...], ...], "sketches": [[item, ...], ...]}; it prints {"rings": [whether each
// crosses itself], "sketches": [each sketch's messages]}.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [core, casesPath] = process.argv.slice(2);
const { crossesItself, crossings } = await import(pathToFileURL(core).href);
const cases = JSON.parse(readFileSync(casesPath, "utf8"));
console.log(JSON.stringify({ rings: cases.rings.map((r) => crossesItself(r)), sketches: cases.sketches.map((s) => crossings(s, 3.0)) }));
