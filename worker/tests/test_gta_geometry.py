"""The GTA 6 template stack: webcam top-centre, captions under it, a wide centred gameplay card, all inside the safe area."""
from clipper.templates import GTA_BOTTOM_SAFE, GTA_W, gta_geometry

BOX = {"x": 0.02, "y": 0.55, "w": 0.2, "h": 0.4}


def test_webcam_centred_captions_between_cam_and_card():
    g = gta_geometry("center", BOX, 1920, 1080)
    cam = g["cam"]
    assert cam["x"] * 2 + cam["w"] == GTA_W                      # horizontally centred
    assert cam["y"] + cam["h"] < g["captionY"] < g["fitTop"]     # captions sit under the webcam, above the game
    assert g["fitTop"] + g["fitH"] <= GTA_BOTTOM_SAFE            # nothing under the platform UI
    assert g["fitH"] == 810                                      # a 4:3 window, not the tight 9:16 crop


def test_whole_frame_option_shows_the_full_16_9_picture():
    g = gta_geometry("fit", BOX, 1920, 1080)
    assert g["fitH"] == 608                                      # 1080 * 9 / 16: no cropping at all


def test_big_webcam_never_pushes_the_card_off_screen():
    g = gta_geometry("center", BOX, 1920, 1080, "large")
    assert g["fitTop"] + g["fitH"] <= GTA_BOTTOM_SAFE and g["fitH"] >= 600


def test_no_webcam_centres_the_gameplay_card():
    g = gta_geometry("center", None, 1920, 1080)
    assert g["cam"] is None and abs((g["fitTop"] * 2 + g["fitH"]) - 1920) <= 4
