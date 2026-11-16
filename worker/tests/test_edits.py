"""Quick text edits: partial changes are kept, a text edit re-renders only that clip and skips the AI review."""
import pytest
from fastapi.testclient import TestClient

from clipper import config, edits
from clipper.main import app

H = {"X-Clipper-Token": "t0k"}


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setattr(config, "TOKEN", "t0k")
    return TestClient(app)


def test_the_body_only_accepts_sensible_values(api):
    ok = edits.TextEditIn(hook_text="", header_text="SPEED ×", caption_size="small", caption_shift=-40)
    assert ok.model_dump(exclude_none=True) == {"hook_text": "", "header_text": "SPEED ×", "caption_size": "small", "caption_shift": -40}
    for bad in ({"caption_size": "huge"}, {"caption_shift": 999}, {"hook_text": "x" * 81}):
        with pytest.raises(Exception):
            edits.TextEditIn(**bad)
    assert api.post("/jobs/00000000-0000-0000-0000-000000000000/clips/0/text", json={"hook_text": "hi"}).status_code == 401  # needs the token
