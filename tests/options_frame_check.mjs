// Run by tests/test_draw.py with node: the viewer's options frame (draw-core.js optionsFrame) on the frames and
// points given as JSON on stdin. It prints what it makes of them, for the test to hold against
// domus.options.scheme.SiteFrame.
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const { optionsFrame } = await import(pathToFileURL(process.argv[2]).href);
assert.equal(optionsFrame(undefined), null, "no frame, no s and o");
const cases = JSON.parse(readFileSync(0, "utf-8"));
console.log(JSON.stringify(cases.map(({ frame, en, so }) => {
  const f = optionsFrame(frame);
  return { so: en.map(([e, n]) => f.so(e, n)), en: so.map(([s, o]) => f.en(s, o)) };
})));
