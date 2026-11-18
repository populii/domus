"""glTF 2.0 binary (.glb): an export's parts (domus.export.scene) as one scene, for Blender and most 3D viewers.

The file is a 12-byte header, a JSON chunk saying what's in the scene, and a binary chunk holding the vertex data and
the pictures, written with the standard library and numpy. Each part is a node, under a node for its group in the
viewer's layers panel and one for the site; each look is a material, in linear colours with sRGB pictures, as glTF
has them. Positions are in metres, y up, about the model's origin (x east, z south), and the asset's extras say the
site's grid and origin, so the model can be placed again: E = E0 + x, N = N0 - z, height = H0 + y.
"""

import io
import json
import math
import struct

import numpy as np

from ..errors import DomusError
from .scene import hex_rgb, srgb_to_linear

FLOAT, UINT32, UINT16, UBYTE = 5126, 5125, 5123, 5121
COMPONENTS = {np.dtype("<f4"): FLOAT, np.dtype("<u4"): UINT32, np.dtype("<u2"): UINT16, np.dtype("u1"): UBYTE}
TYPES = {1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4"}
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
LINES, TRIANGLES = 1, 4
REPEAT, CLAMP_TO_EDGE = 10497, 33071
LINEAR, LINEAR_MIPMAP_LINEAR = 9729, 9987
UNLIT = "KHR_materials_unlit"


def clean(value):
    """`value` as JSON can hold it: numbers that aren't finite become null, and anything else that JSON has no
    word for becomes its text."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (bool, int, str)):
        return value
    return str(value)


def picture(folder, name, linear=False):
    """The bytes and media type of the picture `name` in the model's folder, as glTF takes it: a JPEG or PNG as it
    is, anything else as a PNG. A `linear` picture (the leaf cards' grey, which the viewer multiplies colours by as it
    is) is written as the sRGB values that read back as that grey, as glTF reads every colour picture as sRGB."""
    path = (folder / name).resolve()
    if path.parent != folder.resolve() or not path.is_file():
        raise DomusError(f"The model names a picture, {name}, that isn't in {folder}: build the site again.")
    data = path.read_bytes()
    if not linear and data[:3] == b"\xff\xd8\xff":
        return data, "image/jpeg"
    if not linear and data[:8] == b"\x89PNG\r\n\x1a\n":
        return data, "image/png"
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        img = img.convert("RGBA")
        if linear:
            a = np.asarray(img, float) / 255
            c = np.clip(a[..., :3], 0, 1)
            a[..., :3] = np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)
            img = Image.fromarray(np.round(a * 255).astype(np.uint8))
        out = io.BytesIO()
        img.save(out, "PNG", optimize=True)
    return out.getvalue(), "image/png"


class Glb:
    """A glTF binary file being put together: its JSON and its binary buffer, with what's shared (materials,
    pictures, samplers) kept once."""

    def __init__(self, folder, asset):
        self.folder = folder
        self.doc = {"asset": asset, "scene": 0, "scenes": [], "nodes": [], "meshes": [], "materials": [],
                    "textures": [], "images": [], "samplers": [], "accessors": [], "bufferViews": [], "buffers": []}
        self.bin = bytearray()
        self.shared = {}

    def add(self, kind, item, key=None):
        """Adds `item` to the document's list `kind`, once for each `key`, and gives its index."""
        if key is not None and (kind, key) in self.shared:
            return self.shared[(kind, key)]
        self.doc[kind].append(item)
        index = len(self.doc[kind]) - 1
        if key is not None:
            self.shared[(kind, key)] = index
        return index

    def view(self, data, target=None):
        """A buffer view of `data`, which starts on a four-byte boundary in the buffer."""
        self.bin.extend(b"\0" * (-len(self.bin) % 4))
        view = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            view["target"] = target
        self.bin.extend(data)
        return self.add("bufferViews", view)

    def accessor(self, array, dtype, target):
        """An accessor of `array` (a row for each element) as `dtype`, in a view of its own, with its bounds."""
        a = np.ascontiguousarray(array, dtype=dtype)
        flat = a.reshape(len(a), -1)
        to = float if a.dtype.kind == "f" else int
        return self.add("accessors", {
            "bufferView": self.view(a.tobytes(), target), "componentType": COMPONENTS[a.dtype], "count": len(a),
            "type": TYPES[flat.shape[1]], "min": [to(x) for x in flat.min(axis=0)], "max": [to(x) for x in flat.max(axis=0)]})

    def texture(self, name, repeat, linear):
        """The texture of the picture `name`, repeating or not."""
        key = (name, linear)
        if ("images", key) not in self.shared:
            data, mime = picture(self.folder, name, linear)
            self.add("images", {"bufferView": self.view(data), "mimeType": mime, "name": name.rsplit(".", 1)[0]}, key)
        wrap = REPEAT if repeat else CLAMP_TO_EDGE
        sampler = self.add("samplers", {"magFilter": LINEAR, "minFilter": LINEAR_MIPMAP_LINEAR, "wrapS": wrap, "wrapT": wrap},
                           wrap)
        return self.add("textures", {"sampler": sampler, "source": self.shared[("images", key)]}, (key, wrap))

    def material(self, look, lines=False):
        """The material for `look`: one for each colour or picture and way of drawing it."""
        key = (look.colour, look.texture, look.repeat, look.roughness, look.double_sided, look.alpha, look.opacity,
               look.linear, lines)
        if ("materials", key) in self.shared:
            return self.shared[("materials", key)]
        pbr = {"baseColorFactor": [*(float(c) for c in srgb_to_linear(hex_rgb(look.colour))), float(look.opacity)],
               "metallicFactor": 0.0, "roughnessFactor": float(look.roughness)}
        if look.texture:
            pbr["baseColorTexture"] = {"index": self.texture(look.texture, look.repeat, look.linear)}
        name = look.texture.rsplit(".", 1)[0] if look.texture else look.colour
        mat = {"name": f"{name} lines" if lines else name, "pbrMetallicRoughness": pbr}
        if lines:
            mat["extensions"] = {UNLIT: {}}
            self.doc.setdefault("extensionsUsed", [])
            if UNLIT not in self.doc["extensionsUsed"]:
                self.doc["extensionsUsed"].append(UNLIT)
        if look.double_sided:
            mat["doubleSided"] = True
        if look.alpha != "OPAQUE":
            mat["alphaMode"] = look.alpha
            if look.alpha == "MASK":
                mat["alphaCutoff"] = 0.5
        return self.add("materials", mat, key)

    def primitive(self, piece):
        """A mesh primitive of `piece`: its triangles, or its lines."""
        attrs = {"POSITION": self.accessor(piece.positions, "<f4", ARRAY_BUFFER)}
        if piece.normals is not None:
            attrs["NORMAL"] = self.accessor(piece.normals, "<f4", ARRAY_BUFFER)
        if piece.uv is not None:
            uv = np.column_stack([piece.uv[:, 0], 1 - piece.uv[:, 1]])     # glTF's v runs down the picture
            attrs["TEXCOORD_0"] = self.accessor(uv, "<f4", ARRAY_BUFFER)
        if piece.colours is not None:
            attrs["COLOR_0"] = self.accessor(np.clip(piece.colours, 0, 1), "<f4", ARRAY_BUFFER)
        if piece.faces is None:
            return {"attributes": attrs, "mode": LINES, "material": self.material(piece.look, lines=True)}
        # 65535 is the one index a 16-bit list can't hold (it would restart the primitive).
        dtype = "<u2" if len(piece.positions) <= 65535 else "<u4"
        return {"attributes": attrs, "indices": self.accessor(piece.faces.reshape(-1), dtype, ELEMENT_ARRAY_BUFFER),
                "mode": TRIANGLES, "material": self.material(piece.look)}

    def node(self, name, pieces=(), children=(), extras=None):
        """A node named `name`, with a mesh of `pieces` if it has any, and its children's nodes; gives its index."""
        node = {"name": name}
        prims = [self.primitive(p) for p in pieces if len(p.positions) and (p.faces is None or len(p.faces))]
        if prims:
            node["mesh"] = self.add("meshes", {"name": name, "primitives": prims})
        if children:
            node["children"] = list(children)
        if extras:
            node["extras"] = clean(extras)
        return self.add("nodes", node)

    def bytes(self):
        """The whole .glb: header, JSON chunk (padded with spaces) and binary chunk (padded with zeros), which a file
        with no geometry and no pictures goes without."""
        self.doc["buffers"] = [{"byteLength": len(self.bin)}] if self.bin else []
        doc = {k: v for k, v in self.doc.items() if v != [] or k in ("scenes", "nodes")}
        text = json.dumps(doc, separators=(",", ":"), allow_nan=False).encode("utf-8")
        text += b" " * (-len(text) % 4)
        chunks = struct.pack("<I4s", len(text), b"JSON") + text
        if self.bin:
            data = bytes(self.bin) + b"\0" * (-len(self.bin) % 4)
            chunks += struct.pack("<I4s", len(data), b"BIN\0") + data
        return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def part_extras(part):
    """What a node carries for other software to read: its layer's id, its plant if it is one, and what the viewer
    shows when it's picked."""
    return {"layer": part.id, **({"plant": part.plant} if part.plant else {}), **part.info}


def write(parts, model, folder, path, georef):
    """Writes `parts` (domus.export.scene.parts) as a .glb at `path`, its pictures read from the model's `folder`.
    `georef` (the site's grid, heights and origin) goes in the asset's extras and on the site's node."""
    title = model.get("title") or "Site model"
    asset = {"version": "2.0", "generator": f"Domus {georef['domus']}", "extras": clean(georef)}
    if model.get("attribution"):
        asset["copyright"] = model["attribution"]
    glb = Glb(folder, asset)
    groups = {}
    for part in parts:
        kids = [glb.node(c.name, c.pieces, extras=part_extras(c)) for c in part.children]
        groups.setdefault(part.group, []).append(glb.node(part.name, part.pieces, kids, part_extras(part)))
    top = [glb.node(name, children=nodes) for name, nodes in groups.items()]
    shown = georef.get("option")
    root = glb.node(f"{title}: {shown['short']}" if shown else title, children=top, extras=georef)
    glb.doc["scenes"] = [{"name": title, "nodes": [root]}]
    path.write_bytes(glb.bytes())
