"""Contact sheets of a clip's frames between two times, each frame's name on it, for finding the frames to mark.

    domus video sheet FRAMES_DIR OUT_PREFIX T0 T1 STEP [--cols 6] [--width 480] [--rows 3] [--registered TAG-sfm.npz]

FRAMES_DIR holds a clip's frames as `domus video frames` writes them (t<seconds>.jpg). From T0 to T1 seconds, a frame
at least STEP seconds after the last one taken goes on the sheet; with --registered, only the frames a reconstruction
placed. Each sheet holds --cols by --rows frames, each --width pixels wide, and is written as OUT_PREFIX-1.jpg,
OUT_PREFIX-2.jpg and so on.
"""

from pathlib import Path

import cv2
import numpy as np

from ..errors import DomusError


def pick(names, t0, t1, step, registered=None):
    """The frames (names like t012.345) from `t0` to `t1` seconds, each at least `step` after the last one taken, and
    only those in `registered` if it's given."""
    names = sorted(n for n in names if registered is None or n in registered)
    out, last = [], -1e9
    for n in names:
        t = float(n[1:])
        if t0 <= t <= t1 and t - last >= step - 1e-6:
            out.append(n)
            last = t
    return out


def sheets(src, prefix, chosen, cols=6, width=480, rows=3):
    """Writes the sheets of `chosen` frames from the folder `src`: [(file, width, height, first, last)]."""
    src, per, written = Path(src), cols * rows, []
    for k in range(0, len(chosen), per):
        chunk = chosen[k:k + per]
        th, tiles = None, []
        for n in chunk:
            im = cv2.imread(str(src / f"{n}.jpg"))
            if im is None:
                raise DomusError(f"{src / f'{n}.jpg'} can't be read as a picture")
            h, w = im.shape[:2]
            th = int(round(width * h / w))
            im = cv2.resize(im, (width, th), interpolation=cv2.INTER_AREA)
            cv2.putText(im, n, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(im, n, (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 1, cv2.LINE_AA)
            tiles.append(im)
        while len(tiles) % cols:
            tiles.append(np.zeros((th, width, 3), np.uint8))
        sheet = np.vstack([np.hstack(tiles[r:r + cols]) for r in range(0, len(tiles), cols)])
        out = f"{prefix}-{k // per + 1}.jpg"
        cv2.imwrite(out, sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
        written.append((out, sheet.shape[1], sheet.shape[0], chunk[0], chunk[-1]))
    return written


def make(frames, prefix, t0, t1, step, cols=6, width=480, rows=3, registered=None):
    """`domus video sheet`: contact sheets of the frames in the folder `frames` from `t0` to `t1` seconds, one at
    least every `step` seconds (only those reconstruction `registered`, a <tag>-sfm.npz, placed, if it's given),
    `cols` by `rows` to a sheet, each `width` pixels wide, as <prefix>-1.jpg and on. Gives
    [(file, width, height, first frame, last frame)]."""
    src = Path(frames)
    if not src.is_dir():
        raise DomusError(f"{src} isn't a folder of frames")
    placed = None
    if registered:
        with np.load(registered, allow_pickle=False) as d:
            placed = {str(n) for n, r in zip(d["frame_names"], d["rvecs"], strict=True) if np.isfinite(r).all()}
    chosen = pick([p.stem for p in src.glob("t*.jpg")], t0, t1, step, placed)
    written = sheets(src, prefix, chosen, cols, width, rows)
    for out, w, h, first, last in written:
        print(out, w, "x", h, first, "..", last)
    return written
