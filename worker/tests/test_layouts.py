"""GTA VI presets: geometry stays inside the canvas, the crop dodges the baked-in camera, the stills draw, and both layouts render."""
import shutil
import subprocess

import pytest
from PIL import Image

from clipper import fastrender, layouts as L

BOX = {"x": 0.027, "y": 0.454, "w": 0.303, "h": 0.472}  # a streamer cam baked into the left-bottom of a 16:9 stream


def test_frame_opening_is_measured_from_the_image():
    fi = L.frame_info("neon_palm")
    assert (fi["w"], fi["h"]) == (1460, 1078)
    assert 1.4 < fi["ow"] / fi["oh"] < 1.5 and 100 < fi["ox"] < 130  # the camera opening: 1231 x 850 at about (114, 114)


def test_cam_layout_stack_and_safe_area():
    g = L.geometry("cam", 1920, 1080, BOX, "neon_palm", None)
    fr, cam, game = g["frame"], g["cam"], g["game"]
    assert fr["w"] == 720 and fr["x"] * 2 + fr["w"] == L.W                     # two thirds of the width, centred
    assert fr["x"] <= cam["x"] and cam["x"] + cam["w"] <= fr["x"] + fr["w"]    # the camera sits inside its frame
    assert cam["y"] >= fr["y"] and cam["y"] + cam["h"] <= fr["y"] + fr["h"]
    assert g["header"]["y"] + g["header"]["h"] <= fr["y"]                      # header above the frame
    assert fr["y"] + fr["h"] < g["captionY"] < game["y"] + 80                  # captions right under the frame
    assert game["y"] + game["h"] <= 1800 and all(v % 2 == 0 for v in (game["y"], game["h"], cam["w"], cam["h"]))


def test_crop_slides_away_from_the_baked_in_camera():
    game = L.geometry("cam", 1920, 1080, BOX, "neon_palm", None)["game"]
    assert game["camVisible"] == 0.0 and game["focusX"] > 0.6                  # window moved right of the camera: Speed is seen once
    mid = L.geometry("cam", 1920, 1080, {"x": 0.4, "y": 0.4, "w": 0.2, "h": 0.3}, "neon_palm", None)["game"]
    assert mid["camVisible"] > 0.1                                             # a camera in the middle cannot be dodged: said honestly


def test_hook_layout_keeps_the_whole_widescreen_picture():
    g = L.geometry("hook", 1920, 1080, None, None, (804, 192))
    assert "cam" not in g and "frame" not in g                                 # no extra webcam, no frame
    assert g["game"]["whole"] and (g["game"]["w"], g["game"]["h"]) == (1080, 608)
    assert g["hook"]["y"] + g["hook"]["h"] < g["game"]["y"] and g["captionY"] > g["game"]["y"] + g["game"]["h"]  # captions under the gameplay


def test_stills_draw(tmp_path):
    h = L.header_png("SPEED ×", tmp_path / "h.png")
    assert Image.open(h).size == (1080, L.HEADER_H)
    p, (w, hh) = L.hook_card_png("Speed finally got his hands on GTA 6 😭", tmp_path / "k.png")
    assert w <= 920 and 100 < hh < 260 and Image.open(p).size == (w, hh)
    long, (w2, _) = L.hook_card_png("A very long hook that goes on and on about something quite unusual happening here", tmp_path / "k2.png")
    assert w2 <= 920                                                          # long text shrinks/wraps, never overflows the card width


@pytest.mark.skipif(not shutil.which(fastrender.FFMPEG) and not __import__("os").path.exists(fastrender.FFMPEG), reason="ffmpeg-full not installed")
@pytest.mark.parametrize("kind", ["cam", "hook"])
def test_both_layouts_render_to_1080x1920(tmp_path, kind):
    pub = tmp_path / "public"
    pub.mkdir()
    subprocess.run([fastrender.FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:d=2:r=30", "-f", "lavfi", "-i", "sine=f=300:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(pub / "base.mp4")], check=True)
    a = L.build_assets(kind, pub / "ov", "SPEED ×", "Speed got his hands on GTA 6 😭")
    if kind == "cam":
        shutil.copy2(L.FRAMES["neon_palm"], pub / "ov" / "frame_neon_palm.png")
        a["files"]["frame"] = pub / "ov" / "frame_neon_palm.png"
    spec = L.geometry(kind, 1920, 1080, BOX if kind == "cam" else None, "neon_palm" if kind == "cam" else None, a["hook_size"])
    spec["files"] = {k: f"ov/{v.name}" for k, v in a["files"].items()}
    words = [{"w": w, "s": 0.2 + i * 0.4, "e": 0.55 + i * 0.4} for i, w in enumerate("one two three four".split())]
    p = {"style": "viral", "fps": 30, "duration": 2.0, "src": "base.mp4", "srcW": 1920, "srcH": 1080, "template": "gta_" + kind,
         "layout": {"mode": "fit", "facecamBox": BOX, "gameplayFocusX": 0.5, "facecamShare": 0.5, "gta": spec},
         "zooms": [], "transitions": [], "overlays": [], "sfx": [], "music": None, "logos": [],
         "captions": {"enabled": True, "style": "karaoke", "position": "middle", "words": words, "emphasis": []},
         "hook": {"enabled": False, "text": "x"}, "captionY": spec["captionY"], "captionAccent": L.PINK, "captionOnlyActive": True, "captionScale": 1}
    out = tmp_path / "out.mp4"
    fastrender.render(p, pub, out, tmp_path / "work")
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True).stdout.strip()
    assert probe == "1080,1920"


def test_caption_font_is_small_enough_for_one_line():
    groups = ["SPAT OFF ANYONE", "I", "WHAT DOG WHY"]
    size = L.caption_size(groups, 120)  # a font too big for three words on one line gets shrunk to fit
    font = __import__("PIL.ImageFont", fromlist=["x"]).truetype(str(L.FONT_BLACK), size)
    assert all(font.getlength(g) * 1.12 <= 900 for g in groups) and 40 <= size < 120   # the widest group fits on one line
    assert L.caption_size(["OK"], 74) == 74                                           # short captions keep the full size


def test_picture_is_centred_and_a_hook_card_fits_above_it_without_moving_it():
    with_card = L.geometry("hook", 1920, 1080, None, None, (804, 192))
    without = L.geometry("hook", 1920, 1080, None, None, None)
    assert "hook" in with_card and "hook" not in without
    g = without["game"]
    assert abs((g["y"] * 2 + g["h"]) - L.H) <= 4                                   # vertically centred on the 1920 px screen
    assert with_card["game"]["y"] == g["y"]                                         # adding a hook does not move the stream
    hk = with_card["hook"]
    assert L.HEADER_Y + L.HEADER_H < hk["y"] and hk["y"] + hk["h"] < g["y"]        # the card sits between the header and the picture
    assert without["captionY"] > g["y"] + g["h"] + 100


def test_caption_shift_moves_the_captions_only():
    a = L.geometry("hook", 1920, 1080, None, None, None, 0)
    b = L.geometry("hook", 1920, 1080, None, None, None, 60)
    assert b["captionY"] == a["captionY"] + 60 and b["game"] == a["game"]


def test_a_vertical_phone_video_is_cropped_to_a_band_not_stretched_off_the_screen():
    g = L.geometry("hook", 1080, 1920, None, None, None)["game"]
    assert g["h"] == L.MAX_GAME_H and g["whole"] is False and g["y"] + g["h"] < 1500    # captions still have room below
    wide = L.geometry("hook", 1920, 1080, None, None, None)["game"]
    assert wide["whole"] is True and wide["h"] == 608                                     # a 16:9 stream is still shown whole


def test_the_header_is_drawn_only_when_switched_on(tmp_path):
    off = L.build_assets("hook", tmp_path / "a", "SPEED ×", "")
    on = L.build_assets("hook", tmp_path / "b", "SPEED ×", "", True)
    assert "header" not in off["files"] and "header" in on["files"] and on["files"]["header"].exists()
