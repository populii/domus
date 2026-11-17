// Run by tests/test_draw.py with node: exact drawing's sums, without a browser.
import assert from "node:assert";
import { pathToFileURL } from "node:url";

const { snapTargets, nearestSnap, referenceAngles, projectOnto, inferAngle, parseEntry, entryPoint, createHistory } =
  await import(pathToFileURL(process.argv[2]).href);
const near = (a, b, t = 1e-9) => assert.ok(Math.abs(a - b) <= t, `${a} is not ${b}`);

const lines = [{ label: "Boundary", pts: [[0, 0], [10, 0], [10, 10]] }];
const t = snapTargets(lines, [{ label: "PEG 1", x: 5, z: 5 }]);
assert.equal(nearestSnap({ x: 0.1, z: 0.1 }, t, 0.5).label, "Boundary corner");
assert.equal(nearestSnap({ x: 5, z: 4.9 }, t, 0.5).label, "PEG 1", "a named point is a corner");
const mid = nearestSnap({ x: 5.2, z: 0.1 }, t, 0.5);
assert.equal(mid.label, "Boundary midpoint");
near(mid.x, 5);
const edge = nearestSnap({ x: 2, z: 0.2 }, t, 0.5);
assert.equal(edge.label, "Boundary");
near(edge.x, 2);
near(edge.z, 0);
assert.equal(nearestSnap({ x: 3, z: 3 }, t, 0.5), null, "nothing close");
assert.equal(nearestSnap({ x: 0, z: 0 }, snapTargets(), 1), null, "no targets");

const refs = referenceAngles(lines);
assert.equal(refs.length, 2);
near(refs[0], 0);
near(refs[1], Math.PI / 2);
assert.equal(referenceAngles([{ label: "x", pts: [[0, 0], [0.1, 0]] }]).length, 0, "too short to give a direction");

const leaning = [Math.PI / 6];
const square = inferAngle({ x: 0, z: 0 }, { x: Math.cos(Math.PI * 2 / 3) * 5 + 0.05, z: Math.sin(Math.PI * 2 / 3) * 5 }, leaning);
assert.equal(square.kind, "square");
const along = inferAngle({ x: 0, z: 0 }, { x: -Math.cos(Math.PI / 6) * 5, z: -Math.sin(Math.PI / 6) * 5 }, leaning);
assert.equal(along.kind, "in line", "either way along a direction counts");
assert.equal(inferAngle({ x: 0, z: 0 }, { x: 5, z: 1.5 }, leaning), null, "not near it");
assert.equal(inferAngle({ x: 0, z: 0 }, { x: 0.05, z: 0 }, leaning), null, "too close to tell");

const p = projectOnto({ x: 1, z: 1 }, 0, { x: 4, z: 3 });
near(p.x, 4);
near(p.z, 1);

assert.deepEqual(parseEntry("3.2"), { len: 3.2, bearing: null });
assert.deepEqual(parseEntry(" 3.2 m "), { len: 3.2, bearing: null });
near(parseEntry("3200mm").len, 3.2);
assert.deepEqual(parseEntry(".5@45"), { len: 0.5, bearing: 45 });
for (const bad of ["", "abc", "-1", "0", "1@400", "1,2"]) assert.equal(parseEntry(bad), null, bad);

const north = entryPoint({ x: 1, z: 1 }, { len: 2, bearing: 0 }, null);
near(north.x, 1);
near(north.z, -1, 1e-9);      // north is -z
const east = entryPoint({ x: 0, z: 0 }, { len: 2, bearing: 90 }, null);
near(east.x, 2);
near(east.z, 0, 1e-9);
const toward = entryPoint({ x: 0, z: 0 }, { len: 3, bearing: null }, { x: 0, z: 10 });
near(toward.z, 3);
assert.equal(entryPoint({ x: 0, z: 0 }, { len: 3, bearing: null }, null), null);

const h = createHistory("a", 3);
assert.equal(h.undo(), null, "nothing to undo");
assert.equal(h.record("a"), false, "no change");
h.record("b");
h.record("c");
assert.equal(h.undo(), "b");
assert.equal(h.undo(), "a");
assert.equal(h.undo(), null);
assert.equal(h.redo(), "b");
h.record("d");
assert.equal(h.redo(), null, "a new change ends the redo");
h.record("e");
h.record("f");
assert.equal(h.undo(), "e");
assert.equal(h.undo(), "d");
assert.equal(h.undo(), null, "limited");
