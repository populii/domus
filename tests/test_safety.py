"""What a public server has to hold to."""

import os
import pickle
import zipfile

import numpy as np
import pytest

from domus import serve
from domus.capture import safe_npz


@pytest.mark.parametrize("host", ["localhost", "localhost:8765", "app.localhost:1", "127.0.0.1:8765", "10.20.30.40", "[::1]:8765", "[::1]", None, "SITE.example:443"])
def test_hosts_it_answers_to(host, monkeypatch):
    monkeypatch.setenv("DOMUS_ALLOWED_HOSTS", "other.example, site.example")
    assert serve.host_allowed(host)


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "localhost.evil.example", "127.0.0.1.evil.example"])
def test_hosts_it_refuses(host, monkeypatch):
    monkeypatch.delenv("DOMUS_ALLOWED_HOSTS", raising=False)
    assert not serve.host_allowed(host)


def test_a_cache_loads_as_numpy_saved_it(tmp_path):
    path = tmp_path / "c.npz"
    kps = np.empty(2, dtype=object)
    kps[0], kps[1] = np.zeros((3, 2)), np.ones((1, 2))
    matches = {(0, 1): np.array([[1, 2]]), (1, 2): np.array([[3, 4]])}
    np.savez(path, names=np.array(["a", "b"]), kps=kps, matches=np.array(matches, dtype=object))
    c = safe_npz.load(path)
    assert list(c["names"]) == ["a", "b"]
    assert np.array_equal(c["kps"][1], kps[1])
    assert np.array_equal(c["matches"].item()[(1, 2)], matches[(1, 2)])


class Attack:
    def __reduce__(self):
        return os.system, ("exit 0",)


def test_a_cache_cannot_run_code(tmp_path):
    arr = np.empty(1, dtype=object)
    arr[0] = Attack()
    path = tmp_path / "bad.npz"
    np.savez(path, x=arr)
    assert zipfile.is_zipfile(path)
    with pytest.raises(pickle.UnpicklingError):
        safe_npz.load(path)


def test_nothing_from_a_model_goes_into_the_page_unescaped():
    page = (serve.VIEWER / "index.html").read_text(encoding="utf-8")
    for sink in ('class="layer-name">${layer', "</strong> ${w.name", '<p class="opt-desc">${o.', "<br>${model.attribution", '<span class="hint">${info.', "${o.short}", "${t.label}"):
        assert sink not in page, sink
