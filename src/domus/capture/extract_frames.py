"""Extract review and reconstruction frames from a site video.

    domus video frames sources/raw/walk2/VIDEO.mp4 [--fps 6]

Every decoded frame is scored for sharpness (variance of the Laplacian of a small grey
copy); from each 1/fps-second window the sharpest frame is kept, so motion blur costs as
little as possible. Frames go to sources/raw/frames6/<clip>/t<seconds>.jpg, named by
their time in the clip, and the score of every frame to frames.csv beside them.
"""

import argparse
import csv
from pathlib import Path

import av
import cv2
import numpy as np


def main(site):
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--fps", type=float, default=6.0)
    args = ap.parse_args()
    video = Path(args.video)
    out = site.path / "sources" / "raw" / "frames6" / video.stem
    out.mkdir(parents=True, exist_ok=True)

    rows, best = [], {}
    with av.open(str(video)) as c:
        stream = c.streams.video[0]
        stream.thread_type = "AUTO"
        t0 = None
        for n, frame in enumerate(c.decode(stream)):
            t0 = frame.time if t0 is None else t0
            t = frame.time - t0
            img = frame.to_ndarray(format="bgr24")
            grey = cv2.cvtColor(cv2.resize(img, (480, 360), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            sharp = float(cv2.Laplacian(grey, cv2.CV_32F).var())
            window = int(np.floor(t * args.fps + 1e-6))
            rows.append((n, round(t, 3), window, round(sharp, 1)))
            if window not in best or sharp > best[window][0]:
                best[window] = (sharp, t, img)
    kept = set()
    for _, t, img in best.values():
        name = f"t{t:07.3f}.jpg"
        cv2.imwrite(str(out / name), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        kept.add(round(t, 3))
    with open(out / "frames.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["frame", "t_s", "window", "sharpness", "kept"])
        w.writerows((*r, int(r[1] in kept)) for r in rows)
    print(f"{video.name}: {len(rows)} frames decoded, {len(best)} kept in {out.relative_to(site.path)}")


if __name__ == "__main__":
    from ..project.site import started

    main(started())       # run by itself, on the site the environment names
