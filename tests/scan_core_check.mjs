// Run by tests/test_scan_page.py with node: the scan page's sums, without a browser or a phone.
import assert from "node:assert";
import { pathToFileURL } from "node:url";

const core = await import(pathToFileURL(process.argv[2]).href);
const { angleDelta, createCoverage, buildRoute, sharpness, speedWarning, gapShare, pinToWorld } = core;

assert.equal(angleDelta(10, 350), 20);
assert.equal(angleDelta(350, 10), -20);

const cov = createCoverage(0);
assert.equal(cov.targets.length, 18);
cov.look(0, -20, 200);
assert.equal(cov.targets.filter((t) => t.done).length, 0, "too fast doesn't count");
cov.look(0, -20, 10);
assert.equal(cov.targets.filter((t) => t.done).length, 1);
cov.look(359, 8, 10);
assert.equal(cov.targets.filter((t) => t.done).length, 2, "across north");

const model = {
  layers: [{ id: "walk-route", segments: [0, 0, 0, 10, 0, 0, 10, 0, 0, 10, 0, 10], labels: [{ text: "1", at: [0, 1, 0] }, { text: "2", at: [10, 1, 10] }] }],
  enrich: { route: { stops: [{ n: 1, text: "a" }, { n: 2, text: "b" }] } },
};
const route = buildRoute(model);
assert.deepEqual(route.path, [[0, 0], [10, 0], [10, 10]]);
assert.deepEqual(route.stops[1].at, [10, 10]);
assert.equal(buildRoute({ layers: [] }), null);

// Facing along the first leg with no turn: the stops land where they are, from the start.
const pins = pinToWorld(route, [5, 1, 5], [1, 0, 0]);
assert.ok(Math.hypot(pins[1].world[0] - 15, pins[1].world[1] - 15) < 1e-9);
// Facing the other way, they turn with you, and back again.
const turned = pinToWorld(route, [0, 0, 0], [0, 0, 1]);
assert.ok(Math.hypot(turned[1].world[0] + 10, turned[1].world[1] - 10) < 1e-9);
const back = pinToWorld.invert(route, { pos: [0, 0, 0], fwd: [0, 0, 1] }, turned[1].world);
assert.ok(Math.hypot(back[0] - 10, back[1] - 10) < 1e-9);

assert.equal(gapShare(route.path, []), 1);
assert.equal(gapShare(route.path, [[0, 0], [10, 0], [10, 10]]), 0);

const flat = { width: 8, height: 8, data: new Uint8ClampedArray(8 * 8 * 4).fill(128) };
const edgy = { width: 8, height: 8, data: new Uint8ClampedArray(8 * 8 * 4).map((_, i) => (Math.floor(i / 4) % 2 ? 255 : 0)) };
assert.ok(sharpness(flat) < 1e-6);
assert.ok(sharpness(edgy) > 100);
assert.ok(speedWarning(200, 100));
assert.ok(speedWarning(10, 1));
assert.equal(speedWarning(10, 100), "");
console.log("ok");
