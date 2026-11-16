"""The two GTA VI presets, drawn on a 1080x1920 canvas over a blurred copy of the gameplay:

  gta_cam   header / big streamer cam inside the owner's glowing PNG frame / captions / gameplay filling the rest.
            The gameplay crop slides away from the streamer's baked-in camera so he is seen once.
  gta_hook  header / white rounded hook card (+ optional emoji) / the whole 16:9 gameplay at full width / captions under it.
            No extra webcam: the camera already in the footage stays.

This module only decides positions and draws the small still images (header, hook card). fastrender.py composes them.
Sizes are the owner's references scaled to 1080 wide: cam about 2/3 of the width, gameplay full width, captions in white
with a thick black outline and the spoken word in hot pink."""
import functools
import hashlib
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config

W, H = 1080, 1920
PUB = config.RENDERER / "public"
FRAMES = {"neon_palm": PUB / "frames" / "neon_palm.png"}
FONT_BLACK = PUB / "fonts" / "Montserrat-Black.ttf"
LOGO = PUB / "logos" / "gta6.png"
PINK = "#FF00E5"
KINDS = {"gta_cam": "cam", "gta_hook": "hook"}
FAMILY = {"gta6_gameplay", "gta_cam", "gta_hook"}  # templates that belong to the GTA VI set
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍\U0001F3FB-\U0001F3FF]+")

HEADER_Y, HEADER_H = 96, 150
CAM_MARGIN = 0.035   # share of the frame width kept clear around a detected baked-in camera
CAM_W = 720            # the frame is two thirds of the canvas width
EMOJI_FONTS = [(Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"), 109),
               (Path("/System/Library/Fonts/Apple Color Emoji.ttc"), 160)]


# ---------------------------------------------------------------- the glowing frame
@functools.lru_cache(maxsize=8)
def _frame_info(path: str, mtime: int) -> dict:
    """Size of the frame image and of its transparent opening (found from the middle outwards on the alpha channel)."""
    im = Image.open(path).convert("RGBA")
    a = im.getchannel("A")
    w, h = im.size
    px = a.load()
    cx, cy = w // 2, h // 2

    def walk(x, y, dx, dy):
        while 0 <= x + dx < w and 0 <= y + dy < h and px[x + dx, y + dy] < 40:
            x, y = x + dx, y + dy
        return x, y

    x0, _ = walk(cx, cy, -1, 0)
    x1, _ = walk(cx, cy, 1, 0)
    _, y0 = walk(cx, cy, 0, -1)
    _, y1 = walk(cx, cy, 0, 1)
    return {"w": w, "h": h, "ox": x0, "oy": y0, "ow": x1 - x0 + 1, "oh": y1 - y0 + 1}


def frame_info(name: str) -> dict | None:
    p = FRAMES.get(name)
    return _frame_info(str(p), p.stat().st_mtime_ns) if p and p.exists() else None


# ---------------------------------------------------------------- geometry
def crop_focus(src_w: int, src_h: int, win_w: int, win_h: int, cam_box: dict | None, default: float = 0.5) -> tuple[float, float]:
    """(focus_x, overlap): focus_x is the crop window's CENTRE as a share of the frame width that keeps the streamer's baked-in
    camera out of the gameplay window where the frame allows it, else the position that shows the least of it, nearest the
    middle. overlap is the share of the frame width of the camera that is still inside the window (0 = not visible)."""
    wf = min(1.0, (win_w / win_h) * src_h / src_w)  # window width as a share of the source width
    if not cam_box or wf >= 0.999:
        return default, 0.0
    # the detected box is the face area; the camera the streamer drew is a little bigger: keep a margin so no edge of it peeks in
    c0, c1 = max(0.0, cam_box["x"] - CAM_MARGIN), min(1.0, cam_box["x"] + cam_box["w"] + CAM_MARGIN)
    mid = (1 - wf) / 2
    best = None
    for i in range(61):
        x0 = (1 - wf) * i / 60
        overlap = max(0.0, min(x0 + wf, c1) - max(x0, c0))
        key = (round(overlap, 3), abs(x0 - mid))
        if best is None or key < best[0]:
            best = (key, x0)
    return round(best[1] + wf / 2, 4), best[0][0]


def geometry(kind: str, src_w: int, src_h: int, cam_box: dict | None, frame: str | None, hook_size: tuple[int, int] | None) -> dict:
    """Where everything goes. kind: 'cam' or 'hook'. All numbers are canvas pixels (even)."""
    g: dict = {"kind": kind, "header": {"x": 0, "y": HEADER_Y, "w": W, "h": HEADER_H}}
    if kind == "cam":
        fi = frame_info(frame or "") if frame and frame != "none" else None
        if fi:
            s = CAM_W / fi["w"]
            fh = round(fi["h"] * s) // 2 * 2
            fx, fy = (W - CAM_W) // 2, HEADER_Y + HEADER_H + 14
            g["frame"] = {"name": frame, "x": fx, "y": fy, "w": CAM_W, "h": fh}
            g["cam"] = {"x": fx + round(fi["ox"] * s), "y": fy + round(fi["oy"] * s), "w": round(fi["ow"] * s) // 2 * 2,
                        "h": round(fi["oh"] * s) // 2 * 2, "radius": round(30 * s)}  # the opening's corners are slightly rounded
            bottom = fy + fh
        else:  # no frame chosen: a plain rounded card of the same size and shape
            cw = 640
            ch = round(cw / 1.447) // 2 * 2
            cy = HEADER_Y + HEADER_H + 40
            g["cam"] = {"x": (W - cw) // 2, "y": cy, "w": cw, "h": ch, "radius": 36}
            bottom = cy + ch
        g["captionY"] = bottom + 64
        top = bottom + 24
        h = min(880, (1700 - top)) // 2 * 2
        focus, overlap = crop_focus(src_w, src_h, W, h, cam_box)
        hmax = (1790 - top) // 2 * 2  # a taller card = a narrower window = less of the picture's sides, which can clear the camera
        for hh in range(h + 20, hmax + 1, 20):
            if overlap <= 0.003:
                break
            f2, o2 = crop_focus(src_w, src_h, W, hh, cam_box)
            if o2 < overlap:
                h, focus, overlap = hh, f2, o2
        g["game"] = {"x": 0, "y": top, "w": W, "h": h, "whole": False, "focusX": focus, "camVisible": round(overlap, 3)}
    else:
        hw, hh = hook_size or (700, 150)
        hook_y = HEADER_Y + HEADER_H + 40
        g["hook"] = {"x": (W - hw) // 2, "y": hook_y, "w": hw, "h": hh}
        gh = round(W * src_h / src_w) // 2 * 2  # the whole 16:9 picture at full width
        top = hook_y + hh + 56
        g["game"] = {"x": 0, "y": top, "w": W, "h": gh, "whole": True, "focusX": 0.5}
        g["captionY"] = top + gh + 72
    return g


# ---------------------------------------------------------------- still images
def _emoji_image(ch: str, px: int) -> Image.Image | None:
    """A colour emoji as an RGBA image: the bundled Fluent set when present, else the system colour-emoji font."""
    try:
        from .fastrender import emoji_png
        p = emoji_png(ch)
        if p:
            return Image.open(p).convert("RGBA").resize((px, px), Image.LANCZOS)
    except Exception:  # noqa: BLE001 - fall through to the font
        pass
    for fp, size in EMOJI_FONTS:
        if not fp.exists():
            continue
        try:
            f = ImageFont.truetype(str(fp), size)
            im = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((size // 2, size // 4), ch, font=f, embedded_color=True)
            box = im.getbbox()
            if box:
                return im.crop(box).resize((px, px), Image.LANCZOS)
        except Exception:  # noqa: BLE001
            continue
    return None


def header_png(text: str, out: Path) -> Path:
    """'SPEED ×' in bold white with a thick black outline, then the GTA VI logo, centred."""
    img = Image.new("RGBA", (W, HEADER_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    t = (text or "").strip().upper()
    logo = Image.open(LOGO).convert("RGBA") if LOGO.exists() else None
    lh = 118
    if logo:
        logo = logo.resize((round(logo.width * lh / logo.height), lh), Image.LANCZOS)
    size = 88
    while True:
        font = ImageFont.truetype(str(FONT_BLACK), size)
        tw = d.textlength(t, font=font) if t else 0
        total = tw + (30 if t and logo else 0) + (logo.width if logo else 0)
        if total <= W - 80 or size <= 40:
            break
        size -= 4
    x = (W - total) / 2
    if t:
        d.text((x, HEADER_H / 2), t, font=font, fill="white", stroke_width=9, stroke_fill="black", anchor="lm")
    if logo:
        img.alpha_composite(logo, (round(x + tw + (30 if t else 0)), (HEADER_H - lh) // 2))
    img.save(out)
    return out


def hook_card_png(text: str, out: Path, max_w: int = 840) -> tuple[Path, tuple[int, int]]:
    """White rounded card, bold black uppercase text on one or two lines, an optional trailing emoji."""
    emoji = (EMOJI_RE.findall(text or "") or [""])[-1].replace("️", "")
    body = EMOJI_RE.sub("", text or "").strip().upper()
    pad_x, pad_y = 46, 30
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    for size in range(68, 40, -4):
        font = ImageFont.truetype(str(FONT_BLACK), size)
        emoji_w = round(size * 1.05) + 14 if emoji else 0
        lines, cur = [], ""
        for word in body.split():
            trial = f"{cur} {word}".strip()
            if probe.textlength(trial, font=font) <= max_w - 2 * pad_x - emoji_w:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
        lines = [ln for ln in lines if ln]
        if len(lines) <= 2:
            break
    line_h = round(size * 1.18)
    text_w = max(probe.textlength(ln, font=font) for ln in lines) if lines else 0
    w = round(text_w + 2 * pad_x + emoji_w)
    h = len(lines) * line_h + 2 * pad_y
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=44, fill="white")
    y = pad_y + line_h / 2
    for ln in lines:
        d.text(((w - emoji_w) / 2, y), ln, font=font, fill="#111111", anchor="mm")
        y += line_h
    if emoji:
        em = _emoji_image(emoji, round(size * 1.05))
        if em:
            img.alpha_composite(em, (w - pad_x - em.width + 6, (h - em.height) // 2))
    img.save(out)
    return out, (w, h)


def build_assets(kind: str, out_dir: Path, header_text: str, hook_text: str) -> dict:
    """Write the header (and hook card) images into out_dir. Names carry a hash of their content, so a changed text re-draws."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = lambda *a: hashlib.sha1("|".join(a).encode()).hexdigest()[:10]  # noqa: E731
    files = {"header": out_dir / f"header_{tag(header_text, str(LOGO.stat().st_mtime_ns) if LOGO.exists() else '')}.png"}
    if not files["header"].exists():
        header_png(header_text, files["header"])
    size = None
    if kind == "hook" and (hook_text or "").strip():
        files["hook"] = out_dir / f"hook_{tag(hook_text)}.png"
        size = hook_card_png(hook_text, files["hook"])[1] if not files["hook"].exists() else Image.open(files["hook"]).size
    return {"files": files, "hook_size": size}
