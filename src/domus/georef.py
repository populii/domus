"""Georeferencing scanned survey sheets: find grid crosses, fit pixel -> grid transforms."""

import numpy as np


def find_cross(gray, approx, search=45, arm=20, dark=140):
    """Locate the centre of a '+' grid cross near `approx` (col, row) in a greyscale array.

    Scores every candidate centre by the dark pixels along a horizontal and a vertical arm,
    then refines to the score-weighted centroid of the best candidates. Returns
    (col, row, score) where score is the fraction of arm pixels that are dark.
    """
    cx, cy = int(round(approx[0])), int(round(approx[1]))
    r0, r1 = cy - search - arm, cy + search + arm + 1
    c0, c1 = cx - search - arm, cx + search + arm + 1
    win = (gray[r0:r1, c0:c1] < dark).astype(np.int32)
    # Horizontal run sums: dark count in [c-arm, c+arm] on each row; same for columns.
    k = 2 * arm + 1
    hsum = np.apply_along_axis(lambda r: np.convolve(r, np.ones(k, int), "same"), 1, win)
    vsum = np.apply_along_axis(lambda c: np.convolve(c, np.ones(k, int), "same"), 0, win)
    score = (hsum + vsum) / (2.0 * k)
    inner = score[arm:-arm, arm:-arm]
    best = inner.max()
    if best <= 0:
        return float(cx), float(cy), 0.0
    ys, xs = np.nonzero(inner >= best - 0.02)
    w = inner[ys, xs]
    col = c0 + arm + float(np.average(xs, weights=w))
    row = r0 + arm + float(np.average(ys, weights=w))
    return col, row, float(best)


def refine_line(gray, p0, p1, band=10, dark=110, trim=0.1):
    """Fit a straight drawn line near the approximate segment p0 -> p1 (pixel col, row).

    Collects dark pixels within `band` px of the segment (ignoring the end `trim` fraction,
    where other lines meet it) and fits a total-least-squares line. Returns
    (point, unit_direction, rms_px, n_pixels).
    """
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    d = p1 - p0
    length = np.hypot(*d)
    u = d / length
    nrm = np.array([-u[1], u[0]])
    lo = np.floor(np.minimum(p0, p1) - band).astype(int)
    hi = np.ceil(np.maximum(p0, p1) + band).astype(int)
    sub = gray[lo[1]:hi[1], lo[0]:hi[0]]
    rows, cols = np.nonzero(sub < dark)
    pts = np.column_stack([cols + lo[0] + 0.5, rows + lo[1] + 0.5])
    rel = pts - p0
    along = rel @ u
    across = rel @ nrm
    keep = (np.abs(across) <= band) & (along >= trim * length) & (along <= (1 - trim) * length)
    pts = pts[keep]
    centre = pts.mean(0)
    _, _, vt = np.linalg.svd(pts - centre, full_matrices=False)
    direction = vt[0] if vt[0] @ u >= 0 else -vt[0]
    resid = (pts - centre) @ np.array([-direction[1], direction[0]])
    return centre, direction, float(np.sqrt(np.mean(resid ** 2))), int(len(pts))


def intersect(line_a, line_b):
    """Intersection of two (point, direction) lines."""
    (pa, da), (pb, db) = line_a[:2], line_b[:2]
    m = np.column_stack([da, -db])
    t = np.linalg.solve(m, pb - pa)
    return pa + t[0] * da


def fit_affine(pix, world):
    """Least-squares affine world = A @ [col, row, 1]. Returns (A 2x3, residuals Nx2)."""
    pix = np.asarray(pix, float)
    world = np.asarray(world, float)
    design = np.column_stack([pix, np.ones(len(pix))])
    coef, *_ = np.linalg.lstsq(design, world, rcond=None)
    a = coef.T
    resid = world - design @ coef
    return a, resid


def fit_similarity(src, dst):
    """Least-squares 2D similarity (rotation, uniform scale, shift) mapping src -> dst.

    Returns (scale, rotation_rad, tx, ty, residuals).
    """
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    ms, md = src.mean(0), dst.mean(0)
    s, d = src - ms, dst - md
    zs = s[:, 0] + 1j * s[:, 1]
    zd = d[:, 0] + 1j * d[:, 1]
    k = np.vdot(zs, zd) / np.vdot(zs, zs)  # complex scale*rotation
    t = (md[0] + 1j * md[1]) - k * (ms[0] + 1j * ms[1])
    fitted = k * (src[:, 0] + 1j * src[:, 1]) + t
    resid = np.column_stack([dst[:, 0] - fitted.real, dst[:, 1] - fitted.imag])
    return abs(k), float(np.angle(k)), t.real, t.imag, resid


def fit_rigid(src, dst):
    """Least-squares 2D rotation + shift (no scaling) mapping src -> dst.

    Returns (rotation_rad, tx, ty, residuals).
    """
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    ms, md = src.mean(0), dst.mean(0)
    zs = (src - ms) @ np.array([1, 1j])
    zd = (dst - md) @ np.array([1, 1j])
    rot = np.angle(np.vdot(zs, zd))
    k = np.exp(1j * rot)
    t = (md[0] + 1j * md[1]) - k * (ms[0] + 1j * ms[1])
    fitted = k * (src[:, 0] + 1j * src[:, 1]) + t
    resid = np.column_stack([dst[:, 0] - fitted.real, dst[:, 1] - fitted.imag])
    return float(rot), t.real, t.imag, resid


def apply_affine(a, pts):
    pts = np.atleast_2d(np.asarray(pts, float))
    return pts @ a[:, :2].T + a[:, 2]


def invert_affine(a):
    m = np.linalg.inv(a[:, :2])
    return np.column_stack([m, -m @ a[:, 2]])
