"""Fast renderer: the same props Remotion gets -> 1080x1920 MP4 in ONE ffmpeg pass (about 10x faster on the M1).
Video: layout (full / split / bubble / fit) with time-varying zooms done as scale(eval=frame)+crop, captions, hook and
text stickers drawn by libass (ASS subtitles), flashes as drawbox, hardware H.264 (VideoToolbox).
Audio: source + music (looped, ducked under speech, faded) + SFX, mixed without normalising (finalize() sets loudness).
Remotion stays the fallback (Settings > render engine) and is used automatically if this renderer fails.
Needs Homebrew's ffmpeg-full (libass); the system ffmpeg has no subtitle/text filters."""
import os
import platform
import re
import shutil
from pathlib import Path

import numpy as np

from . import config

_ffmpeg_default = "/usr/bin/ffmpeg" if platform.system() == "Linux" else "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
FFMPEG = os.environ.get("FFMPEG_FULL") or _ffmpeg_default
if not Path(FFMPEG).exists():
    FFMPEG = shutil.which("ffmpeg") or FFMPEG
FONTS = config.RENDERER / "public" / "fonts"
W, H = 1080, 1920
CAPTION_BAR = 200  # px height of the caption strip between webcam and gameplay (split layout, "bar" captions)
FONT_K = 1.56  # libass font sizes come out at 1/1.56 of the same CSS px size in Chrome (measured on rendered frames)
SAFE_SIDE, SAFE_TOP = 80, 230
# mirrors THEMES in renderer/src/Clip.tsx (font = full font name inside renderer/public/fonts)
THEMES = {
    "viral":     dict(size=92, accent="#FFE600", hot="#FF3B3B", stroke=7, upper=True, hook_bg="#FFFFFF", hook_fg="#111111", lower_y=0.7, font="Montserrat Black"),
    "funny":     dict(size=92, accent="#7CFF4F", hot="#FF4FD8", stroke=7, upper=True, hook_bg="#FFE600", hook_fg="#111111", lower_y=0.7, font="Luckiest Guy"),
    "fast":      dict(size=112, accent="#00E5FF", hot="#FF3B3B", stroke=7, upper=True, hook_bg="#111111", hook_fg="#FFFFFF", lower_y=0.7, font="Anton"),
    "cinematic": dict(size=84, accent="#F5D06F", hot="#F5D06F", stroke=0, upper=True, hook_bg="#000000", hook_fg="#FFFFFF", lower_y=0.73, font="Bebas Neue"),
    "minimal":   dict(size=70, accent="#FFFFFF", hot="#FFE600", stroke=5, upper=False, hook_bg="#FFFFFF", hook_fg="#111111", lower_y=0.72, font="Montserrat ExtraBold"),
}


# Caption palette (owner's choice, 2026-11-15): white words, ONE neon for the word being spoken and highlighted words,
# red only for the one or two strongest words of a clip. Same for every style.
NEON, RED = "#7CFF4F", "#FF3B3B"
RED_MAX = 2

EMOJI_DIR = config.ROOT / "vendor" / "fluentui-emoji" / "assets"
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\U0001F3FB-\U0001F3FF]+")
_EMOJI: dict | None = None


def emoji_png(ch: str) -> Path | None:
    """Microsoft Fluent 3D emoji (MIT, vendor/fluentui-emoji) for an emoji character; None if not in the set."""
    global _EMOJI
    if _EMOJI is None:
        import json
        _EMOJI = {}
        for meta in EMOJI_DIR.glob("*/metadata.json") if EMOJI_DIR.exists() else []:
            try:
                g = json.loads(meta.read_text()).get("glyph", "")
            except ValueError:
                continue
            pngs = sorted(meta.parent.glob("**/3D/*.png"), key=lambda x: ("Default" not in str(x), len(str(x))))
            if g and pngs:
                _EMOJI[g.replace("\ufe0f", "")] = pngs[0]
    return _EMOJI.get((ch or "").strip().replace("\ufe0f", ""))


def encoder() -> str:
    """The video encoder the fast renderer uses on this host (NVENC is GPU; VideoToolbox is Apple hardware)."""
    return "h264_nvenc" if platform.system() == "Linux" else "h264_videotoolbox"


def available() -> bool:
    return Path(FFMPEG).exists() and FONTS.exists()


# ---------- small helpers ----------

def _ass_color(hexc: str, alpha: int = 0) -> str:
    h = hexc.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _esc(s: str) -> str:
    return s.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def _norm(w: str) -> str:
    return re.sub(r"[^\w']", "", w.lower())


def _f(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") or "0"


def group_words(words: list[dict]) -> list[list[dict]]:
    """Same grouping as Clip.tsx groupWords: <=3 words, <=16 chars, split on pauses and sentence ends."""
    groups, cur = [], []
    for w in words:
        chars = sum(len(x["w"]) + 1 for x in cur)
        if cur and (len(cur) >= 3 or chars + len(w["w"]) > 16 or w["s"] - cur[-1]["e"] > 0.45 or re.search(r"[.!?]$", cur[-1]["w"])):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


# ---------- time expressions (ffmpeg expr) ----------

def _smooth(x: str) -> str:
    return f"(({x})*({x})*(3-2*({x})))"


def zoom_expr(zooms: list[dict], targets: tuple[str, ...], trans: list[dict], mul: float = 1.0) -> tuple[str, str, str]:
    """(scale, focus_x, focus_y) expressions of t, matching zoomAt() in Clip.tsx plus zoom_punch transitions."""
    parts, fx, fy = [], "0.5", "0.5"
    for z in [z for z in zooms if z["target"] in targets]:
        s, e = z["start"], z["end"]
        d = max(0.05, e - s)
        ind = min(0.14, d / 3) if z["style"] == "punch" else d * 0.7
        outd = min(0.25, d / 3) if z["style"] == "punch" else min(0.4, d * 0.3)
        k = (f"if(between(t,{_f(s)},{_f(s + ind)}),{_smooth(f'(t-{_f(s)})/{_f(ind)}')},"
             f"if(between(t,{_f(s + ind)},{_f(e - outd)}),1,"
             f"if(between(t,{_f(e - outd)},{_f(e)}),{_smooth(f'({_f(e)}-t)/{_f(outd)}')},0)))")
        parts.append(f"{_f((z['scale'] - 1) * mul)}*{k}")
        fx = f"if(between(t,{_f(s)},{_f(e)}),{_f(z['fx'])},{fx})"
        fy = f"if(between(t,{_f(s)},{_f(e)}),{_f(z['fy'])},{fy})"
    for tr in trans:
        if tr["kind"] == "zoom_punch":  # 1.14 -> 1 over 0.3 s, ease out
            a = _f(tr["at"])
            parts.append(f"0.14*if(between(t,{a},{a}+0.3),pow(1-(t-{a})/0.3,3),0)")
    scale = "min(1.9,1+" + "+".join(parts) + ")" if parts else "1"
    return scale, fx, fy


def shake_expr(trans: list[dict], axis: str) -> str:
    terms = []
    for tr in trans:
        if tr["kind"] == "shake":
            a = _f(tr["at"])
            f = 70 if axis == "x" else 83
            fn = "sin" if axis == "x" else "cos"
            terms.append(f"if(between(t,{a},{a}+0.45),{fn}((t-{a})*{f})*22*(1-(t-{a})/0.45),0)")
    return "+".join(terms) or "0"


# ---------- geometry (mirrors Region in Clip.tsx) ----------

def cover_rect(srcW, srcH, w, h, box: dict | None, focus_x=0.5, area: dict | None = None) -> tuple[int, int, int, int]:
    """Source rectangle (x, y, cw, ch) that 'covers' a w x h window: the whole frame (or only its real picture `area`,
    without letterbox bars) centred on focus_x, or a facecam box."""
    if area and not box:
        ax, ay, aw, ah = area["x"] * srcW, area["y"] * srcH, area["w"] * srcW, area["h"] * srcH
        k = max(w / aw, h / ah)
        cw, ch = min(aw, w / k), min(ah, h / k)
        x = min(max(ax, ax + focus_x * aw - cw / 2), ax + aw - cw)
        y = min(max(ay, ay + ah / 2 - ch / 2), ay + ah - ch)
        return int(x) // 2 * 2, int(y) // 2 * 2, int(cw) // 2 * 2, int(ch) // 2 * 2
    if box:
        k = max(w / (box["w"] * srcW), h / (box["h"] * srcH))
        cx, cy = (box["x"] + box["w"] / 2) * srcW, (box["y"] + box["h"] / 2) * srcH
    else:
        k = max(w / srcW, h / srcH)
        cx, cy = focus_x * srcW, srcH / 2
    cw, ch = min(srcW, w / k), min(srcH, h / k)
    x = min(max(0.0, cx - cw / 2), srcW - cw)
    y = min(max(0.0, cy - ch / 2), srcH - ch)
    return int(x) // 2 * 2, int(y) // 2 * 2, int(cw) // 2 * 2, int(ch) // 2 * 2


def _region(inp: str, out: str, rect, w: int, h: int, z: tuple[str, str, str], shake=("0", "0"), xexpr: str | None = None) -> str:
    """Crop a source rect, scale to w x h * zoom(t), crop the w x h window at the zoom focus (+ shake).
    xexpr: optional ffmpeg expression of t for the crop's x (the auto-reframe camera path) instead of the fixed x."""
    x, y, cw, ch = rect
    xc = f"'{xexpr}':{y}:exact=1" if xexpr else f"{x}:{y}"
    s, fx, fy = z
    zoomed = s != "1" or shake != ("0", "0")
    if not zoomed:
        return f"[{inp}]crop={cw}:{ch}:{xc},scale={w}:{h}:flags=bicubic,setsar=1[{out}]"
    pad = "1.04" if shake != ("0", "0") else "1"  # a little headroom so a shake never shows an edge
    return (f"[{inp}]crop={cw}:{ch}:{xc},"
            f"scale=w='trunc({w}*{pad}*({s})/2)*2':h='trunc({h}*{pad}*({s})/2)*2':eval=frame:flags=bicubic,"
            f"crop={w}:{h}:x='max(0,min({w}*{pad}*({s})-{w},({w}*{pad}*({s})-{w})*({fx})+({shake[0]})))'"
            f":y='max(0,min({h}*{pad}*({s})-{h},({h}*{pad}*({s})-{h})*({fy})+({shake[1]})))',setsar=1[{out}]")


def write_mask(path: Path, w: int, h: int, radius: float):
    """Anti-aliased rounded-rectangle (or circle, radius = w/2) alpha mask as a PGM image."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32) + 0.5
    rx = np.clip(np.maximum(radius - xx, xx - (w - radius)), 0, None)
    ry = np.clip(np.maximum(radius - yy, yy - (h - radius)), 0, None)
    d = np.sqrt(rx ** 2 + ry ** 2) - radius
    a = np.clip(0.5 - d, 0, 1) * 255
    with path.open("wb") as f:
        f.write(f"P5 {w} {h} 255\n".encode())
        f.write(a.astype(np.uint8).tobytes())


# ---------- ASS (captions, hook, stickers) ----------

def build_ass(p: dict, caption_y: float) -> str:
    th = dict(THEMES.get(p.get("style") or "viral", THEMES["viral"]))
    if p["layout"].get("captionBar") and p["layout"]["mode"] == "split":
        th["size"] = round(th["size"] * 0.8)  # one line that fits inside the caption strip
    accent, hot = _ass_color(NEON), _ass_color(RED)
    white = "&H00FFFFFF"
    up = (lambda s: s.upper()) if th["upper"] else (lambda s: s)
    border = 1 if th["stroke"] else 1
    shadow_alpha = 0x70 if th["stroke"] else 0x30
    styles = [
        f"Style: Cap,{th['font']},{round(th['size'] * FONT_K)},{white},{white},&H00000000,&H{shadow_alpha:02X}000000,0,0,0,0,100,100,-1,0,{border},"
        f"{th['stroke'] or 2},{6 if th['stroke'] else 3},5,{SAFE_SIDE},{SAFE_SIDE},0,1",
        # hook: clean white bold text with a dark outline and soft shadow (no coloured box), last word in neon
        f"Style: Hook,Montserrat Black,{round(84 * FONT_K)},{white},{white},&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,"
        f"7,5,8,{SAFE_SIDE + 20},{SAFE_SIDE + 20},0,1",
        f"Style: Sticker,Montserrat Black,{round(62 * FONT_K)},{white},{white},{_ass_color(RED)},&H80000000,0,0,0,0,100,100,0,0,3,14,0,5,{SAFE_SIDE},{SAFE_SIDE},0,1",
    ]
    ev = []
    cap = p["captions"]
    caps: list[list] = []  # [start, end, text]; trimmed below so two captions are never on screen together
    if cap.get("enabled", True) and cap.get("words"):
        emph = {_norm(e) for e in cap.get("emphasis") or []}
        red = {_norm(e) for e in (cap.get("emphasis") or [])[:RED_MAX]}  # red only for the strongest one or two words
        groups = group_words(cap["words"])
        for gi, g in enumerate(groups):
            g_start = g[0]["s"] - 0.05
            g_end = min(g[-1]["e"] + 0.35, groups[gi + 1][0]["s"] if gi + 1 < len(groups) else 1e9)
            # one event per state: before the first word is active, then each word in turn
            for k, w in enumerate(g):
                st = g_start if k == 0 else w["s"]
                en = g[k + 1]["s"] if k + 1 < len(g) else g_end
                if en - st < 0.02:
                    continue
                toks = []
                for j, x in enumerate(g):
                    is_e = _norm(x["w"]) in emph
                    color = hot if _norm(x["w"]) in red else accent if (is_e or j == k) else (
                        "&H73FFFFFF" if cap.get("style") == "karaoke" and j > k else white)
                    tag = f"\\c{color}\\fscx{110 if j == k else 100}\\fscy{110 if j == k else 100}\\frz{2 if (is_e and j == k) else 0}"
                    toks.append(f"{{{tag}}}{_esc(up(x['w']))}")
                pop = "\\fscx82\\fscy82\\t(0,160,\\fscx100\\fscy100)" if (k == 0 and cap.get("style", "pop") == "pop") else ""
                caps.append([st, en, f"{{\\an5\\pos(540,{int(caption_y)}){pop}}}" + " ".join(toks)])
        caps.sort(key=lambda c: c[0])
        for i, c in enumerate(caps):
            if i + 1 < len(caps):
                c[1] = min(c[1], caps[i + 1][0])
            if c[1] - c[0] >= 0.02:
                ev.append(f"Dialogue: 1,{_ts(c[0])},{_ts(c[1])},Cap,,0,0,0,,{c[2]}")
    hook = p.get("hook") or {}
    hook_text = EMOJI_RE.sub("", hook.get("text") or "").strip()  # libass cannot draw colour emoji
    if hook.get("enabled", True) is not False and hook_text:
        d = float(hook.get("duration") or 2.5)
        words = hook_text.split()
        styled = (_esc(" ".join(words[:-1])) + " " if len(words) > 1 else "") + f"{{\\c{accent}}}" + _esc(words[-1])
        lay = p.get("layout") or {}
        hook_y = SAFE_TOP + 30
        if lay.get("mode") == "split" and lay.get("facecamBox"):
            # the top of the screen is the streamer's face: the hook goes just below the webcam (and the caption strip)
            cam_h = round(H * lay.get("facecamShare", 0.22))
            hook_y = min(cam_h + (CAPTION_BAR if lay.get("captionBar") else 0) + 34, H - 520)
        ev.append(f"Dialogue: 2,{_ts(0)},{_ts(d)},Hook,,0,0,0,,{{\\an8\\pos(540,{hook_y})\\fscx92\\fscy92"
                  f"\\t(0,220,\\fscx100\\fscy100)\\fad(120,220)}}{styled}")
    pos = {"top": (8, 540, SAFE_TOP + 190), "center": (5, 540, int(H * 0.4)), "upper_left": (7, SAFE_SIDE + 14, SAFE_TOP + 204),
           "upper_right": (9, W - SAFE_SIDE - 14, SAFE_TOP + 204)}
    for o in p.get("overlays") or []:
        if o["kind"] != "text" and o.get("file"):
            continue  # same as Clip.tsx: file-backed emoji/image overlays are not drawn
        text = EMOJI_RE.sub("", o["value"]).strip()  # libass cannot draw colour emoji
        if not text:
            continue
        an, x, y = pos.get(o["position"], pos["upper_right"])
        anim = ("\\fscx10\\fscy10\\t(0,220,\\fscx100\\fscy100)" if o.get("animation") != "slide_up"
                else f"\\move({x},{y + 140},{x},{y},0,250)")
        place = "" if o.get("animation") == "slide_up" else f"\\pos({x},{y})"
        ev.append(f"Dialogue: 3,{_ts(o['start'])},{_ts(o['end'])},Sticker,,0,0,0,,{{\\an{an}{place}\\frz3{anim}\\fad(0,170)}}{_esc(text)}")
    return "\n".join(["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
                      "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
                      "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
                      "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
                      *styles, "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text", *ev, ""])


def caption_y(p: dict) -> float:
    th = THEMES.get(p.get("style") or "viral", THEMES["viral"])
    lay = p["layout"]
    cam_h = round(H * lay["facecamShare"])
    pos = p["captions"].get("position", "middle")
    if lay["mode"] == "split" and lay.get("captionBar"):
        return cam_h // 2 * 2 + CAPTION_BAR / 2
    if lay["mode"] == "split":
        y = H * th["lower_y"] if pos == "lower" else cam_h + 40 + th["size"] * 0.6
    elif lay["mode"] == "fit":
        y = 300 + round(W * 1.12) - 140
    else:
        y = H * th["lower_y"] if pos == "lower" else H * 0.58
    return y + (p.get("captionOffsetY") or 0)


# ---------- audio ----------

def _speech_regions(words: list[dict], gap: float = 0.3) -> list[tuple[float, float]]:
    out = []
    for w in sorted(words, key=lambda x: x["s"]):
        if out and w["s"] - out[-1][1] <= gap:
            out[-1] = (out[-1][0], max(out[-1][1], w["e"]))
        else:
            out.append((w["s"], w["e"]))
    return out


def duck_expr(words: list[dict]) -> str:
    """0..1 duck amount at time t: 1 while someone speaks, easing to 0 over 0.3 s (duckAmount() in Clip.tsx)."""
    terms = [f"clip(1-max(max({_f(s)}-t,t-{_f(e)}),0)/0.3,0,1)" for s, e in _speech_regions(words)]
    if not terms:
        return "0"
    expr = terms[0]
    for t in terms[1:]:
        expr = f"max({expr},{t})"
    return expr


# ---------- main ----------

def render(p: dict, pub: Path, out: Path, work: Path) -> None:
    pub, out, work = pub.resolve(), out.resolve(), work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    lay = p["layout"]
    mode = lay["mode"] if (lay["mode"] not in ("split", "bubble") or lay.get("facecamBox")) else "full"
    sw, sh = p["srcW"], p["srcH"]
    dur = p["duration"]
    zooms, trans = p.get("zooms") or [], p.get("transitions") or []
    zmul = p.get("zoomScale") or 1
    shake = (shake_expr(trans, "x"), shake_expr(trans, "y"))
    gx = lay.get("gameplayFocusX", 0.5)
    area = lay.get("gameplayArea")  # the real game picture inside the source (letterbox bars removed), or None
    path = lay.get("gameplayFocusPath") or None  # auto-reframe: [(t, focus_x)] keyframes, or None for a static crop

    def gcrop(w_: int, h_: int):
        """(source rect, x(t) expression or None) for the gameplay window; follows the camera path when there is one."""
        rect = cover_rect(sw, sh, w_, h_, None, gx, area)
        if not path:
            return rect, None
        from .reframe import path_expr
        ax, aw = (area["x"] * sw, area["w"] * sw) if area else (0.0, float(sw))
        lo, hi = ax, ax + aw - rect[2]
        return rect, f"max({lo:.1f},min({hi:.1f},{ax:.1f}+({path_expr([tuple(k) for k in path])})*{aw:.1f}-{rect[2] / 2:.1f}))"

    inputs = ["-i", str(pub / p["src"])]
    fc, n_in = [], 1

    def add_input(args):
        nonlocal n_in
        inputs.extend(args)
        n_in += 1
        return n_in - 1

    zg = zoom_expr(zooms, ("gameplay", "frame") if mode in ("split", "bubble") else ("gameplay", "frame", "facecam"), trans, zmul)
    zf = zoom_expr(zooms, ("facecam",), [], zmul)
    if mode == "split":
        cam_h = round(H * lay["facecamShare"]) // 2 * 2
        bar_h = CAPTION_BAR if lay.get("captionBar") else 0
        game_h = H - cam_h - bar_h
        fc.append("[0:v]split=2[gsrc][csrc]")
        gr, gxe = gcrop(W, game_h)
        fc.append(_region("gsrc", "game", gr, W, game_h, zg, shake, gxe))
        if bar_h:  # captions get their own strip between the streamer and the game: they cover neither
            fc.append(_region("csrc", "cam", cover_rect(sw, sh, W, cam_h, lay["facecamBox"]), W, cam_h, zf))
            fc.append(f"color=c=0x0B0B0F:s={W}x{bar_h}:r=30,setsar=1[bar]")
            fc.append("[cam][bar][game]vstack=inputs=3:shortest=1[base]")
        else:
            fc.append(_region("csrc", "cam0", cover_rect(sw, sh, W, cam_h - 8, lay["facecamBox"]), W, cam_h - 8, zf))
            fc.append("[cam0]pad=w=iw:h=ih+8:color=black[cam]")
            fc.append("[cam][game]vstack=inputs=2[base]")
    elif mode == "bubble":
        b = lay["facecamBox"]
        round_ = (lay.get("bubbleShape") or "circle") == "circle"
        aspect = 1.0 if round_ else min(1.4, max(0.8, (b["w"] * sw) / (b["h"] * sh)))
        size = (lay.get("bubbleSize") or 0.34) * W
        bw_max = int(W * 0.72) // 2 * 2
        bh_max = int(bw_max / aspect) // 2 * 2
        write_mask(work / "bubble_mask.pgm", bw_max, bh_max, bw_max / 2 if round_ else 40 * bw_max / size)
        write_mask(work / "bubble_ring.pgm", bw_max, bh_max, bw_max / 2 if round_ else 46 * bw_max / size)
        mi = add_input(["-loop", "1", "-framerate", "30", "-i", str(work / "bubble_mask.pgm")])
        ri = add_input(["-loop", "1", "-framerate", "30", "-i", str(work / "bubble_ring.pgm")])
        zs = zf[0]
        grow = f"(1+(({zs})-1)*1.6)"
        bw = f"min({W * 0.72},{_f(size)}*{grow})"
        corner = lay.get("bubbleCorner") or "top_right"
        left, mid = corner.endswith("left"), corner.startswith("mid")
        kk = f"min(1,({grow}-1)/0.64)"
        x0 = "48" if left else f"({W}-48-({bw}))"
        bx = f"({x0}+((({W})-({bw}))/2-{x0})*0.5*{kk})"
        by = f"({_f(H * 0.42)}-({bw})/{_f(aspect)}/2)" if mid else str(72 if p.get("template") == "gta6_gameplay" else SAFE_TOP + 190)
        fc.append("[0:v]split=2[gsrc][csrc]")
        gr, gxe = gcrop(W, H)
        fc.append(_region("gsrc", "game", gr, W, H, zg, shake, gxe))
        inner = (f"1+(({zs})-1)*0.35", zf[1], zf[2])
        fc.append(_region("csrc", "camf", cover_rect(sw, sh, bw_max, bh_max, b), bw_max, bh_max, inner))
        fc.append(f"[{mi}:v]format=gray,setsar=1[mk]")
        fc.append("[camf][mk]alphamerge,format=yuva420p[camm]")
        sc = f"w='trunc(({bw})/2)*2':h='trunc(({bw})/{_f(aspect)}/2)*2':eval=frame"
        fc.append(f"[camm]scale={sc}[cams]")
        fc.append(f"[{ri}:v]format=gray,setsar=1[rg]")
        fc.append(f"color=c=white:s={bw_max}x{bh_max}:r=30,format=yuv420p,setsar=1[wh]")
        fc.append("[wh][rg]alphamerge,format=yuva420p[ringm]")
        ring = 8 if p.get("template") == "gta6_gameplay" else 14
        rsc = f"w='trunc((({bw})+{ring})/2)*2':h='trunc((({bw})/{_f(aspect)}+{ring})/2)*2':eval=frame"
        fc.append(f"[ringm]scale={rsc}[rings]")
        fc.append(f"[game][rings]overlay=x='{bx}-{ring // 2}':y='{by}-{ring // 2}':eval=frame:shortest=1[g1]")
        fc.append(f"[g1][cams]overlay=x='{bx}':y='{by}':eval=frame:shortest=1[base]")
    elif mode == "fit":
        fit_h, fit_top = round(W * 1.12) // 2 * 2, 300
        if p.get("bgSrc") and (pub / p["bgSrc"]).exists():
            bi = add_input(["-i", str(pub / p["bgSrc"])])
            fc.append(f"[{bi}:v]scale={W}:{H}:flags=bilinear,setsar=1,fps=30[bg]")
        else:
            fc.append(f"[0:v]scale=-2:320,crop=180:320,boxblur=12:2,eq=brightness=-0.12,scale={W}:{H},setsar=1[bg]")
        write_mask(work / "card_mask.pgm", W, fit_h, 36)
        mi = add_input(["-loop", "1", "-framerate", "30", "-i", str(work / "card_mask.pgm")])
        gr, gxe = gcrop(W, fit_h)
        fc.append(_region("0:v", "card0", gr, W, fit_h, zg, shake, gxe))
        fc.append(f"[{mi}:v]format=gray,setsar=1[cmk]")
        fc.append("[card0][cmk]alphamerge[card]")
        fc.append(f"[bg][card]overlay=0:{fit_top}:shortest=1[base]")
    else:
        gr, gxe = gcrop(W, H)
        fc.append(_region("0:v", "base", gr, W, H, zg, shake, gxe))
    v = "base"
    flashes = [tr for tr in trans if tr["kind"] == "flash"]
    for i, tr in enumerate(flashes):  # white flash fading out over 0.25 s, in three steps
        a = tr["at"]
        steps = [(0, 0.08, 0.8), (0.08, 0.16, 0.5), (0.16, 0.25, 0.22)]
        chain = ",".join(f"drawbox=x=0:y=0:w=iw:h=ih:color=white@{al}:t=fill:enable='between(t,{_f(a + s0)},{_f(a + s1)})'"
                         for s0, s1, al in steps)
        fc.append(f"[{v}]{chain}[fl{i}]")
        v = f"fl{i}"
    for i, o in enumerate(p.get("overlays") or []):
        img = emoji_png(o["value"]) if o["kind"] == "emoji" else (pub / o["file"] if o.get("file") and not o["file"].endswith(".svg") else None)
        if not img or not Path(img).exists():
            continue
        size = 300 if o["kind"] == "emoji" else 460
        x = {"upper_left": f"{SAFE_SIDE}", "upper_right": f"{W - SAFE_SIDE}-w"}.get(o["position"], "(W-w)/2")
        y = {"center": f"{int(H * 0.4)}-h/2"}.get(o["position"], f"{SAFE_TOP + 170}")
        a, e = o["start"], max(o["end"], o["start"] + 0.6)
        ii = add_input(["-loop", "1", "-framerate", "30", "-t", _f(e + 0.1), "-i", str(img)])
        pop = f"min(1,max(0.05,(t-{_f(a)})/0.18))"  # grow in over 0.18 s
        fc.append(f"[{ii}:v]format=rgba,scale={size}:-2,setpts=PTS-STARTPTS+{_f(a)}/TB,"
                  f"scale=w='max(2,trunc({size}*{pop}/2)*2)':h=-2:eval=frame[st{i}]")
        fc.append(f"[{v}][st{i}]overlay=x='{x}':y='{y}':eval=frame:enable='between(t,{_f(a)},{_f(e)})'[sv{i}]")
        v = f"sv{i}"
    for i, logo in enumerate(p.get("logos") or []):
        img = pub / logo["file"]
        if not img.exists() or img.suffix.lower() == ".svg":
            continue
        li = add_input(["-loop", "1", "-framerate", "30", "-i", str(img)])
        lw = int(logo.get("w", 285))
        lx = int(float(logo.get("x", .82)) * W - lw / 2)
        ly = f"{int(float(logo.get('y', .105)) * H)}-h/2"
        opacity = max(0.0, min(1.0, float(logo.get("opacity", 1))))
        fc.append(f"[{li}:v]format=rgba,colorchannelmixer=aa={_f(opacity)},scale={lw}:-2[lg{i}]")
        fc.append(f"[{v}][lg{i}]overlay=x={lx}:y='{ly}':shortest=1[lv{i}]")
        v = f"lv{i}"
    ass = work / "overlay.ass"
    ass.write_text(build_ass(p, caption_y(p)), encoding="utf-8")
    fc.append(f"[{v}]ass=filename={ass.name}:fontsdir={FONTS},format=yuv420p,trim=duration={_f(dur)}[vout]")
    # ---- audio ----
    amix = ["[0:a]aresample=48000,asetpts=PTS-STARTPTS[a0]"]
    alabels = ["[a0]"]
    m = p.get("music")
    if m and (pub / m["file"]).exists():
        mi = add_input(["-stream_loop", "-1", "-i", str(pub / m["file"])])
        duck = duck_expr(p["captions"].get("words") or [])
        vol = (f"pow(10,({_f(m['gainDb'])}+({_f(m.get('duckDb', -10))})*({duck}))/20)"
               f"*min(1,min(t/0.8,max(0,({_f(dur)}-t)/1.2)))")
        amix.append(f"[{mi}:a]aresample=48000,atrim=0:{_f(dur)},asetpts=PTS-STARTPTS,volume='{vol}':eval=frame[am]")
        alabels.append("[am]")
    for k, s in enumerate(p.get("sfx") or []):
        f = pub / s["file"]
        if not f.exists():
            continue
        si = add_input(["-i", str(f)])
        ms = max(0, int(round(s["at"] * 1000)))
        amix.append(f"[{si}:a]aresample=48000,volume={_f(s['gainDb'])}dB,adelay={ms}|{ms}[s{k}]")
        alabels.append(f"[s{k}]")
    if len(alabels) > 1:
        amix.append("".join(alabels) + f"amix=inputs={len(alabels)}:duration=first:normalize=0:dropout_transition=0[aout]")
    else:
        amix.append("[a0]anull[aout]")
    graph = ";".join(fc + amix)
    (work / "graph.txt").write_text(graph)
    tmp = out.with_suffix(".tmp.mp4")
    video_codec = (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0", "-maxrate", "20M", "-bufsize", "40M"]
                   if platform.system() == "Linux" else
                   ["-c:v", "h264_videotoolbox", "-b:v", "14M", "-maxrate", "20M"])
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *inputs, "-filter_complex_script", str(work / "graph.txt"),
           "-map", "[vout]", "-map", "[aout]", "-t", _f(dur), "-r", "30", *video_codec,
           "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-g", "60",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(tmp)]
    import subprocess
    p_ = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=work)
    if p_.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 10_000:
        raise RuntimeError(f"fast render failed ({p_.returncode}): {(p_.stderr or '')[-900:]}")
    shutil.move(tmp, out)
