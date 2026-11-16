"""Chunked upload: pieces arrive separately (any order, retried), a retry skips pieces already there, and the
stitched file is byte-identical in the inbox."""
import hashlib
import os

import pytest
from fastapi.testclient import TestClient

from clipper import config
from clipper.main import app

H = {"X-Clipper-Token": "t0k"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TOKEN", "t0k")
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "INBOX", tmp_path / "inbox")
    (tmp_path / "inbox").mkdir()
    return TestClient(app)  # no `with`: the startup hooks (database, queue) are not needed here


def _send(c, uid, i, total, data, name="big.mp4"):
    return c.post("/upload/chunk", params={"upload_id": uid, "index": i, "total": total, "name": name}, content=data, headers=H)


def test_pieces_in_any_order_become_the_exact_file(client, tmp_path):
    blob = os.urandom(5 * 1024 * 1024 + 123)
    cut = 2 * 1024 * 1024
    pieces = [blob[i:i + cut] for i in range(0, len(blob), cut)]
    for i in reversed(range(len(pieces))):  # out of order on purpose
        assert _send(client, "up-123456", i, len(pieces), pieces[i]).status_code == 200
    r = client.post("/upload/finish", params={"upload_id": "up-123456", "total": len(pieces), "name": "big.mp4"}, headers=H)
    assert r.status_code == 200 and r.json() == {"filename": "big.mp4", "bytes": len(blob)}
    got = (tmp_path / "inbox" / "big.mp4").read_bytes()
    assert hashlib.sha256(got).digest() == hashlib.sha256(blob).digest()
    assert not (tmp_path / "data" / "uploads" / "up-123456").exists()  # pieces cleaned up


def test_status_lists_pieces_so_a_retry_can_skip_them(client):
    _send(client, "up-abcdef", 0, 3, b"a")
    _send(client, "up-abcdef", 2, 3, b"c")
    assert client.get("/upload/status", params={"upload_id": "up-abcdef"}, headers=H).json() == {"have": [0, 2]}
    r = client.post("/upload/finish", params={"upload_id": "up-abcdef", "total": 3, "name": "big.mp4"}, headers=H)
    assert r.status_code == 409 and "missing" in r.json()["detail"]
    _send(client, "up-abcdef", 1, 3, b"b")
    assert client.post("/upload/finish", params={"upload_id": "up-abcdef", "total": 3, "name": "big.mp4"}, headers=H).json()["bytes"] == 3


def test_rejects_bad_input(client):
    assert _send(client, "up-123456", 0, 1, b"x", name="evil.exe").status_code == 400
    assert _send(client, "../escape", 0, 1, b"x").status_code == 400
    assert _send(client, "up-123456", 5, 3, b"x").status_code == 400  # piece number outside the total
    assert _send(client, "up-123456", 0, 1, b"").status_code == 400
    assert client.post("/upload/chunk", params={"upload_id": "up-123456", "index": 0, "total": 1, "name": "a.mp4"}, content=b"x").status_code == 401
