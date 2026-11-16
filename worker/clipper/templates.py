"""Layout templates the owner picks per job (New job form, `template`):
  auto      the AI picks the layout for each clip (split, bubble, full or fit);
  full      full-screen 9:16 crop, captions on, no webcam panel (talking videos, IRL, non-gaming);
  split_50  webcam on the top half, the video on the bottom half;
  split_20  small webcam strip on top (20%), the video fills the other 80%.
The webcam position is ONE fixed rectangle per clip (detected once on the clip, else the AI's own box): nothing follows
faces or switches webcams over time (that tracking was removed on the owner's request).
The template is enforced in code after the AI's plan, and the AI is told about it in the prompt."""
import json

from . import config
from .edl import EDL, Box

TEMPLATES = {
    "auto": {"label": "Auto (the AI picks)", "mode": None, "share": None},
    "full": {"label": "Full screen + captions", "mode": "full", "share": None},
    "split_50": {"label": "Split 50/50", "mode": "split", "share": 0.5},
    "split_20": {"label": "Split 20/80", "mode": "split", "share": 0.2},
    "gta6_gameplay": {"label": "GTA 6 — Gameplay Focus", "mode": "gta6", "share": None},
    "gta_cam": {"label": "GTA VI — Framed reaction cam", "mode": "gta_cam", "share": None},
    "gta_hook": {"label": "GTA VI — Widescreen + hook card", "mode": "gta_hook", "share": None},
}


GTA_W, GTA_H = 1080, 1920
GTA_BOTTOM_SAFE = 1500  # below this the platform UI (caption, buttons) covers the picture


def gta_geometry(crop: str | None, box: dict | None, src_w: int, src_h: int, size: str | None = None) -> dict:
    """Vertical stack of the GTA 6 template on a blurred copy of the video, top to bottom:
    webcam card (top centre, only the webcam is shown) / captions / gameplay card (wide, centred, not a tight crop).
    crop "fit" shows the whole frame in the card; anything else a centred 4:3 window. Nothing leaves the safe area."""
    fit_h = round(GTA_W * src_h / src_w) // 2 * 2 if crop == "fit" else 810
    if not box:  # no webcam: the gameplay card sits in the middle, captions under it
        top = (GTA_H - fit_h) // 4 * 2
        return {"fitH": fit_h, "fitTop": top, "cam": None, "captionY": min(top + fit_h + 100, GTA_BOTTOM_SAFE)}
    frac = {"small": 0.30, "medium": 0.38, "large": 0.46}.get(size or "medium", 0.38)
    cam_w = round(GTA_W * frac) // 2 * 2
    cam_h = round(cam_w / 1.4) // 2 * 2  # one fixed card shape; the detected webcam box is cropped to fill it
    cam_top = 240
    caption_y = cam_top + cam_h + 64
    fit_top = caption_y + 66
    fit_h = min(fit_h, (GTA_BOTTOM_SAFE - fit_top) // 2 * 2)
    return {"fitH": fit_h, "fitTop": fit_top, "captionY": caption_y,
            "cam": {"x": (GTA_W - cam_w) // 2, "y": cam_top, "w": cam_w, "h": cam_h, "radius": 36}}


def _get(name: str | None) -> dict | None:
    t = TEMPLATES.get(name or "auto")
    return None if not t or t["mode"] is None else t


def prompt_rule(name: str | None) -> str:
    t = _get(name)
    if not t:
        return ""
    if t["mode"] == "full":
        return ("\n## Layout fixed by the owner: FULL SCREEN\nlayout.mode must be \"full\" (one full-screen 9:16 crop, no webcam "
                "panel). Set gameplay_focus_x to where the subject is. Keep captions enabled. Zooms target \"frame\" or "
                "\"gameplay\" only.\n")
    if t["mode"] == "gta6":
        return ("\n## Layout fixed by the owner: GTA 6 GAMEPLAY FOCUS\nThe layout is fixed in code: a webcam card at the top centre, captions "
                "just under it, and the gameplay as a wide centred card on a blurred copy of the video. Keep captions enabled. "
                "Hook text, overlays, zooms, sound effects, transitions and music are disabled; do not plan them. If no webcam exists, gameplay only.\n")
    if t["mode"] == "gta_cam":
        return ("\n## Layout fixed by the owner: GTA VI FRAMED REACTION CAM\nThe layout is fixed in code: header on top, the streamer's webcam large inside a "
                "glowing frame, captions right under the frame, the gameplay filling the rest. Keep captions enabled. Hook text, overlays, "
                "zooms, sound effects, transitions and music are disabled; do not plan them.\n")
    if t["mode"] == "gta_hook":
        return ("\n## Layout fixed by the owner: GTA VI WIDESCREEN + HOOK CARD\nThe layout is fixed in code: header on top, a white hook card, the whole "
                "widescreen gameplay at full width, captions under it. Keep captions enabled. Write `hook.text` as ONE punchy line of at most 8 words "
                "about this exact moment (it is shown in the card), optionally ending with a single emoji. Overlays, zooms, sound effects, "
                "transitions and music are disabled; do not plan them.\n")
    return (f"\n## Layout fixed by the owner: SPLIT, webcam {round(t['share'] * 100)}% of the height on top\nlayout.mode must be "
            f"\"split\" with facecam_share {t['share']} and facecam_box = the webcam rectangle in the source frame.\n")


def _detected_box(job_id, idx) -> dict | None:
    """The webcam rectangle detected once for this clip (or for the whole video). Fixed for the whole clip."""
    a = config.job_dir(str(job_id)) / "analysis"
    for f in (a / f"facecam_clip_{idx}.json", a / "facecam.json"):
        try:
            face = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if face.get("found") and face.get("box") and not face.get("fullscreen_face"):
            return {k: face["box"][k] for k in "xywh"}
    return None


def apply_template(job_id, edl: EDL, c: dict, name: str | None, options: dict | None = None) -> tuple[EDL, str | None]:
    t = _get(name)
    if not t:
        return edl, None
    lay = edl.layout
    options = options or {}
    if t["mode"] == "gta6":
        box = _detected_box(job_id, c.get("idx")) or (lay.facecam_box.model_dump() if lay.facecam_box else None)
        crop = options.get("gameplay_crop") or "center"
        focus = {"left": 0.22, "center": 0.5, "right": 0.78, "fit": 0.5}.get(crop, lay.gameplay_focus_x)  # "auto" keeps the AI's focus
        upd = {"mode": "fit", "facecam_box": Box.model_validate(box) if box else None, "gameplay_focus_x": focus}
        new = edl.model_copy(update={"layout": lay.model_copy(update=upd), "zooms": [], "overlays": [], "sfx": [], "transitions": [],
                                     "music": edl.music.model_copy(update={"enabled": False}),
                                     "captions": edl.captions.model_copy(update={"enabled": True})})  # the job's captions setting (off) still wins at render time
        return new, f"layout set by template: {t['label']}" + (" (no webcam found; gameplay-only)" if not box else "")
    if t["mode"] in ("gta_cam", "gta_hook"):  # drawn from layouts.py by the fast renderer
        box = _detected_box(job_id, c.get("idx")) or (lay.facecam_box.model_dump() if lay.facecam_box else None)
        note = ""
        if t["mode"] == "gta_cam" and not box:
            note = " (no webcam found: widescreen layout instead)"  # render.py makes the same choice
        upd = {"mode": "fit", "facecam_box": Box.model_validate(box) if box else None, "gameplay_focus_x": 0.5}
        new = edl.model_copy(update={"layout": lay.model_copy(update=upd), "zooms": [], "overlays": [], "sfx": [], "transitions": [],
                                     "music": edl.music.model_copy(update={"enabled": False}),
                                     "captions": edl.captions.model_copy(update={"enabled": True})})
        return new, f"layout set by template: {t['label']}{note}"
    if t["mode"] == "full":
        upd = {"mode": "full", "facecam_box": None}
        zooms = [z.model_copy(update={"target": "frame"}) if z.target == "facecam" else z for z in edl.zooms]
        caps = edl.captions.model_copy(update={"enabled": True})
        new = edl.model_copy(update={"layout": lay.model_copy(update=upd), "zooms": zooms, "captions": caps})
        return new, None if lay.mode == "full" else f"layout '{lay.mode}' changed to 'full' (template: {t['label']})"
    box = _detected_box(job_id, c.get("idx")) or (lay.facecam_box.model_dump() if lay.facecam_box else None)
    if not box:
        return (edl.model_copy(update={"layout": lay.model_copy(update={"mode": "full", "facecam_box": None})}),
                f"template {t['label']}: no webcam found in this clip, so it is full screen instead")
    upd = {"mode": "split", "facecam_share": t["share"], "facecam_box": Box.model_validate(box)}
    return edl.model_copy(update={"layout": lay.model_copy(update=upd)}), f"layout set by template: {t['label']}"
