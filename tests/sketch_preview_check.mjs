// Run by tests/test_sketch_preview.py with node: the viewer's sketch preview (app/viewer/sketch-core.js) worked out for
// each sketch of a corpus on a built model, with the viewer's own Manifold, any `defaults` the sketch gives laid over
// the model's. Prints a line of JSON for each sketch.
//     node sketch_preview_check.mjs CORE MANIFOLD_JS MODEL_JSON CORPUS_JSON [SEGMENTS] [CONFLICT_STEP]
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [core, manifold, modelPath, corpusPath, segments, conflictStep] = process.argv.slice(2);
const { makePreview, PREVIEW_SEGMENTS } = await import(pathToFileURL(core).href);
const Module = (await import(pathToFileURL(manifold).href)).default;
const wasm = await Module();
wasm.setup();
const model = JSON.parse(readFileSync(modelPath, "utf8"));
const previews = new Map();
const previewFor = (defaults) => {
  const key = JSON.stringify(defaults || {});
  if (!previews.has(key)) {
    previews.set(key, makePreview(wasm, { ...model, regrade: { ...model.regrade, defaults: { ...model.regrade.defaults, ...defaults } } }));
  }
  return previews.get(key);
};
for (const sketch of JSON.parse(readFileSync(corpusPath, "utf8")).sketches) {
  let row;
  try {
    const r = previewFor(sketch.defaults).update(sketch.items, { segments: segments ? Number(segments) : PREVIEW_SEGMENTS,
      ...(conflictStep ? { conflictStep: Number(conflictStep) } : {}), meshes: false });
    row = { cut_m3: r.cut_m3, fill_m3: r.fill_m3, unsupported: r.unsupported, empty: r.empty, ms: r.ms };
  } catch (err) {
    row = { error: String(err && err.stack ? err.stack : err) };
  }
  console.log(JSON.stringify({ name: sketch.name, ...row }));
}
