// The sums behind exact drawing, in plan metres (x east, z south), and the wall options' frame, on the site's grid, so
// they can run without a browser.

const DEG = Math.PI / 180;
const dist = (a, b) => Math.hypot(a.x - b.x, a.z - b.z);

// lines: [{ label, pts: [[x, z], ...] }]. points: [{ label, x, z }].
export function snapTargets(lines = [], points = []) {
  const corners = points.map((p) => ({ x: p.x, z: p.z, label: p.label }));
  const mids = [], edges = [];
  for (const line of lines) {
    line.pts.forEach(([x, z], i) => {
      corners.push({ x, z, label: `${line.label} corner` });
      if (i) {
        const [px, pz] = line.pts[i - 1];
        mids.push({ x: (x + px) / 2, z: (z + pz) / 2, label: `${line.label} midpoint` });
        edges.push({ a: { x: px, z: pz }, b: { x, z }, label: line.label });
      }
    });
  }
  return { corners, mids, edges };
}

function nearestOnEdge(q, { a, b }) {
  const dx = b.x - a.x, dz = b.z - a.z, L2 = dx * dx + dz * dz;
  const t = L2 ? Math.min(1, Math.max(0, ((q.x - a.x) * dx + (q.z - a.z) * dz) / L2)) : 0;
  return { x: a.x + t * dx, z: a.z + t * dz };
}

// The best thing to snap to within tol metres: a corner, then a midpoint, then along an edge. Or null.
export function nearestSnap(q, { corners, mids, edges }, tol) {
  const best = (list) => list.reduce((m, p) => {
    const d = dist(q, p);
    return d <= tol && (!m || d < m.d) ? { ...p, d } : m;
  }, null);
  const pick = best(corners) || best(mids);
  if (pick) return pick;
  return best(edges.map((e) => ({ ...nearestOnEdge(q, e), label: e.label })));
}

// The directions worth lining up with (radians, 0 to pi), from the site's lines and any extra segments.
export function referenceAngles(lines = []) {
  const out = [];
  for (const { pts } of lines) {
    for (let i = 1; i < pts.length; i++) {
      const dx = pts[i][0] - pts[i - 1][0], dz = pts[i][1] - pts[i - 1][1];
      if (Math.hypot(dx, dz) < 0.5) continue;
      out.push(((Math.atan2(dz, dx) % Math.PI) + Math.PI) % Math.PI);
    }
  }
  return out;
}

// q moved onto the line through `from` at `angle`, on q's side.
export function projectOnto(from, angle, q) {
  const ux = Math.cos(angle), uz = Math.sin(angle);
  const t = (q.x - from.x) * ux + (q.z - from.z) * uz;
  return { x: from.x + t * ux, z: from.z + t * uz };
}

// If the line from `from` to q is within tolDeg of a reference direction or square to one, that direction. Or null.
export function inferAngle(from, q, angles, tolDeg = 2.5) {
  if (dist(from, q) < 0.2) return null;
  const a = Math.atan2(q.z - from.z, q.x - from.x);
  let best = null;
  for (const ref of angles) {
    for (const [r, kind] of [[ref, "in line"], [ref + Math.PI / 2, "square"]]) {
      const off = Math.abs(Math.atan2(Math.sin(2 * (a - r)), Math.cos(2 * (a - r)))) / 2;
      if (off <= tolDeg * DEG && (!best || off < best.off)) best = { angle: r, kind, off };
    }
  }
  return best;
}

// A typed entry: a length in metres ("3.2", "3.2 m", "3200mm"), and optionally a compass bearing ("3.2@45"). Or null.
export function parseEntry(text) {
  const m = /^\s*(\d*\.?\d+)\s*(mm|m)?\s*(?:@\s*(\d*\.?\d+)\s*(?:°|deg)?)?\s*$/i.exec(text);
  if (!m) return null;
  const len = parseFloat(m[1]) * (m[2]?.toLowerCase() === "mm" ? 0.001 : 1);
  if (!(len > 0)) return null;
  const bearing = m[3] === undefined ? null : parseFloat(m[3]);
  return bearing !== null && bearing > 360 ? null : { len, bearing };
}

// The point an entry names from `from`: along its bearing, or else towards `toward`. Null if there's no direction.
export function entryPoint(from, entry, toward) {
  if (entry.bearing !== null) {
    const b = entry.bearing * DEG;
    return { x: from.x + entry.len * Math.sin(b), z: from.z - entry.len * Math.cos(b) };
  }
  if (!toward) return null;
  const d = dist(from, toward);
  if (d < 1e-6) return null;
  return { x: from.x + ((toward.x - from.x) / d) * entry.len, z: from.z + ((toward.z - from.z) / d) * entry.len };
}

// Undo and redo over snapshots: record each new state, and step back and forward through them.
export function createHistory(first, limit = 100) {
  const back = [first];
  let ahead = [];
  return {
    record(state) {
      if (state === back[back.length - 1]) return false;
      back.push(state);
      if (back.length > limit) back.shift();
      ahead = [];
      return true;
    },
    undo() {
      if (back.length < 2) return null;
      ahead.push(back.pop());
      return back[back.length - 1];
    },
    redo() {
      if (!ahead.length) return null;
      const s = ahead.pop();
      back.push(s);
      return s;
    },
  };
}

// The wall options' frame, from the model's summary.options_site.frame ({ origin, toward, side }), as
// domus.options.scheme.SiteFrame has it: s is metres along the line from origin towards toward, and o metres in from
// it on the lot's side, its right looking along the line unless side is "left". so(E, N) and en(s, o) turn points on
// the site's grid into the frame and back. Null without a frame.
export function optionsFrame(frame) {
  if (!frame) return null;
  const [oe, on] = frame.origin, de = frame.toward[0] - oe, dn = frame.toward[1] - on, L = Math.hypot(de, dn);
  const a = [de / L, dn / L], k = frame.side === "left" ? 1 : -1, w = [-a[1] * k, a[0] * k];   // along, and in
  return {
    so: (E, N) => [(E - oe) * a[0] + (N - on) * a[1], (E - oe) * w[0] + (N - on) * w[1]],
    en: (s, o) => [oe + s * a[0] + o * w[0], on + s * a[1] + o * w[1]],
  };
}
