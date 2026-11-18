// The sums behind the scan page, with no browser in them so they can be tested by themselves.

const SLOW_ENOUGH = 60;        // degrees a second: a dot only counts when the phone is about this steady
const TOO_FAST = 110;          // degrees a second: blur and lost matches past this
const TOO_SOFT = 40;           // edge strength below this is a blurred frame, or a blank wall
const REACH = 12;              // degrees from the middle of the view that covers a dot
const NEAR_M = 3;              // a route point is covered with the track this close

export function angleDelta(to, from) {
  return ((to - from + 540) % 360) - 180;
}

// Dots right round the phone: a row looking slightly down at the ground and walls, and one higher up.
export function createCoverage(startYaw, columns = 9, pitches = [-20, 8]) {
  const targets = [];
  for (const pitch of pitches) {
    for (let k = 0; k < columns; k++) targets.push({ yaw: (startYaw + (k * 360) / columns) % 360, pitch, done: false });
  }
  return {
    targets,
    look(yaw, pitch, rate) {
      if (rate > SLOW_ENOUGH) return;
      for (const t of targets) {
        if (!t.done && Math.abs(angleDelta(t.yaw, yaw)) < REACH && Math.abs(t.pitch - pitch) < REACH) t.done = true;
      }
    },
    // The closest one left, to steer towards.
    next() {
      return targets.find((t) => !t.done) || null;
    },
  };
}

// The route from the built model, in viewer metres: path as [x, z], and the stops with their words and places.
export function buildRoute(model) {
  const layer = model.layers?.find((l) => l.id === "walk-route");
  const stops = model.enrich?.route?.stops;
  if (!layer || !stops) return null;
  const path = [];
  const s = layer.segments;
  for (let i = 0; i < s.length; i += 6) {
    if (!i) path.push([s[0], s[2]]);
    path.push([s[i + 3], s[i + 5]]);
  }
  const at = {};
  for (const lab of layer.labels || []) at[lab.text] = [lab.at[0], lab.at[2]];
  return { path, stops: stops.map((st) => ({ ...st, at: at[String(st.n)] || path[0] })) };
}

// How sharp a small greyscale sample of the frame is: the mean strength of its edges.
export function sharpness(image) {
  const { data, width, height } = image;
  const grey = (x, y) => {
    const i = (y * width + x) * 4;
    return 0.3 * data[i] + 0.59 * data[i + 1] + 0.11 * data[i + 2];
  };
  let sum = 0, n = 0;
  for (let y = 1; y < height - 1; y += 2) {
    for (let x = 1; x < width - 1; x += 2) {
      sum += Math.abs(4 * grey(x, y) - grey(x - 1, y) - grey(x + 1, y) - grey(x, y - 1) - grey(x, y + 1));
      n++;
    }
  }
  return n ? sum / n : 0;
}

export function speedWarning(rate, sharp) {
  if (rate > TOO_FAST) return "Slow down";
  if (sharp != null && sharp < TOO_SOFT) return "Blurry, or nothing to see: hold steady on something with detail";
  return "";
}

// The share of the route's points with the track nowhere near. The track's entries start [x, z].
export function gapShare(path, track) {
  if (!path.length) return 0;
  const gaps = path.filter((p) => !track.some((t) => Math.hypot(t[0] - p[0], t[1] - p[1]) < NEAR_M)).length;
  return gaps / path.length;
}

function frameOf(route, pos, fwd) {
  const a = route.path[0] || [0, 0];
  const b = route.path.find((p) => Math.hypot(p[0] - a[0], p[1] - a[1]) > 0.5) || [a[0], a[1] - 1];
  // The turn that carries the route's first leg onto the way the phone faces.
  const routeAngle = Math.atan2(b[1] - a[1], b[0] - a[0]);
  const fwdAngle = Math.atan2(fwd[2], fwd[0]);
  return { a, pos, turn: fwdAngle - routeAngle };
}

// The AR session's start is stop 1, facing along the first leg: the stops in the session's own metres.
export function pinToWorld(route, pos, fwd) {
  const f = frameOf(route, pos, fwd);
  const c = Math.cos(f.turn), s = Math.sin(f.turn);
  return route.stops.map((st, index) => {
    const dx = st.at[0] - f.a[0], dz = st.at[1] - f.a[1];
    return { index, world: [pos[0] + c * dx - s * dz, pos[2] + s * dx + c * dz] };
  });
}

// And back: a place in the session's metres, in the route's.
pinToWorld.invert = (route, origin, here) => {
  const f = frameOf(route, origin.pos, origin.fwd);
  const c = Math.cos(-f.turn), s = Math.sin(-f.turn);
  const dx = here[0] - origin.pos[0], dz = here[1] - origin.pos[2];
  return [f.a[0] + c * dx - s * dz, f.a[1] + s * dx + c * dz];
};
