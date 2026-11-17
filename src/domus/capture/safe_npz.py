"""Read a cache .npz without running anything in it.

The caches hold arrays of arrays and a dict of matches, which numpy stores as pickles, and a pickle can
run any code when it's loaded. A site folder can come from someone else, so only what the caches really
hold is let through.
"""

import io
import pickle
import zipfile

import numpy as np

ALLOWED = {
    ("numpy", "ndarray"), ("numpy", "dtype"),
    ("numpy.core.multiarray", "_reconstruct"), ("numpy._core.multiarray", "_reconstruct"),
    ("numpy.core.multiarray", "scalar"), ("numpy._core.multiarray", "scalar"),
}


class _Arrays(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) not in ALLOWED:
            raise pickle.UnpicklingError(f"{module}.{name} isn't allowed in a cache")
        return super().find_class(module, name)


def _read(fh):
    version = np.lib.format.read_magic(fh)
    read_header = {(1, 0): np.lib.format.read_array_header_1_0, (2, 0): np.lib.format.read_array_header_2_0}[version]
    dtype = read_header(fh)[2]
    if dtype.hasobject:
        return _Arrays(fh).load()
    fh.seek(0)
    return np.lib.format.read_array(fh, allow_pickle=False)


def load(path):
    """{name: array} of a .npz, as np.load(path, allow_pickle=True) gives, but safely."""
    with zipfile.ZipFile(path) as z:
        return {n.removesuffix(".npy"): _read(io.BytesIO(z.read(n))) for n in z.namelist()}
