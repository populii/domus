// The viewer's sketch preview: a sketch worked out as solids with Manifold (manifold-3d's WebAssembly) while it's
// drawn, so its walls, the ground they hold, its batters and the soil it cuts and fills can be redrawn as it moves.
//
// A JavaScript copy, step for step, of what domus.options.preview works out in Python with the build's own rules
// (domus.options.sketch, scheme and solids), held to it by the sketches in tests/sketch_corpus.json, which the two
// work out to within about a millionth of a cubic metre. Like the Python it works on the site's grid (east, north)
// with heights as they are, from what the model gives the viewer: model.json's `origin`, `regrade` and ground layer.
//
// Only what a sketch makes is here: walls and the ground they hold, and areas, level or falling, with their batters.
// Where five or more corners of a terrace lie on one circle, which way the build cuts them depends on how Qhull first
// did, so a sketch with one says it can't be shown (`unsupported`).
//
// No DOM: the viewer runs this in a worker (sketch-worker.js), and node runs it in the tests. Manifold's objects live
// in the WebAssembly's memory, so each one made is kept in a list and deleted when the work it was for is done.

export const PREVIEW_SEGMENTS = 24;   // the sides of a batter's cone: between the 16 and 32 the owner chose
export const DRAG_SEGMENTS = 16;      // and while a sketch is dragged, for speed
export const DRAG_CONFLICT_STEP_M = 0.05;   // the grid where batters disagree, while a sketch is dragged
const EDGE_M = 1e-6;                  // solids.EDGE_M: how far a solid reaches past the part of the plan it's for
const MARGIN_M = 1.0;                 // solids.MARGIN_M: how far past the regrade area the ground is taken
const CONFLICT_STEP_M = 0.02;         // solids.CONFLICT_STEP_M: the grid where batters disagree
const TIE_LIFT_M = 1e-8;              // solids.TIE_LIFT_M: each piece's distance cone this much over the one before
const FALLS = { flat: 0.0, "1:100": 0.01, "1:50": 0.02, "1:40": 0.025, "1:25": 0.04 };   // sketch.FALLS
const KINDS = { lawn: "lawn", bed: "planting", path: "paving" };                          // sketch.KINDS

const hyp = Math.hypot;

// --- numbers, as numpy works them ----------------------------------------------------------------------------------

// numpy.interp(x, xp, fp)
function interp(x, xp, fp) {
  const n = xp.length;
  if (x < xp[0]) return fp[0];
  if (x >= xp[n - 1]) return fp[n - 1];
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xp[mid] <= x) lo = mid; else hi = mid;
  }
  return ((fp[hi] - fp[lo]) / (xp[hi] - xp[lo])) * (x - xp[lo]) + fp[lo];
}

// mesh.resample_line: points about every `spacing` along a polyline and at each of its corners, and their distances.
function resampleLine(line, spacing) {
  const s = [0];
  for (let i = 1; i < line.length; i++) s.push(s[i - 1] + hyp(line[i][0] - line[i - 1][0], line[i][1] - line[i - 1][1]));
  const total = s[s.length - 1];
  const num = Math.max(Math.ceil(total / spacing) + 1, 2);
  const step = total / (num - 1);
  const t = [];
  for (let i = 0; i < num; i++) {
    const v = i === num - 1 ? total : i * step;
    let near = Infinity;
    for (const sv of s) near = Math.min(near, Math.abs(v - sv));
    if (near > 0.1 * spacing) t.push(v);
  }
  const chain = [...new Set([...t, ...s])].sort((a, b) => a - b);
  const pts = chain.map((v) => [interp(v, s, line.map((p) => p[0])), interp(v, s, line.map((p) => p[1]))]);
  return { pts, chain };
}

// walls.line_normals: at each point, the left normal of the way from the point before it to the point after it.
function lineNormals(pts) {
  const n = pts.length;
  return pts.map((_, i) => {
    const a = pts[Math.max(i - 1, 0)], b = pts[Math.min(i + 1, n - 1)];
    const len = hyp(b[0] - a[0], b[1] - a[1]);
    const d = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
    return [-d[1], d[0]];
  });
}

// mesh.corner_offsets
function cornerOffsets(pts, side) {
  const n = pts.length;
  return pts.map((_, i) => {
    const legs = [];
    for (const [a, b] of [[pts[Math.max(i - 1, 0)], pts[i]], [pts[i], pts[Math.min(i + 1, n - 1)]]]) {
      const len = hyp(b[0] - a[0], b[1] - a[1]);
      if (len > 1e-9) legs.push([(b[0] - a[0]) / len, (b[1] - a[1]) / len]);
    }
    if (!legs.length) legs.push([1, 0]);
    let t = [legs[0][0] + legs[legs.length - 1][0], legs[0][1] + legs[legs.length - 1][1]];
    const tl = hyp(t[0], t[1]);
    t = tl < 1e-9 ? legs[0] : [t[0] / tl, t[1] / tl];
    const m = Math.max(legs[0][0] * t[0] + legs[0][1] * t[1], 0.5);
    return [(side * -t[1]) / m, (side * t[0]) / m];
  });
}

// mesh.offset_behind: a wall's back `distance` behind its face line, mitred at its corners, never folding back.
function offsetBehind(pts, distance, side) {
  const off = cornerOffsets(pts, side);
  const out = pts.map((p, i) => [p[0] + distance * off[i][0], p[1] + distance * off[i][1]]);
  const chain = [0];
  for (let i = 1; i < pts.length; i++) chain.push(chain[i - 1] + hyp(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  for (let i = 1; i < pts.length - 1; i++) {
    let a = [pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]], b = [pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]];
    const la = hyp(a[0], a[1]), lb = hyp(b[0], b[1]);
    if (la < 1e-9 || lb < 1e-9) continue;
    a = [a[0] / la, a[1] / la];
    b = [b[0] / lb, b[1] / lb];
    if ((a[0] * b[1] - a[1] * b[0]) * side <= 1e-6) continue;
    const half = Math.sqrt(Math.max(0, (1 + (a[0] * b[0] + a[1] * b[1])) / 2));
    const reach = (distance * Math.sqrt(Math.max(0, 1 - half ** 2))) / Math.max(half, 0.5);
    const mitre = out[i].slice();
    for (let k = 0; k < pts.length; k++) if (Math.abs(chain[k] - chain[i]) < reach - 1e-9) out[k] = mitre.slice();
  }
  return out;
}

// Even-odd: whether (x, y) is inside rings ([[x, y], ...], each not closed).
function inRings(rings, x, y) {
  let c = false;
  for (const r of rings) {
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      if ((r[i][1] > y) !== (r[j][1] > y) && x < ((r[j][0] - r[i][0]) * (y - r[i][1])) / (r[j][1] - r[i][1]) + r[i][0]) c = !c;
    }
  }
  return c;
}

function boundsOf(rings) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const r of rings) for (const p of r) {
    x0 = Math.min(x0, p[0]); y0 = Math.min(y0, p[1]); x1 = Math.max(x1, p[0]); y1 = Math.max(y1, p[1]);
  }
  return [x0, y0, x1, y1];
}

// mesh.Surface: a triangulated surface read at points, the highest of its triangles over each, NaN where none is.
function makeSurface(V, F) {
  const nf = F.length / 3, S = [], lo = [], hi = [];
  for (let f = 0; f < nf; f++) {
    const a = 3 * F[3 * f], b = 3 * F[3 * f + 1], c = 3 * F[3 * f + 2];
    const den = (V[b + 1] - V[c + 1]) * (V[a] - V[c]) + (V[c] - V[b]) * (V[a + 1] - V[c + 1]);
    if (!(Math.abs(den) > 1e-15)) continue;
    S.push([V[c], V[c + 1], (V[b + 1] - V[c + 1]) / den, (V[c] - V[b]) / den, (V[c + 1] - V[a + 1]) / den, (V[a] - V[c]) / den,
      V[c + 2], V[a + 2] - V[c + 2], V[b + 2] - V[c + 2]]);
    lo.push([Math.min(V[a], V[b], V[c]), Math.min(V[a + 1], V[b + 1], V[c + 1])]);
    hi.push([Math.max(V[a], V[b], V[c]), Math.max(V[a + 1], V[b + 1], V[c + 1])]);
  }
  if (!S.length) return { z: () => NaN };
  const ext = lo.map((l, k) => Math.max(hi[k][0] - l[0], hi[k][1] - l[1])).sort((p, q) => p - q);
  const mid = ext.length >> 1;
  const size = (ext.length % 2 ? ext[mid] : (ext[mid - 1] + ext[mid]) / 2) || 1.0;
  const base = [Math.min(...lo.map((l) => l[0])), Math.min(...lo.map((l) => l[1]))];
  let width = 0;
  const span = lo.map((l, k) => {
    const i0 = Math.floor((l[0] - base[0]) / size), j0 = Math.floor((l[1] - base[1]) / size);
    const i1 = Math.floor((hi[k][0] - base[0]) / size), j1 = Math.floor((hi[k][1] - base[1]) / size);
    width = Math.max(width, i1);
    return [i0, j0, i1, j1];
  });
  width += 2;
  const cells = new Map();
  span.forEach(([i0, j0, i1, j1], k) => {
    for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) {
      const key = i + width * j;
      if (!cells.has(key)) cells.set(key, []);
      cells.get(key).push(k);
    }
  });
  return {
    z(e, n) {
      const cx = Math.floor((e - base[0]) / size), cy = Math.floor((n - base[1]) / size);
      if (cx < 0 || cy < 0 || cx >= width) return NaN;
      let out = NaN;
      for (const k of cells.get(cx + width * cy) || []) {
        const s = S[k], dx = e - s[0], dy = n - s[1];
        const u = s[2] * dx + s[3] * dy, v = s[4] * dx + s[5] * dy;
        if (u >= -1e-9 && v >= -1e-9 && 1 - u - v >= -1e-9) {
          const z = s[6] + u * s[7] + v * s[8];
          if (!(out >= z)) out = z;
        }
      }
      return out;
    },
  };
}

// --- a sketch as an option (domus.options.sketch) ------------------------------------------------------------------------

// sketch.behind: the points `depth` behind each point of a line, to the left or right looking along it, mitred.
function behind(line, side, depth) {
  const n = line.length, s = side === "left" ? 1.0 : -1.0, nrm = [];
  for (let i = 0; i + 1 < n; i++) {
    const dx = line[i + 1][0] - line[i][0], dy = line[i + 1][1] - line[i][1];
    const len = Math.max(hyp(dx, dy), 1e-9);
    nrm.push([-(dy / len) * s, (dx / len) * s]);
  }
  const out = new Array(n);
  out[0] = [line[0][0] + nrm[0][0] * depth, line[0][1] + nrm[0][1] * depth];
  out[n - 1] = [line[n - 1][0] + nrm[n - 2][0] * depth, line[n - 1][1] + nrm[n - 2][1] * depth];
  for (let i = 1; i < n - 1; i++) {
    let mx = nrm[i - 1][0] + nrm[i][0], my = nrm[i - 1][1] + nrm[i][1];
    const ml = Math.max(hyp(mx, my), 1e-9);
    mx /= ml;
    my /= ml;
    const m = Math.max(mx * nrm[i][0] + my * nrm[i][1], 0.5);
    out[i] = [line[i][0] + (mx * depth) / m, line[i][1] + (my * depth) / m];
  }
  return out;
}

// scheme.crossing's test, as shapely's LinearRing.is_simple has it: whether an outline (its corners in turn, [x, y],
// closed back to the first) crosses or touches itself, or doubles back along itself. A corner given twice in a row is
// no crossing; an outline of fewer than three corners doubles back.
export function crossesItself(corners) {
  const P = [];
  for (const [x, y] of corners) {
    const q = P[P.length - 1];
    if (!q || q[0] !== Number(x) || q[1] !== Number(y)) P.push([Number(x), Number(y)]);
  }
  if (P.length > 1 && P[0][0] === P[P.length - 1][0] && P[0][1] === P[P.length - 1][1]) P.pop();
  const n = P.length;
  if (n < 3) return true;
  const Q = P.map(([x, y]) => [x - P[0][0], y - P[0][1]]);
  const turn = (a, b, c) => Math.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]));
  const within = (a, b, p) => Math.min(a[0], b[0]) <= p[0] && p[0] <= Math.max(a[0], b[0])
    && Math.min(a[1], b[1]) <= p[1] && p[1] <= Math.max(a[1], b[1]);
  const meet = (a, b, c, d) => {                  // whether the sides a-b and c-d have a point in common
    const d1 = turn(c, d, a), d2 = turn(c, d, b), d3 = turn(a, b, c), d4 = turn(a, b, d);
    if (d1 * d2 < 0 && d3 * d4 < 0) return true;
    return (d1 === 0 && within(c, d, a)) || (d2 === 0 && within(c, d, b)) || (d3 === 0 && within(a, b, c)) || (d4 === 0 && within(a, b, d));
  };
  const back = (a, v, b) => turn(a, v, b) === 0 && (a[0] - v[0]) * (b[0] - v[0]) + (a[1] - v[1]) * (b[1] - v[1]) > 0;
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const [a, b] = [Q[i], Q[(i + 1) % n]], [c, d] = [Q[j], Q[(j + 1) % n]];
      if (j === i + 1) { if (back(a, b, d)) return true; }          // neighbours: only by doubling back
      else if (i === 0 && j === n - 1) { if (back(b, a, c)) return true; }
      else if (meet(a, b, c, d)) return true;
    }
  }
  return false;
}

// sketch.crossings, word for word: a message for each item of a sketch whose outline crosses itself, an area's or the
// ground a wall holds (its line, and the line `back` behind it). The viewer doesn't let one be drawn, and the server
// doesn't save one as an option: shapely's buffer(0) would reshape it in the build.
export function crossings(items, backDefault = 3.0) {
  const out = [];
  for (const it of items) {
    const en = (it.en || []).map((p) => [Number(p[0]), Number(p[1])]);
    if (it.kind === "area" && en.length >= 3) {
      if (crossesItself(en)) out.push(`The outline of ${it.name || `Area ${it.id}`} crosses itself: move its corners so that no side crosses another.`);
    } else if (it.kind === "wall" && en.length >= 2) {
      const held = (it.retains === "left" || it.retains === "right") && it.mode === "level" && it.tops && it.tops.length;
      if (held && crossesItself([...en, ...behind(en, it.retains, Number(it.back || backDefault)).reverse()])) {
        out.push(`The ground behind ${it.name || `Wall ${it.id}`} crosses itself: move the wall's corners, or hold less ground behind it.`);
      }
    }
  }
  return out;
}

// sketch.sketched: the option a sketch makes, keeping every existing wall.
function sketched(items, upstand, backDefault) {
  const walls = [], terraces = [];
  for (const it of items) {
    const en = (it.en || []).map((p) => [Number(p[0]), Number(p[1])]);
    if (it.kind === "wall" && en.length >= 2) {
      const tops = it.mode === "level" && it.tops && it.tops.length ? it.tops.map(Number) : null;
      const wall = { id: `S${it.id}`, line: en, tops, height: tops ? null : Number(it.height || it.height_above_ground || 0.6), side: null };
      walls.push(wall);
      if (it.retains !== "left" && it.retains !== "right") continue;
      wall.side = it.retains === "left" ? 1 : -1;
      if (!tops) continue;
      const back = behind(en, it.retains, Number(it.back || backDefault));
      const levels = tops.map((t) => t - upstand);
      terraces.push({ id: `S${it.id}-behind`, kind: "lawn", ring: [...en, ...back.slice().reverse()],
        z: [...levels, ...levels.slice().reverse()] });
    } else if (it.kind === "area" && en.length >= 3) {
      const grad = FALLS[it.fall] ?? 0.0;
      const dir = { north: [0, 1], east: [1, 0], south: [0, -1], west: [-1, 0] }[it.fall_to] || [0, 1];
      const mx = en.reduce((a, p) => a + p[0], 0) / en.length, my = en.reduce((a, p) => a + p[1], 0) / en.length;
      terraces.push({ id: `S${it.id}`, kind: KINDS[it.area] || "lawn", ring: en,
        z: en.map((p) => Number(it.level) - grad * ((p[0] - mx) * dir[0] + (p[1] - my) * dir[1])) });
    }
  }
  return { walls, terraces };
}

// --- the option worked out (scheme.Design, as preview.SketchDesign has it) ----------------------------------------------

// solids._capsule_span: where the line a + t d comes within r of the segment p-q.
function between(v0, v1, lo, hi) {
  if (v1 === 0) return lo < v0 && v0 < hi ? [-Infinity, Infinity] : null;
  const t0 = (lo - v0) / v1, t1 = (hi - v0) / v1;
  return t0 < t1 ? [t0, t1] : [t1, t0];
}

function capsuleSpan(a, d, p, q, r) {
  const spans = [];
  const A = d[0] * d[0] + d[1] * d[1];
  for (const c of [p, q]) {
    const fx = a[0] - c[0], fy = a[1] - c[1];
    const B = 2.0 * (d[0] * fx + d[1] * fy), C = fx * fx + fy * fy - r * r;
    const disc = B * B - 4.0 * A * C;
    if (A > 0 && disc > 0) {
      const root = Math.sqrt(disc);
      spans.push([(-B - root) / (2.0 * A), (-B + root) / (2.0 * A)]);
    }
  }
  const length = hyp(q[0] - p[0], q[1] - p[1]);
  if (length > 0) {
    const ux = (q[0] - p[0]) / length, uy = (q[1] - p[1]) / length, ox = a[0] - p[0], oy = a[1] - p[1];
    const across = between(ux * oy - uy * ox, ux * d[1] - uy * d[0], -r, r);
    const along = between(ux * ox + uy * oy, ux * d[0] + uy * d[1], 0.0, length);
    if (across && along && Math.max(across[0], along[0]) < Math.min(across[1], along[1])) {
      spans.push([Math.max(across[0], along[0]), Math.min(across[1], along[1])]);
    }
  }
  if (!spans.length) return null;
  return [Math.min(...spans.map((s) => s[0])), Math.max(...spans.map((s) => s[1]))];
}

// A terrace's corners as scheme.Terrace has them: shapely's buffer(0) turns a polygon clockwise and keeps its first
// corner.
function clockwise(ring) {
  const [x0, y0] = ring[0];
  let area2 = 0;
  for (let i = 0; i < ring.length; i++) {
    const [ax, ay] = ring[(i + ring.length - 1) % ring.length], [bx, by] = ring[i];
    area2 += (ax - x0) * (by - y0) - (bx - x0) * (ay - y0);
  }
  return area2 > 0 ? [ring[0], ...ring.slice(1).reverse()] : [...ring];
}

// solids.free_pieces: each terrace's outline less what a wall holds (within its thickness and HELD_M of its line),
// taken round it as the Python takes it. Two pieces that meet at a corner have the same cone round it, and where those
// coincide the first of them has that part of the plan (nearestCells), so the pieces come in the Python's order: round
// a corner where the level changes, which one has it changes the batter.
function freePieces(terraces, walls, held) {
  return terraces.map((t) => {
    const ring = clockwise(t.ring), pieces = [];
    ring.push(ring[0]);
    for (let k = 0; k + 1 < ring.length; k++) {
      const a = ring[k], b = ring[k + 1], d = [b[0] - a[0], b[1] - a[1]], length = hyp(d[0], d[1]);
      if (length <= 1e-12) continue;
      const spans = [];
      for (const w of walls) {
        const r = w.thickness + held;
        for (let i = 0; i + 1 < w.line.length; i++) {
          const s = capsuleSpan(a, d, w.line[i], w.line[i + 1], r);
          if (s && s[0] < 1.0 && s[1] > 0.0) spans.push([Math.max(s[0], 0.0), Math.min(s[1], 1.0)]);
        }
      }
      spans.sort((p, q) => p[0] - q[0] || p[1] - q[1]);
      let u = 0.0;
      for (const [h0, h1] of spans) {
        if ((h0 - u) * length > 1e-6) pieces.push([[a[0] + u * d[0], a[1] + u * d[1]], [a[0] + h0 * d[0], a[1] + h0 * d[1]]]);
        u = Math.max(u, h1);
      }
      if ((1.0 - u) * length > 1e-6) pieces.push([[a[0] + u * d[0], a[1] + u * d[1]], b]);
    }
    return pieces;
  });
}

// A terrace's own levels at its corners as a plane, when they lie on one: (z at its first corner, dz/de, dz/dn), from
// the three corners furthest apart, every corner within a nanometre of it; else null. Domus reads such a terrace on
// its corners' triangles, which give the plane wherever they're cut.
function planeOf(ring, z) {
  const [x0, y0] = ring[0];
  let best = null, area = 0;
  for (let i = 1; i < ring.length; i++) for (let j = i + 1; j < ring.length; j++) {
    const a = (ring[i][0] - x0) * (ring[j][1] - y0) - (ring[i][1] - y0) * (ring[j][0] - x0);
    if (Math.abs(a) > area) { area = Math.abs(a); best = [i, j]; }
  }
  if (!best || area < 1e-9) return null;
  const [i, j] = best;
  const ax = ring[i][0] - x0, ay = ring[i][1] - y0, az = z[i] - z[0], bx = ring[j][0] - x0, by = ring[j][1] - y0, bz = z[j] - z[0];
  const den = ax * by - ay * bx, ge = (az * by - ay * bz) / den, gn = (ax * bz - az * bx) / den;
  for (let k = 0; k < ring.length; k++) {
    if (Math.abs(z[0] + ge * (ring[k][0] - x0) + gn * (ring[k][1] - y0) - z[k]) > 1e-9) return null;
  }
  return [z[0], ge, gn];
}

// scheme.Terrace's triangles for a terrace with its own level at each corner (geo.mesh.delaunay_by_height), as
// [i, j, k] counter-clockwise: the Delaunay triangles of its corners (three corners make one where no other corner is
// inside their circle), but where two triangles' four corners lie within CIRCLE_NEAR_M of one circle, cut along the
// diagonal whose ends are closer in height, or as close, the one from the corner given first. Corners exactly on one
// circle are first laid as a fan, where Qhull lays them its own way; the cutting by height ends the same from either
// for four corners, but five or more on one circle can end either way, and then null: the terrace isn't previewed.
export const CIRCLE_NEAR_M = 0.005;     // geo.mesh.CIRCLE_NEAR_M

export function delaunay(pts, z) {
  const n = pts.length, [x0, y0] = pts[0];
  const P = pts.map(([x, y]) => [x - x0, y - y0]);
  const size = Math.max(...P.map(([x, y]) => Math.max(Math.abs(x), Math.abs(y)))) || 1;
  const area2 = (a, b, c) => (P[b][0] - P[a][0]) * (P[c][1] - P[a][1]) - (P[b][1] - P[a][1]) * (P[c][0] - P[a][0]);
  const inCircle = (a, b, c, d) => {              // > 0 where d is inside the circle through a, b, c (counter-clockwise)
    const [ax, ay] = [P[a][0] - P[d][0], P[a][1] - P[d][1]], [bx, by] = [P[b][0] - P[d][0], P[b][1] - P[d][1]];
    const [cx, cy] = [P[c][0] - P[d][0], P[c][1] - P[d][1]];
    return (ax * ax + ay * ay) * (bx * cy - cx * by) - (bx * bx + by * by) * (ax * cy - cx * ay) + (cx * cx + cy * cy) * (ax * by - bx * ay);
  };
  const flat = 1e-12 * size * size, round = 1e-10 * size ** 4;
  const tris = [], groups = new Map();
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) for (let k = j + 1; k < n; k++) {
    const o = area2(i, j, k);
    if (Math.abs(o) <= flat) continue;
    const [a, b, c] = o > 0 ? [i, j, k] : [i, k, j];
    let empty = true;
    const on = [];
    for (let m = 0; m < n && empty; m++) {
      if (m === a || m === b || m === c) continue;
      const v = inCircle(a, b, c, m);
      if (v > round) empty = false;
      else if (v >= -round) on.push(m);
    }
    if (!empty) continue;
    if (!on.length) tris.push([a, b, c]);
    else {
      const group = [a, b, c, ...on].sort((p, q) => p - q);
      groups.set(group.join(","), group);
    }
  }
  for (const group of groups.values()) {         // corners on one circle: a fan, to be cut by height below
    const cx = group.reduce((t, m) => t + P[m][0], 0) / group.length, cy = group.reduce((t, m) => t + P[m][1], 0) / group.length;
    const round_ = [...group].sort((p, q) => Math.atan2(P[p][1] - cy, P[p][0] - cx) - Math.atan2(P[q][1] - cy, P[q][0] - cx));
    for (let m = 1; m + 1 < round_.length; m++) tris.push([round_[0], round_[m], round_[m + 1]]);
  }
  if (!tris.length) return null;
  // mesh._off_circle: how far s lies from the circle through p, q and r
  const offCircle = (p, q, r, s) => {
    const [ax, ay] = P[p], [bx, by] = P[q], [cx, cy] = P[r];
    const den = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by));
    if (!(Math.abs(den) > 1e-12)) return Infinity;
    const a2 = ax * ax + ay * ay, b2 = bx * bx + by * by, c2 = cx * cx + cy * cy;
    const ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / den, uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / den;
    return Math.abs(Math.hypot(P[s][0] - ux, P[s][1] - uy) - Math.hypot(ax - ux, ay - uy));
  };
  const cross = (o, a, b) => (P[a][0] - P[o][0]) * (P[b][1] - P[o][1]) - (P[a][1] - P[o][1]) * (P[b][0] - P[o][0]);
  const shared = () => {                         // each edge two triangles share, in order of its corners: [a, b, c, d, t1, t2]
    const seen = new Map();
    tris.forEach((t, k) => {
      for (let e = 0; e < 3; e++) {
        const i = t[e], j = t[(e + 1) % 3], key = i < j ? `${i},${j}` : `${j},${i}`;
        if (!seen.has(key)) seen.set(key, []);
        seen.get(key).push([k, t[(e + 2) % 3]]);
      }
    });
    return [...seen.entries()].filter(([, v]) => v.length === 2)
      .map(([key, v]) => [...key.split(",").map(Number), v[0][1], v[1][1], v[0][0], v[1][0]])
      .sort((p, q) => p[0] - q[0] || p[1] - q[1]);
  };
  const near = ([a, b, c, d]) => Math.min(offCircle(a, b, c, d), offCircle(a, b, d, c)) <= CIRCLE_NEAR_M;
  for (let pass = 0; pass < 10 * tris.length + 10; pass++) {
    const used = new Set();
    let cut = 0;
    for (const [a, b, c, d, t1, t2] of shared()) {
      if (used.has(t1) || used.has(t2) || !(cross(c, d, a) * cross(c, d, b) < 0) || !near([a, b, c, d])) continue;
      const dab = Math.abs(z[a] - z[b]), dcd = Math.abs(z[c] - z[d]);
      if (!(dcd < dab - 1e-9 || (Math.abs(dcd - dab) <= 1e-9 && Math.min(c, d) < Math.min(a, b)))) continue;
      tris[t1] = [c, d, a];
      tris[t2] = [d, c, b];
      used.add(t1).add(t2);
      cut++;
    }
    if (!cut) break;
  }
  // five or more corners on one circle: two such pairs share a triangle
  const close = shared().filter(near), seen = new Set();
  for (const [, , , , t1, t2] of close) {
    if (seen.has(t1) || seen.has(t2)) return null;
    seen.add(t1).add(t2);
  }
  return tris.map(([a, b, c]) => (cross(a, b, c) < 0 ? [a, c, b] : [a, b, c]));
}

// A terrace's level at (e, n) on its triangles (scheme.Terrace.z): in the one it's in; a micrometre or less outside
// them, the nearest's carried on to it (Terrace._carried); further, the nearest corner's.
function onTriangles(pts, z, tris) {
  const T = tris.map(([a, b, c]) => {
    const [ax, ay] = pts[a], [bx, by] = pts[b], [cx, cy] = pts[c];
    const den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
    return { a, b, c, ax, ay, bx, by, cx, cy, den };
  });
  const weights = (t, e, n) => {
    const u = ((t.by - t.cy) * (e - t.cx) + (t.cx - t.bx) * (n - t.cy)) / t.den;
    const v = ((t.cy - t.ay) * (e - t.cx) + (t.ax - t.cx) * (n - t.cy)) / t.den;
    return [u, v, 1 - u - v];
  };
  const level = (t, w) => w[0] * z[t.a] + w[1] * z[t.b] + w[2] * z[t.c];
  const gap = (t, e, n) => Math.min(...[[t.ax, t.ay, t.bx, t.by], [t.bx, t.by, t.cx, t.cy], [t.cx, t.cy, t.ax, t.ay]].map(([px, py, qx, qy]) => {
    const dx = qx - px, dy = qy - py, u = Math.min(1, Math.max(0, ((e - px) * dx + (n - py) * dy) / (dx * dx + dy * dy)));
    return hyp(e - px - u * dx, n - py - u * dy);
  }));
  return (e, n) => {
    let near = null, best = Infinity;
    for (const t of T) {
      const w = weights(t, e, n);
      if (w[0] >= -1e-9 && w[1] >= -1e-9 && w[2] >= -1e-9) return level(t, w);
      const g = gap(t, e, n);
      if (g < best) { best = g; near = t; }
    }
    if (near && best < 1e-6) return level(near, weights(near, e, n));
    let corner = 0, d = Infinity;
    pts.forEach(([x, y], m) => { const g = hyp(e - x, n - y); if (g < d) { d = g; corner = m; } });
    return z[corner];
  };
}

function makeDesign(opt, defaults, rules, ground, garden) {
  const terraces = opt.terraces.map((t) => {
    const isLevel = t.z.every((z) => z === t.z[0]);
    const plane = isLevel ? null : planeOf(t.ring, t.z);
    const [x0, y0] = t.ring[0];
    const tris = isLevel || plane ? null : delaunay(t.ring, t.z);
    const zAt = isLevel ? () => t.z[0] : plane ? (e, n) => plane[0] + plane[1] * (e - x0) + plane[2] * (n - y0)
      : tris ? onTriangles(t.ring, t.z, tris) : null;
    return { ...t, batter: Number(t.batter ?? defaults.batter), paving: t.kind === "paving" ? Number(defaults.paving_m ?? 0.2) : 0.0,
      level: t.z[0], isLevel, plane, tris, zAt, lowest: Math.min(...t.z), highest: Math.max(...t.z) };
  });
  const walls = opt.walls.map((w) => {         // scheme.option_wall: how it's built, from the options' defaults
    const thickness = Number(defaults.thickness_m), depth = Number(defaults.depth_m || thickness);
    return { ...w, thickness, depth, upstand: Number(defaults.upstand_m),
      embed: [Number(defaults.embed_m ?? 0), Number(defaults.embed_per_m ?? 0), Number(defaults.embed_courses ?? 0)],
      seal: Number(defaults.seal_m ?? 0), bedding: Number(defaults.bedding_m ?? 0), base: Number(defaults.base_m ?? 0),
      trench: Number(defaults.trench_m || depth) };
  });
  const pieces = freePieces(terraces, walls, rules.held_m);
  const D = { terraces, walls, pieces, garden, ground, rules, defaults };

  // solids.batter_levels: the level the batters raise the ground to at (e, n), and the level they cut it to.
  D.batters = (e, n) => {
    let raise = -Infinity, lower = Infinity;
    terraces.forEach((t, k) => {
      if (!pieces[k].length) return;
      let best = Infinity, fx = 0, fy = 0;
      for (const [a, b] of pieces[k]) {
        const abx = b[0] - a[0], aby = b[1] - a[1];
        const u = Math.min(1.0, Math.max(0.0, ((e - a[0]) * abx + (n - a[1]) * aby) / (abx * abx + aby * aby)));
        const x = a[0] + u * abx, y = a[1] + u * aby, d = hyp(e - x, n - y);
        if (d < best) { best = d; fx = x; fy = y; }
      }
      if (best < rules.reach_m) {
        const ze = t.isLevel ? t.level : t.zAt(fx, fy);
        raise = Math.max(raise, ze - best / t.batter);
        lower = Math.min(lower, ze + best / t.batter);
      }
    });
    return [raise, lower];
  };

  // scheme.Design._terraced (and sample: a sketch has no steps): the first terrace a point is in gives its level, and
  // outside them the batters' larger change.
  D.sample = (e, n) => {
    const h0 = ground.z(e, n);
    if (!Number.isFinite(h0) || !inRings(garden.rings, e, n)) return h0;
    for (const t of terraces) if (inRings([t.ring], e, n)) return t.zAt(e, n);
    const [r, l] = D.batters(e, n);
    const up = Math.max(r - h0, 0), down = Math.max(h0 - l, 0);
    return up >= down ? h0 + up : h0 - down;
  };

  // scheme.Design.wall_levels: each wall's side, toe and top along it.
  D.wallLevels = () => walls.map((w) => {
    const { pts, chain } = resampleLine(w.line, rules.wall_step_m);
    let nrm = lineNormals(pts), side = w.side;
    if (side == null) {
      let sum = 0, count = 0;
      pts.forEach((p, i) => {
        const v = D.sample(p[0] + nrm[i][0] * 0.8, p[1] + nrm[i][1] * 0.8) - D.sample(p[0] - nrm[i][0] * 0.8, p[1] - nrm[i][1] * 0.8);
        if (!Number.isNaN(v)) { sum += v; count++; }
      });
      side = count && sum / count > 0 ? 1 : -1;
    }
    nrm = nrm.map((v) => [v[0] * side, v[1] * side]);
    const retained = pts.map((p, i) => D.sample(p[0] + nrm[i][0] * (w.depth + rules.back_probe_m), p[1] + nrm[i][1] * (w.depth + rules.back_probe_m)));
    const toe = pts.map((p, i) => D.sample(p[0] - nrm[i][0] * rules.toe_probe_m, p[1] - nrm[i][1] * rules.toe_probe_m));
    let top;
    if (w.tops) {
      const full = [0];
      for (let i = 1; i < w.line.length; i++) full.push(full[i - 1] + hyp(w.line[i][0] - w.line[i - 1][0], w.line[i][1] - w.line[i - 1][1]));
      top = chain.map((c) => interp(c, full, w.tops));
    } else if (w.height != null) {
      top = toe.map((z) => z + w.height);
    } else {
      top = retained.map((z) => z + w.upstand);
    }
    top = top.map((z, i) => Math.max(z, toe[i] + 0.05));
    // its first course below the ground at its toe (a sketch's walls name no blocks, so no whole courses)
    const [least, perM] = w.embed;
    const embedment = top.map((z, i) => Math.max(Math.max(least, perM * Math.max(z - toe[i], 0.0)), 0.0));
    return { ...w, side, chain, toe, top, samples: pts, normals: nrm, embedment };
  });
  return D;
}

// scheme.trench_bottom: a new wall's trench's bottom at each of its toe's samples, its first course (walls.foot: its
// toe less its embedment, where it has one) less its bedding and base course.
function trenchBottom(w) {
  const sunk = w.embedment.some((v) => v > 0);
  return w.toe.map((z, i) => (sunk ? z - w.embedment[i] : z) - w.bedding - w.base);
}

// scheme.drainage: where a wall's trench reaches past its body and its option gives soil over the aggregate behind
// it, that strip of plan (as rings), its line, the trench's bottom along it and the soil's depth; else null.
function drainageOf(w) {
  if (w.trench <= w.depth + 1e-9 || w.seal <= 0) return null;
  const ring = [...offsetBehind(w.line, w.depth, w.side), ...offsetBehind(w.line, w.trench, w.side).reverse()];
  return { ring, line: w.line, chain: w.chain, toe: trenchBottom(w), seal: w.seal };
}

// --- solids (domus.options.solids), with Manifold ----------------------------------------------------------------------

export function makePreview(wasm, model) {
  const { Manifold, CrossSection, Mesh } = wasm;
  const [E0, N0, H0] = model.origin.map(Number);
  const rg = model.regrade;
  const rules = rg.rules, defaults = rg.defaults;
  const onGrid = (pts) => pts.map((p) => [Number(p[0]) + E0, N0 - Number(p[1])]);
  const gardenRings = rg.area.map(onGrid);
  const gb = boundsOf(gardenRings);
  const origin = [Math.floor(gb[0]), Math.floor(gb[1])];
  const [ox, oy] = origin;
  const garden = { rings: gardenRings, bounds: gb };

  // The ground layer, each corner about the origin in single precision, as a WebGL mesh holds it (preview.ModelGround).
  const layer = model.layers.find((l) => l.id === "ground");
  const P = layer.positions, nv = P.length / 3;
  const GV = new Float64Array(3 * nv);
  for (let i = 0; i < nv; i++) {
    GV[3 * i] = Math.fround((Number(P[3 * i]) + E0) - ox) + ox;
    GV[3 * i + 1] = Math.fround((N0 - Number(P[3 * i + 2])) - oy) + oy;
    GV[3 * i + 2] = Math.fround(Number(P[3 * i + 1]) + H0);
  }
  const GF = Uint32Array.from(layer.indices);
  const ground = makeSurface(GV, GF);
  const vmin = [Infinity, Infinity], vmax = [-Infinity, -Infinity];
  for (let i = 0; i < nv; i++) {
    vmin[0] = Math.min(vmin[0], GV[3 * i]); vmin[1] = Math.min(vmin[1], GV[3 * i + 1]);
    vmax[0] = Math.max(vmax[0], GV[3 * i]); vmax[1] = Math.max(vmax[1], GV[3 * i + 1]);
  }

  // The existing walls, as the solids take them (preview.old_walls): plan, line, distances along it, toe.
  const old = rg.walls.map((w) => ({ id: w.id, plan: w.plan.map(onGrid), line: onGrid(w.line), chain: w.chain.map(Number),
    toe: w.toe.map((z) => Number(z) + H0) }));

  let scope = [], keeper = scope;
  const K = (x) => { keeper.push(x); return x; };
  // Kept from one sketch to the next while it's dragged, as long as they hold (each with what it made, to delete):
  // the ground over a window on a 4 m grid round the sketch's, with the soil before on it; and each existing wall's
  // pieces' nearest parts, which never change.
  const WINDOW_STEP_M = 4.0;
  let held = { key: null, made: [] };
  const helds = new Map();          // the last two kept, by key, the latest last
  const cellsOf = new Map(), cellsMade = [];
  const keepFor = (list, make) => {
    const was = keeper;
    keeper = list;
    try { return make(); } finally { keeper = was; }
  };
  // With `profile`, each step is made to finish and timed on its own (Manifold otherwise does the work only when a
  // result is asked of it, so the time would all land on the last step).
  let prof = null, last = 0;
  const mark = (name, ...ms) => {
    if (!prof) return;
    for (const m of ms) m.isEmpty();
    const now = performance.now();
    prof[name] = (prof[name] || 0) + now - last;
    last = now;
  };
  const local = (pts) => pts.map((p) => [p[0] - ox, p[1] - oy]);
  const sectionOf = (rings) => K(new CrossSection(rings.map(local), "EvenOdd"));
  const prism = (cs, low, high) => K(K(cs.extrude(high - low)).translate([0, 0, low]));
  const union = (parts) => {
    const some = parts.filter((p) => !p.isEmpty());
    return some.length ? K(Manifold.union(some)) : K(Manifold.union([]));
  };
  const unionCS = (parts) => (parts.length ? K(CrossSection.union(parts)) : K(CrossSection.union([])));
  const outline = (m) => {
    const polys = K(m.project()).toPolygons();
    return polys.length ? K(new CrossSection(polys, "Positive")) : K(CrossSection.union([]));
  };
  const boxCS = (b) => sectionOf([[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]]);

  function splitByPlane(m, point, gradient) {
    const len = hyp(gradient[0], gradient[1], -1.0);
    const n = [gradient[0] / len, gradient[1] / len, -1.0 / len];
    const c = point[2] - gradient[0] * point[0] - gradient[1] * point[1];
    return [K(m.trimByPlane(n, -c / len)), K(m.trimByPlane([-n[0], -n[1], -n[2]], c / len))];
  }

  // solids.close_surface: a height field of triangles closed into a solid, its edge carried down to a flat bottom.
  function closeSurface(V, faces, bottom) {
    let f = [];
    for (const [a, b, c] of faces) {
      if ((V[b][0] - V[a][0]) * (V[c][1] - V[a][1]) - (V[b][1] - V[a][1]) * (V[c][0] - V[a][0]) > 0) f.push([a, b, c]);
    }
    const source = V.map((_, i) => i);
    // _unpinch: where the surface only touches itself at a corner, each fan round it takes its own copy of it.
    const border = (fs) => {        // each edge only one triangle has, the way it runs it
      const big = 4 * V.length + 8, have = new Set(), out = [];
      for (const t of fs) { have.add(t[0] * big + t[1]); have.add(t[1] * big + t[2]); have.add(t[2] * big + t[0]); }
      for (const t of fs) {
        if (!have.has(t[1] * big + t[0])) out.push([t[0], t[1]]);
        if (!have.has(t[2] * big + t[1])) out.push([t[1], t[2]]);
        if (!have.has(t[0] * big + t[2])) out.push([t[2], t[0]]);
      }
      return out;
    };
    const starts = new Map();
    for (const [p] of border(f)) starts.set(p, (starts.get(p) || 0) + 1);
    for (const [vtx, many] of starts) {
      if (many < 2) continue;
      const around = f.map((t, i) => (t.includes(vtx) ? i : -1)).filter((i) => i >= 0);
      const group = new Map(around.map((t) => [t, t]));
      const root = (t) => { while (group.get(t) !== t) { group.set(t, group.get(group.get(t))); t = group.get(t); } return t; };
      const byNeighbour = new Map();
      for (const t of around) for (const w of f[t]) if (w !== vtx) { if (!byNeighbour.has(w)) byNeighbour.set(w, []); byNeighbour.get(w).push(t); }
      for (const ts of byNeighbour.values()) for (const t of ts.slice(1)) group.set(root(t), root(ts[0]));
      const fans = new Map();
      for (const t of around) { const r = root(t); if (!fans.has(r)) fans.set(r, []); fans.get(r).push(t); }
      [...fans.values()].slice(1).forEach((fan) => {
        source.push(vtx);
        for (const t of fan) f[t] = f[t].map((x) => (x === vtx ? source.length - 1 : x));
      });
    }
    const used = [...new Set(f.flat())].sort((p, q) => p - q);
    const index = new Map(used.map((u, i) => [u, i]));
    f = f.map((t) => t.map((x) => index.get(x)));
    const v = used.map((u) => V[source[u]]);
    const n = v.length;
    const next = new Map(border(f).map(([p, q]) => [p, q]));
    const loops = [], seen = new Set();
    for (const start of next.keys()) {
      if (seen.has(start)) continue;
      const loop = [];
      let p = start;
      while (!seen.has(p)) { seen.add(p); loop.push(p); p = next.get(p); }
      loops.push(loop);
    }
    const tris = [...f];
    for (const loop of loops) {
      loop.forEach((p, i) => {
        const q = loop[(i + 1) % loop.length];
        tris.push([p, p + n, q + n], [p, q + n, q]);
      });
    }
    const order = loops.flat();
    for (const [a, b, c] of wasm.triangulate(loops.map((loop) => loop.map((p) => [v[p][0], v[p][1]])))) {
      tris.push([order[c] + n, order[b] + n, order[a] + n]);
    }
    const vert = new Float32Array(6 * n);
    v.forEach((p, i) => { vert.set([p[0], p[1], p[2]], 3 * i); vert.set([p[0], p[1], bottom], 3 * (i + n)); });
    const m = K(new Manifold(new Mesh({ numProp: 3, vertProperties: vert, triVerts: Uint32Array.from(tris.flat()) })));
    if (m.status() !== "NoError") throw new Error(`the ground isn't a closed solid: ${m.status()}`);
    return m;
  }

  // solids.cone_hull
  function coneHull(a, za, b, zb, batter, far, segments, up) {
    const s = up ? 1.0 : -1.0, R = rules.reach_m, pts = [];
    for (const [p, z] of [[a, za], [b, zb]]) {
      pts.push([p[0], p[1], z]);
      for (const level of [z + (s * R) / batter, far]) {
        for (let k = 0; k < segments; k++) {
          const ang = (2 * Math.PI * k) / segments;
          pts.push([p[0] + Math.cos(ang) * R, p[1] + Math.sin(ang) * R, level]);
        }
      }
    }
    return K(Manifold.hull(pts));
  }

  // solids._overlay: the parts of the plan the shapes divide it into, each with the shapes it's in.
  function overlay(shapes) {
    let cells = [];
    shapes.forEach((cs, i) => {
      if (cs.isEmpty()) return;
      const out = [];
      let rest = cs;
      for (const [cell, members] of cells) {
        const both = K(cell.intersect(cs)), left = K(cell.subtract(cs));
        if (!both.isEmpty()) out.push([both, [...members, i]]);
        if (!left.isEmpty()) out.push([left, members]);
        rest = K(rest.subtract(cell));
      }
      if (!rest.isEmpty()) out.push([rest, [i]]);
      cells = out;
    });
    return cells;
  }

  // solids._nearest_cells: for each straight piece of a line, the part of the plan within `reach` nearest it. Where two
  // pieces' cones coincide, round a corner they share, the first piece has it: each cone stands TIE_LIFT_M over the
  // one before, so it's not left to which face Manifold's union keeps.
  function nearestCells(ends, segments, reach) {
    const ring = [];
    for (let k = 0; k < segments; k++) {
      const ang = (2 * Math.PI * k) / segments;
      ring.push([Math.cos(ang) * reach, Math.sin(ang) * reach]);
    }
    const ids = new Map(), distance = [];
    ends.forEach(([a, b], k) => {
      const lift = k * TIE_LIFT_M, pts = [[a[0], a[1], lift], [b[0], b[1], lift]];
      for (const p of [a, b]) for (const r of ring) pts.push([p[0] + r[0], p[1] + r[1], reach + lift]);
      const m = K(K(Manifold.hull(pts)).asOriginal());
      ids.set(m.originalID(), k);
      distance.push(m);
    });
    const mesh = union(distance).getMesh();
    const v = mesh.vertProperties, np = mesh.numProp, tv = mesh.triVerts, ntri = tv.length / 3;
    const owner = new Int32Array(ntri).fill(-1);
    for (let r = 0; r < mesh.runOriginalID.length; r++) {
      const k = ids.has(mesh.runOriginalID[r]) ? ids.get(mesh.runOriginalID[r]) : -1;
      for (let t = mesh.runIndex[r] / 3; t < mesh.runIndex[r + 1] / 3; t++) owner[t] = k;
    }
    const tris = ends.map(() => []);
    for (let t = 0; t < ntri; t++) {
      const [a, b, c] = [tv[3 * t], tv[3 * t + 1], tv[3 * t + 2]].map((i) => [v[np * i], v[np * i + 1], v[np * i + 2]]);
      const nz = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
      if (nz < -1e-12 && owner[t] >= 0) tris[owner[t]].push([[c[0], c[1]], [b[0], b[1]], [a[0], a[1]]]);
    }
    return tris.map((ts) => (ts.length ? K(new CrossSection(ts, "NonZero")) : K(CrossSection.union([]))));
  }

  // solids._square_column: heights along a straight line, carried square across it, and a flat level below (or above).
  function squareColumn(start, along, t, z, level, far, up) {
    const line = t.map((ti, i) => [ti, z[i]]);
    const ring = up ? [...line, [line[line.length - 1][0], level], [line[0][0], level]]
      : [[line[0][0], level], [line[line.length - 1][0], level], ...line.slice().reverse()];
    const m = K(K(new CrossSection([ring], "Positive")).extrude(2.0 * far));
    const nx = -along[1], ny = along[0];
    return K(m.transform([along[0], along[1], 0, 0, 0, 0, 1, 0, -nx, -ny, 0, 0, start[0] + far * nx, start[1] + far * ny, 0, 1]));
  }

  // solids._toe_columns: everything over a wall's plan below its toe (from `low`) and above it (to `high`); the toe at
  // a point is the toe at the nearest point of the line. Each piece's nearest part is worked out once for a wall.
  const cellsFor = new Map();
  function toeColumns(body, low, high, segments) {
    const pts = local(body.line);
    const at = [0];
    for (let i = 1; i < pts.length; i++) at.push(at[i - 1] + hyp(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    const keep = [];
    for (let k = 0; k + 1 < pts.length; k++) if (at[k + 1] - at[k] > 1e-9) keep.push(k);
    const b = boundsOf([...body.plan, body.line]);
    const far = hyp(b[2] - b[0], b[3] - b[1]) + 1.0;
    const cs = K(K(new CrossSection(body.plan.map(local), "EvenOdd")).offset(2 * EDGE_M, "Miter"));
    let cells = [null];
    if (keep.length > 1) {
      const key = body.id != null ? `${body.id}|${segments}` : null;
      if (key && !cellsOf.has(key)) cellsOf.set(key, keepFor(cellsMade, () => nearestCells(keep.map((k) => [pts[k], pts[k + 1]]), segments, far)));
      cells = key ? cellsOf.get(key) : nearestCells(keep.map((k) => [pts[k], pts[k + 1]]), segments, far);
    }
    const below = [], above = [];
    keep.forEach((k, i) => {
      const cell = keep.length > 1 ? cells[i] : null;
      const region = cell ? K(cs.intersect(K(cell.offset(EDGE_M, "Miter")))) : cs;
      if (region.isEmpty()) return;
      const length = at[k + 1] - at[k];
      const along = [(pts[k + 1][0] - pts[k][0]) / length, (pts[k + 1][1] - pts[k][1]) / length];
      const inner = body.chain.filter((c) => c > at[k] + 1e-9 && c < at[k + 1] - 1e-9).map((c) => c - at[k]);
      const t = [...new Set([-far, 0.0, ...inner, length, length + far])].sort((p, q) => p - q);
      const z = t.map((ti) => interp(at[k] + Math.min(Math.max(ti, 0.0), length), body.chain, body.toe));
      const col = prism(region, low - 1.0, high + 1.0);
      below.push(K(squareColumn(pts[k], along, t, z, low, far, false).intersect(col)));
      above.push(K(squareColumn(pts[k], along, t, z, high, far, true).intersect(col)));
    });
    return [union(below), union(above)];
  }

  // solids._batter, for a level terrace: the hulls of cones along each straight piece of its free edge.
  // A fill cone is below its terrace's level and a cut cone above it, so where all the ground within reach of the
  // terrace's free edge is above the level its fill cones change nothing, and where it's all below its cut cones
  // don't: those are left out (`fills`, `cuts` false), which leaves the solids as they were.
  function batter(t, pieces, low, high, segments, fills = true, cuts = true) {
    if (!t.isLevel) return sloping(t, pieces, low, high, segments, fills, cuts);
    const hulls = pieces.map(([a, b]) => {
      const p = [a[0] - ox, a[1] - oy], q = [b[0] - ox, b[1] - oy];
      return [fills ? coneHull(p, t.level, q, t.level, t.batter, low, segments, false) : null,
        cuts ? coneHull(p, t.level, q, t.level, t.batter, high, segments, true) : null];
    });
    return [union(hulls.map((h) => h[0]).filter(Boolean)), union(hulls.map((h) => h[1]).filter(Boolean))];
  }

  // solids._batter's second way: where the level changes along the free edge, each straight piece gives the batter
  // only in its own part of the plan (where it's the nearest piece), and there it's a wedge along the piece, or a cone
  // round whichever end is nearest. A piece of a plane's edge needs no cutting where the level bends, as it doesn't.
  function sloping(t, given, low, high, segments, fills, cuts) {
    const pieces = t.tris ? bent(t, given) : given;
    const R = rules.reach_m, ends = pieces.map(([a, b]) => [[a[0] - ox, a[1] - oy], [b[0] - ox, b[1] - oy]]);
    const bottom = Math.min(low, high) - 1.0, roof = Math.max(low, high) + 1.0;
    const region = (corners) => K(new CrossSection([corners], "NonZero"));
    const cells = nearestCells(ends, segments, R), raised = [], cut = [];
    ends.forEach(([a, b], k) => {
      const cell = cells[k];
      if (cell.isEmpty()) return;
      const za = t.zAt(pieces[k][0][0], pieces[k][0][1]), zb = t.zAt(pieces[k][1][0], pieces[k][1][1]);
      const len = hyp(b[0] - a[0], b[1] - a[1]), along = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
      const across = [-along[1] * R, along[0] * R], far = [along[0] * 2 * R, along[1] * 2 * R];
      const plus = (p, q, f = 1) => [p[0] + f * q[0], p[1] + f * q[1]];
      const slab = region([plus(a, across, -1), plus(b, across, -1), plus(b, across), plus(a, across)]);
      const beyond = [[a, za, region([plus(a, across, -1), plus(a, across), plus(plus(a, across), far, -1), plus(plus(a, across, -1), far, -1)])],
        [b, zb, region([plus(b, across, -1), plus(plus(b, across, -1), far), plus(plus(b, across), far), plus(b, across)])]];
      for (const [up, out, end, want] of [[false, raised, low, fills], [true, cut, high, cuts]]) {
        if (!want) continue;
        const drop = ((up ? 1.0 : -1.0) * R) / t.batter, pts = [];
        for (const [q, zp] of [[a, za], [b, zb]]) {
          pts.push([q[0], q[1], zp], [...plus(q, across, -1), zp + drop], [...plus(q, across), zp + drop],
            [...plus(q, across, -1), end], [...plus(q, across), end]);
        }
        out.push(K(K(Manifold.hull(pts)).intersect(prism(K(slab.intersect(cell)), bottom, roof))));
        for (const [p, zp, side] of beyond) {
          const part = K(side.intersect(cell));
          if (!part.isEmpty()) out.push(K(coneHull(p, zp, p, zp, t.batter, end, segments, up).intersect(prism(part, bottom, roof))));
        }
      }
    });
    return [union(raised), union(cut)];
  }

  // solids._straight: the straight pieces of a terrace's free edge cut again where they cross an edge of its triangles,
  // where its level bends.
  function bent(t, pieces) {
    const edges = new Map();
    for (const [a, b, c] of t.tris) for (const [i, j] of [[a, b], [b, c], [c, a]]) edges.set(`${Math.min(i, j)},${Math.max(i, j)}`, [t.ring[i], t.ring[j]]);
    const out = [];
    for (const [p, q] of pieces) {
      const dx = q[0] - p[0], dy = q[1] - p[1], len = hyp(dx, dy), cuts = new Set();
      for (const [r, s] of edges.values()) {
        const ex = s[0] - r[0], ey = s[1] - r[1], den = dx * ey - dy * ex;
        if (Math.abs(den) < 1e-15) continue;                       // along it, or never meeting it
        const u = ((r[0] - p[0]) * ey - (r[1] - p[1]) * ex) / den, v = ((r[0] - p[0]) * dy - (r[1] - p[1]) * dx) / den;
        if (u >= 0 && u <= 1 && v >= 0 && v <= 1) cuts.add(u * len);
      }
      const at = [0, ...[...cuts].filter((c) => c > 1e-9 && c < len - 1e-9).sort((a, b) => a - b), len];
      for (let k = 0; k + 1 < at.length; k++) {
        if (at[k + 1] - at[k] > 1e-9) out.push([[p[0] + (dx / len) * at[k], p[1] + (dy / len) * at[k]], [p[0] + (dx / len) * at[k + 1], p[1] + (dy / len) * at[k + 1]]]);
      }
    }
    return out;
  }

  // The lowest and highest ground within `reach` of straight pieces (site metres): the corners of the triangles near.
  function groundRange(pieces, reach) {
    const b = boundsOf([pieces.flat()]);
    const i0 = Math.floor((b[0] - ox - reach) / NEAR_STEP_M) - 1, j0 = Math.floor((b[1] - oy - reach) / NEAR_STEP_M) - 1;
    const i1 = Math.floor((b[2] - ox + reach) / NEAR_STEP_M) + 1, j1 = Math.floor((b[3] - oy + reach) / NEAR_STEP_M) + 1;
    let lo = Infinity, hi = -Infinity;
    for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) {
      for (const f of byCell.get(`${i},${j}`) || []) for (let c = 0; c < 3; c++) {
        const z = GV[3 * GF[3 * f + c] + 2];
        if (z < lo) lo = z;
        if (z > hi) hi = z;
      }
    }
    return [lo, hi];
  }

  // solids._cut_wins: where one batter cuts the ground by more than another raises it, read on a grid.
  function cutWins(region, design, step) {
    const rb = region.bounds();
    const x0 = rb.min[0], y0 = rb.min[1], x1 = rb.max[0], y1 = rb.max[1];
    const arange = (start, stop) => {
      const n = Math.max(Math.ceil((stop - start) / step), 0), out = [];
      for (let i = 0; i < n; i++) out.push(start + i * step);
      return out;
    };
    const xs = arange(x0 - step, x1 + 2 * step), ys = arange(y0 - step, y1 + 2 * step);
    const nx = xs.length, ny = ys.length, gap = new Float64Array(nx * ny);
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const e = xs[i] + ox, n = ys[j] + oy;
      const [r, l] = design.batters(e, n), h0 = ground.z(e, n);
      gap[j * nx + i] = Math.max(r - h0, 0) - Math.max(h0 - l, 0);
    }
    const below = (j, i) => gap[j * nx + i] < 0;
    const pieces = [];
    for (let j = 0; j + 1 < ny; j++) {
      let start = -1;
      for (let i = 0; i <= nx - 1; i++) {
        const whole = i < nx - 1 && below(j, i) && below(j, i + 1) && below(j + 1, i) && below(j + 1, i + 1);
        if (whole && start < 0) start = i;
        if (!whole && start >= 0) {
          pieces.push([[xs[start], ys[j]], [xs[i], ys[j]], [xs[i], ys[j + 1]], [xs[start], ys[j + 1]]]);
          start = -1;
        }
      }
    }
    for (let j = 0; j + 1 < ny; j++) for (let i = 0; i + 1 < nx; i++) {
      const any = below(j, i) || below(j, i + 1) || below(j + 1, i) || below(j + 1, i + 1);
      const all = below(j, i) && below(j, i + 1) && below(j + 1, i) && below(j + 1, i + 1);
      if (!any || all) continue;
      for (const tri of [[[j, i], [j, i + 1], [j + 1, i + 1]], [[j, i], [j + 1, i + 1], [j + 1, i]]]) {
        const g = tri.map(([a, b]) => gap[a * nx + b]);
        if (!g.some((x) => x < 0)) continue;
        const p = tri.map(([a, b]) => [xs[b], ys[a]]), out = [];
        for (let k = 0; k < 3; k++) {
          const a = k, b = (k + 1) % 3;
          if (g[a] < 0) out.push(p[a]);
          if ((g[a] < 0) !== (g[b] < 0)) {
            const s = g[a] / (g[a] - g[b]);
            out.push([p[a][0] + s * (p[b][0] - p[a][0]), p[a][1] + s * (p[b][1] - p[a][1])]);
          }
        }
        pieces.push(out);
      }
    }
    if (!pieces.length) return K(CrossSection.union([]));
    return K(K(new CrossSection(pieces, "NonZero")).intersect(region));
  }

  // solids.option_window: the box round all an option can change, as far as a batter reaches and a metre more.
  function optionWindow(design, walls) {
    const boxes = design.terraces.map((t) => boundsOf([t.ring]));
    for (const w of walls) {       // a wall's plan reaches a micrometre past its outline (scheme.wall_body)
      const b = boundsOf([[...w.line, ...offsetBehind(w.line, w.trench, w.side)]]);
      boxes.push([b[0] - EDGE_M, b[1] - EDGE_M, b[2] + EDGE_M, b[3] + EDGE_M]);
    }
    if (!boxes.length) return null;
    const reach = rules.reach_m + 1.0;
    return [Math.min(...boxes.map((b) => b[0])) - reach, Math.min(...boxes.map((b) => b[1])) - reach,
      Math.max(...boxes.map((b) => b[2])) + reach, Math.max(...boxes.map((b) => b[3])) + reach];
  }

  function update(items, { segments = PREVIEW_SEGMENTS, conflictStep = CONFLICT_STEP_M, meshes = true, profile = false } = {}) {
    const t0 = performance.now();
    scope = [];
    prof = profile ? {} : null;
    last = t0;
    try {
      const opt = sketched(items, Number(defaults.upstand_m), Number(rules.sketch_back_m));
      if (!opt.walls.length && !opt.terraces.length) return { empty: true };
      const design = makeDesign(opt, defaults, rules, ground, garden);
      if (design.terraces.some((t) => !t.zAt)) {
        return { unsupported: "a terrace with five or more of its corners on one circle" };
      }
      const walls = design.wallLevels();
      const out = solids(design, walls, optionWindow(design, walls), segments, conflictStep);
      out.ms = performance.now() - t0;
      if (meshes) out.meshes = drawn(out, walls);
      if (prof) out.profile = prof;
      delete out.parts;
      return out;
    } finally {
      for (const x of scope) x.delete();
      scope = [];
    }
  }

  // The ground over window `q` as a solid, its bottom and top below and above everything (the ground there, and the
  // levels from `lowest` to `highest`), and the soil before on it: the ground, but at its toe inside an existing
  // wall, the lowest where two meet. Kept while `q` and the bottom and top hold.
  let tin = { key: null };
  function groundOver(q, lowest, highest, segments) {
    if (tin.key !== q.join(",")) {        // the ground's triangles near the window
      const faces = [], V = [], vid = new Map();
      let zlo = Infinity, zhi = -Infinity;
      for (let f = 0; f < GF.length / 3; f++) {
        const a = GF[3 * f], b = GF[3 * f + 1], c = GF[3 * f + 2];
        const mx = (GV[3 * a] + GV[3 * b] + GV[3 * c]) / 3, my = (GV[3 * a + 1] + GV[3 * b + 1] + GV[3 * c + 1]) / 3;
        if (!(mx > q[0] - 2 && mx < q[2] + 2 && my > q[1] - 2 && my < q[3] + 2)) continue;
        faces.push([a, b, c].map((i) => {
          if (!vid.has(i)) { vid.set(i, V.length); V.push([GV[3 * i] - ox, GV[3 * i + 1] - oy, GV[3 * i + 2]]); }
          zlo = Math.min(zlo, GV[3 * i + 2]);
          zhi = Math.max(zhi, GV[3 * i + 2]);
          return vid.get(i);
        }));
      }
      tin = { key: q.join(","), faces, V, zlo, zhi };
    }
    const { faces, V, zlo, zhi } = tin;
    const zb = Math.floor(Math.min(zlo, lowest)) - 3.0, top = Math.ceil(Math.max(zhi, highest)) + 5.0;
    const key = `${q.join(",")}|${zb}|${top}|${segments}`;
    if (helds.has(key)) {
      held = helds.get(key);
      helds.delete(key);
      helds.set(key, held);
      return held;
    }
    const made = [];
    held = keepFor(made, () => {
      const groundM = closeSurface(V, faces, zb);
      const winCS = boxCS(q);
      const olds = old.filter((w) => !K(sectionOf(w.plan).intersect(winCS)).isEmpty());
      let before = groundM, holes = null, oldToes = [];
      if (olds.length) {
        const tallQ = (cs) => prism(cs, zb - 2.0, top + 2.0);
        const plans = olds.map((w) => sectionOf(w.plan));
        const under = olds.map((w) => toeColumns(w, zb, top + 1.0, segments)[0]);
        const toes = [];
        for (const [cell, members] of overlay(plans)) {
          let low = under[members[0]];
          for (const k of members.slice(1)) low = K(low.intersect(under[k]));
          toes.push(K(low.intersect(tallQ(K(cell.offset(EDGE_M, "Miter"))))));
        }
        holes = tallQ(unionCS(plans));
        before = union([K(groundM.subtract(holes)), ...toes]);
        oldToes = toes;
      }
      before.isEmpty();                       // worked out now, while it's being kept
      return { key, made, zb, top, groundM, before, olds, winCS, kept: new Map(), segments, holes, oldToes, near: new Map() };
    });
    helds.set(key, held);
    while (helds.size > 2) {
      const [oldest, gone] = helds.entries().next().value;
      for (const x of gone.made) x.delete();
      helds.delete(oldest);
    }
    return held;
  }

  // The soil before near a box (in the solids' own metres): the ground's triangles whose middles are within 2 m of it,
  // on a 2 m grid, closed into a solid with the kept ground's bottom, and the existing walls' toes in it. Inside the
  // box it's the soil before; kept with the ground.
  const NEAR_STEP_M = 2.0;
  const byCell = new Map();
  for (let f = 0; f < GF.length / 3; f++) {
    const a = GF[3 * f], b = GF[3 * f + 1], c = GF[3 * f + 2];
    const mx = (GV[3 * a] + GV[3 * b] + GV[3 * c]) / 3 - ox, my = (GV[3 * a + 1] + GV[3 * b + 1] + GV[3 * c + 1]) / 3 - oy;
    const key = `${Math.floor(mx / NEAR_STEP_M)},${Math.floor(my / NEAR_STEP_M)}`;
    if (!byCell.has(key)) byCell.set(key, []);
    byCell.get(key).push(f);
  }
  function beforeNear(b) {
    const i0 = Math.floor(b.min[0] / NEAR_STEP_M) - 2, j0 = Math.floor(b.min[1] / NEAR_STEP_M) - 2;
    const i1 = Math.floor(b.max[0] / NEAR_STEP_M) + 2, j1 = Math.floor(b.max[1] / NEAR_STEP_M) + 2;
    const key = `${i0},${j0},${i1},${j1}`;
    if (held.near.has(key)) return held.near.get(key);
    const solid = keepFor(held.made, () => {
      const faces = [], V = [], vid = new Map();
      for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) {
        for (const f of byCell.get(`${i},${j}`) || []) {
          faces.push([GF[3 * f], GF[3 * f + 1], GF[3 * f + 2]].map((v) => {
            if (!vid.has(v)) { vid.set(v, V.length); V.push([GV[3 * v] - ox, GV[3 * v + 1] - oy, GV[3 * v + 2]]); }
            return vid.get(v);
          }));
        }
      }
      const g = closeSurface(V, faces, held.zb);
      const near = held.holes ? union([K(g.subtract(held.holes)), ...held.oldToes]) : g;
      near.isEmpty();
      return near;
    });
    held.near.set(key, solid);
    return solid;
  }

  // An existing wall's toe columns for the soil after (from a metre above the bottom), kept with the ground.
  function keptColumns(w) {
    if (!held.kept.has(w.id)) held.kept.set(w.id, keepFor(held.made, () => toeColumns(w, held.zb + 1.0, held.top + 1.0, held.segments)));
    return held.kept.get(w.id);
  }

  // solids.option_solids, for a sketch: no steps; every existing wall kept.
  function solids(design, walls, within, segments, conflictStep) {
    let [g0, g1, g2, g3] = garden.bounds;
    if (within) [g0, g1, g2, g3] = [Math.max(g0, within[0]), Math.max(g1, within[1]), Math.min(g2, within[2]), Math.min(g3, within[3])];
    const win = [g0 - MARGIN_M, g1 - MARGIN_M, g2 + MARGIN_M, g3 + MARGIN_M];
    if (vmin[0] > win[0] || vmin[1] > win[1] || vmax[0] < win[2] || vmax[1] < win[3]) {
      throw new Error("the ground drawn doesn't reach past the regrade area");
    }
    // solids.ground_triangles, for a window on a 4 m grid round this one, within the ground drawn: a larger ground
    // than the Python takes, and not clipped to the window, gives the same solids inside it, and holds while the
    // sketch moves. So does a lower bottom, or a higher top, than the Python's.
    const step = WINDOW_STEP_M;
    const q = [Math.max(Math.floor(win[0] / step) * step - step, vmin[0] + 2.5), Math.max(Math.floor(win[1] / step) * step - step, vmin[1] + 2.5),
      Math.min(Math.ceil(win[2] / step) * step + step, vmax[0] - 2.5), Math.min(Math.ceil(win[3] / step) * step + step, vmax[1] - 2.5)];
    const pieces = design.pieces;
    const deep = design.terraces.map((t, k) => (pieces[k].length ? rules.reach_m / t.batter : 0.0));
    const lowest = Math.min(...design.terraces.map((t, k) => t.lowest - deep[k]));
    const highest = Math.max(...design.terraces.map((t, k) => t.highest + deep[k]));
    const ground = groundOver(q, lowest, highest, segments);
    const { zb, top, groundM, before, olds, winCS } = ground;
    mark("ground and the soil before", before);
    let area = sectionOf(garden.rings);
    if (within) area = K(area.intersect(boxCS(within)));
    const tall = (cs) => prism(cs, zb - 2.0, top + 2.0);
    const wide = (ring) => K(sectionOf([ring]).offset(EDGE_M, "Miter"));
    const cut = [], fill = [], parts = [];
    const soil = (region, after, above, part) => {
      if (region.isEmpty()) return;
      const here = tall(region), near = beforeNear(region.bounds());
      fill.push(K(K(after.intersect(here)).subtract(near)));
      cut.push(K(K(near.intersect(here)).intersect(above)));
      parts.push(part);
    };

    // The walls, new and kept: the soil is at the wall's toe, the lowest where two meet.
    const bodies = [];
    const grownRings = (ring) => {         // a plan a micrometre past its outline, as shapely's buffer makes it
      const grown = K(sectionOf([ring]).offset(EDGE_M, "Round"));
      return [grown, grown.toPolygons().map((r) => r.map((p) => [p[0] + ox, p[1] + oy]))];
    };
    for (const w of walls) {     // scheme.wall_body: its trench, from its face (its line) back, at the trench's bottom
      const [grown, rings] = grownRings([...w.line, ...offsetBehind(w.line, w.trench, w.side).reverse()]);
      const plan = K(grown.intersect(area));
      if (plan.isEmpty()) continue;
      const body = { plan: rings, line: w.line, chain: w.chain, toe: trenchBottom(w) };
      bodies.push([plan, ...toeColumns(body, zb + 1.0, top + 1.0, segments), true]);
    }
    for (const w of olds) {          // a kept wall's columns are worked out only if a new wall's cell needs them
      const plan = K(sectionOf(w.plan).intersect(area));
      if (!plan.isEmpty()) bodies.push([plan, null, null, false, w]);
    }
    mark("walls' toe columns", ...bodies.filter((b) => b[3]).flatMap((b) => [b[1], b[2]]));
    const columns = (k) => (bodies[k][3] ? [bodies[k][1], bodies[k][2]] : keptColumns(bodies[k][4]));
    const walled = unionCS(bodies.map((b) => b[0]));
    for (const [cell, members] of overlay(bodies.map((b) => b[0]))) {
      if (!members.some((k) => bodies[k][3])) continue;      // only kept walls: the soil stays at their toes
      let [after, above] = columns(members[0]);
      for (const k of members.slice(1)) {
        const [a, b] = columns(k);
        after = K(after.intersect(a));
        above = K(above.add(b));
      }
      soil(cell, after, above, "walls");
    }

    mark("walls' soil", ...cut, ...fill);
    // Terraces: the first a point is in gives its level, and the soil is any paving's depth under it.
    let taken = K(CrossSection.union([]));
    const levels = [], soils = [];             // (a part of the plan, everything below its soil after)
    design.terraces.forEach((t) => {
      const mine = K(sectionOf([t.ring]).intersect(area));
      const own = K(mine.subtract(taken));
      taken = K(taken.add(mine));
      // one level or a plane over the whole terrace; with its own level at each corner, a plane over each of its
      // triangles (solids._terrace_columns takes their surface whole), each part of the terrace worked out on its own
      const planes = !t.tris ? [[t.ring, t.level, t.plane ? [t.plane[1], t.plane[2]] : [0.0, 0.0], own]]
        : t.tris.map(([a, b, c]) => {
          const tri = [t.ring[a], t.ring[b], t.ring[c]];
          const [z0, ge, gn] = planeOf(tri, [t.z[a], t.z[b], t.z[c]]) || [t.z[a], 0.0, 0.0];
          return [tri, z0, [ge, gn], K(own.intersect(sectionOf([tri])))];
        });
      for (const [ring, z0, gradient, part] of planes) {
        if (part.isEmpty()) continue;
        const col = prism(wide(ring), zb + 1.0, top + 1.0), at = [ring[0][0] - ox, ring[0][1] - oy];
        const [level] = splitByPlane(col, [at[0], at[1], z0], gradient);
        levels.push([part, level]);
        const [soilTop, over] = splitByPlane(col, [at[0], at[1], z0 - t.paving], gradient);
        soils.push([part, soilTop]);
        soil(K(part.subtract(walled)), soilTop, over, "terrace");
      }
    });

    // The rest of the regrade area: the batters.
    mark("terraces", ...cut, ...fill);
    const rest = K(area.subtract(taken));
    const fills = [], cuts = [];
    design.terraces.forEach((t, k) => {
      if (!pieces[k].length) return;
      const [lo, hi] = groundRange(pieces[k], rules.reach_m);
      const [r, c] = batter(t, pieces[k], zb + 0.25, top + 2.5, segments, lo < t.highest, hi > t.lowest);
      fills.push(r);
      cuts.push(c);
    });
    const designed = [];
    if (fills.length) {
      const raiseTo = union(fills), cutTo = union(cuts);
      mark("batters' hulls", raiseTo, cutTo);
      const boxAll = prism(winCS, zb + 0.5, top + 1.5);
      const underCut = K(boxAll.subtract(cutTo));
      let up = K(outline(K(raiseTo.subtract(groundM))).intersect(rest));
      const lowered = K(outline(K(groundM.intersect(cutTo))).intersect(rest));
      const both = K(up.intersect(lowered));
      mark("batters' outlines", up, lowered);
      if (!both.isEmpty()) up = K(up.subtract(cutWins(both, design, conflictStep)));
      mark("where batters disagree", up);
      const down = K(lowered.subtract(up));
      soil(K(up.subtract(walled)), raiseTo, K(boxAll.subtract(raiseTo)), "batters raise");
      soil(K(down.subtract(walled)), underCut, cutTo, "batters cut");
      designed.push(K(raiseTo.intersect(tall(up))), K(underCut.intersect(tall(down))));
      soils.push([up, raiseTo], [down, underCut], [K(K(rest.subtract(up)).subtract(down)), null]);
    } else {
      soils.push([rest, null]);                 // null: the soil before
    }

    // The soil over a new wall's drainage aggregate: from its `seal` under the soil's level after, down to no lower
    // than its trench's bottom, behind its body where no wall stands. Where two walls' meet, the deeper seal's.
    const sealed = walls.map((w, i) => [i, drainageOf(w)]).filter(([, d]) => d).sort((a, b) => b[1].seal - a[1].seal);
    if (sealed.length) {
      const blocked = unionCS([...walls.map((w) => grownRings([...w.line, ...offsetBehind(w.line, w.depth, w.side).reverse()])[0]),
        ...bodies.filter((b) => !b[3]).map((b) => b[0])]);
      let done = K(CrossSection.union([]));
      for (const [, d] of sealed) {
        const [grown, rings] = grownRings(d.ring);
        const region = K(K(K(grown.intersect(area)).subtract(blocked)).subtract(done));
        done = K(done.add(region));
        if (region.isEmpty()) continue;
        const above = toeColumns({ plan: rings, line: d.line, chain: d.chain, toe: d.toe }, zb + 1.0, top + 1.0, segments)[1];
        for (const [own, below] of soils) {
          const part = K(own.intersect(region));
          if (part.isEmpty()) continue;
          const under = below || beforeNear(part.bounds());
          fill.push(K(K(K(under.intersect(tall(part))).intersect(above)).subtract(K(under.translate([0.0, 0.0, -d.seal])))));
        }
      }
    }
    for (const [own, level] of levels) if (!own.isEmpty()) designed.push(K(level.intersect(tall(own))));
    const vol = (ms) => ms.reduce((s, m) => s + m.volume(), 0);
    const volumes = [vol(cut), vol(fill)];
    mark("the rest, and volumes");
    return { cut_m3: volumes[0], fill_m3: volumes[1], parts: { cut, fill, designed, zb }, zb };
  }

  // What the viewer draws, in viewer metres: the designed ground's top where it's moved, the soil cut and filled, the
  // walls. Each is positions and indices, the positions in single precision for a WebGL buffer.
  function drawn(out, walls) {
    const asViewer = (m, keep) => {
      const mesh = m.getMesh(), v = mesh.vertProperties, np = mesh.numProp, tv = mesh.triVerts;
      const pos = new Float32Array((v.length / np) * 3);
      for (let i = 0; i < v.length / np; i++) {
        pos[3 * i] = v[np * i] + ox - E0;
        pos[3 * i + 1] = v[np * i + 2] - H0;
        pos[3 * i + 2] = -(v[np * i + 1] + oy - N0);
      }
      const idx = [];
      for (let t = 0; t < tv.length / 3; t++) {
        const a = tv[3 * t], b = tv[3 * t + 1], c = tv[3 * t + 2];
        if (!keep || keep(v, np, a, b, c)) idx.push(a, c, b);       // the viewer's z is south: the winding turns
      }
      return { positions: pos, indices: Uint32Array.from(idx) };
    };
    const tops = (v, np, a, b, c) => {         // the faces looking up, off the bottom of the column
      const nz = (v[np * b] - v[np * a]) * (v[np * c + 1] - v[np * a + 1]) - (v[np * b + 1] - v[np * a + 1]) * (v[np * c] - v[np * a]);
      return nz > 1e-12 && Math.min(v[np * a + 2], v[np * b + 2], v[np * c + 2]) > out.parts.zb + 1.5;
    };
    const wallsOut = walls.map((w) => {
      const back = offsetBehind(w.samples, w.depth, w.side);
      const sunk = w.embedment.some((v) => v > 0);
      const pos = [], idx = [];
      w.samples.forEach((p, i) => {
        const foot = sunk ? w.toe[i] - w.embedment[i] : w.toe[i];
        for (const q of [p, back[i]]) for (const z of [foot, Math.max(w.top[i], w.toe[i] + 1e-3)]) pos.push(q[0] - E0, z - H0, -(q[1] - N0));
        if (i) {
          const k = 4 * i, j = k - 4;
          idx.push(j + 1, k + 1, k + 3, j + 1, k + 3, j + 3, j, k + 2, k, j, j + 2, k + 2, j, k, k + 1, j, k + 1, j + 1, j + 2, j + 3, k + 3, j + 2, k + 3, k + 2);
        }
      });
      return { positions: Float32Array.from(pos), indices: Uint32Array.from(idx) };
    });
    return {
      designed: out.parts.designed.map((m) => asViewer(m, tops)),
      cut: out.parts.cut.filter((m) => !m.isEmpty()).map((m) => asViewer(m)),
      fill: out.parts.fill.filter((m) => !m.isEmpty()).map((m) => asViewer(m)),
      walls: wallsOut,
    };
  }

  // Delete what's kept from one sketch to the next: the ground, the soil before, the walls' nearest parts.
  function dispose() {
    for (const x of [...[...helds.values()].flatMap((h) => h.made), ...cellsMade]) x.delete();
    helds.clear();
    held = { key: null, made: [] };
    cellsMade.length = 0;
    cellsOf.clear();
  }

  return { update, dispose };
}
