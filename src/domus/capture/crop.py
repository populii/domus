"""A piece of a photo at full size, its corners read off a smaller copy, turned and enlarged to read detail.

    domus video crop PHOTO OUT.jpg REDUCED_LONG_SIDE X0 Y0 X1 Y1 [--zoom 1] [--rotate 0]

X0 Y0 X1 Y1 are the piece's corners in a copy of the photo whose long side is REDUCED_LONG_SIDE pixels (the size you
looked at it), turned the way its EXIF says. The piece is cut from the photo at full size, turned --rotate degrees
anticlockwise and scaled by --zoom.
"""


from PIL import Image, ImageOps


def crop(src, out, side, box, zoom=1.0, rotate=0.0):
    """Writes the piece and returns its size and the box it took from the full-size photo."""
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    k = max(im.size) / side
    full = tuple(int(round(v * k)) for v in box)
    c = im.crop(full)
    if rotate:
        c = c.rotate(rotate, expand=True, resample=Image.BICUBIC)
    if zoom != 1.0:
        c = c.resize((int(c.size[0] * zoom), int(c.size[1] * zoom)), Image.LANCZOS)
    c.save(out, quality=92)
    return c.size, full


def piece(photo, out, side, box, zoom=1.0, rotate=0.0):
    """`domus video crop`: the piece of `photo` whose corners `box` (X0, Y0, X1, Y1) were read on a copy with a
    long side of `side` pixels, cut at full size, turned `rotate` degrees anticlockwise and scaled by `zoom`, as
    `out`. Gives its size and the box it took from the full-size photo."""
    size, full = crop(photo, out, side, box, zoom, rotate)
    print(out, size, "full-res box", full)
    return size, full
