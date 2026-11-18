// The sketch preview's worker (sketch-core.js, with Manifold's WebAssembly), so the page stays smooth while it works.
// It takes the model's `origin`, `regrade` and ground layer once ({type: "model"}), then each sketch as it changes
// ({type: "sketch", turn, items, dragging}): the latest one waiting is worked out next and the rest dropped, and each
// answer ({type: "preview", turn, ...}) carries what to draw and the soil's volumes, its meshes' buffers handed over.
// While a sketch is dragged its batters' cones have fewer sides, and where they disagree the grid is coarser.
import Module from "./vendor/manifold-3d/manifold.js";
import { DRAG_CONFLICT_STEP_M, DRAG_SEGMENTS, makePreview } from "./sketch-core.js";

const wasm = Module().then((w) => { w.setup(); return w; });
let preview = null, waiting = null, busy = false;

self.onmessage = async ({ data }) => {
  if (data.type === "model") {
    try {
      preview = makePreview(await wasm, data.model);
      if (waiting && !busy) work();
    } catch (err) {
      self.postMessage({ type: "preview", turn: 0, error: `The preview couldn't start: ${err}` });
    }
  } else if (data.type === "sketch") {
    waiting = data;
    if (!busy) work();
  }
};

async function work() {
  busy = true;
  await wasm;
  while (waiting && preview) {
    const job = waiting;
    waiting = null;
    let out;
    try {
      out = preview.update(job.items, job.dragging ? { segments: DRAG_SEGMENTS, conflictStep: DRAG_CONFLICT_STEP_M } : {});
    } catch (err) {
      out = { error: String(err) };
    }
    const handed = [];
    for (const list of Object.values(out.meshes || {})) for (const m of list) handed.push(m.positions.buffer, m.indices.buffer);
    self.postMessage({ type: "preview", turn: job.turn, ...out }, handed);
  }
  busy = false;
}
