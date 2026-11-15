import re
from clipper import fastrender


def _props(layout):
    return {"style": "viral", "duration": 20.0,
            "captions": {"enabled": True, "style": "word", "position": "middle", "emphasis": [],
                         "words": [{"w": "Go.", "s": 1.0, "e": 1.4}]},
            "hook": {"text": "He lost everything", "duration": 2.5},
            "overlays": [], "layout": layout}


def _hook_y(ass):
    m = re.search(r"Hook,,0,0,0,,\{\\an8\\pos\(540,(\d+)\)", ass)
    assert m, "hook line missing"
    return int(m.group(1))


def test_hook_top_in_full_layout():
    y = _hook_y(fastrender.build_ass(_props({"mode": "full"}), 1300))
    assert y == fastrender.SAFE_TOP + 30


def test_hook_below_webcam_in_split():
    lay = {"mode": "split", "facecamShare": 0.4, "captionBar": False,
           "facecamBox": {"x": 0, "y": 0, "w": 0.27, "h": 0.27}}
    y = _hook_y(fastrender.build_ass(_props(lay), 1300))
    cam_h = round(fastrender.H * 0.4)
    assert y >= cam_h + 30 and y <= fastrender.H - 520


def test_hook_below_caption_bar_in_split():
    lay = {"mode": "split", "facecamShare": 0.22, "captionBar": True,
           "facecamBox": {"x": 0, "y": 0, "w": 0.27, "h": 0.27}}
    y = _hook_y(fastrender.build_ass(_props(lay), 1300))
    assert y >= round(fastrender.H * 0.22) + fastrender.CAPTION_BAR + 30
