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
}


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
        return ("\n## Layout fixed by the owner: GTA 6 GAMEPLAY FOCUS\nUse full-bleed gameplay with a detected webcam in a rounded upper-left card. "
                "Captions, hook text, overlays, zooms, sound effects, transitions and music are disabled. If no webcam exists, use gameplay only.\n")
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
        crop = options.get("gameplay_crop", "auto")
        focus = {"left": 0.22, "center": 0.5, "right": 0.78}.get(crop, lay.gameplay_focus_x)
        size = {"small": 0.38, "medium": 0.42, "large": 0.46}.get(options.get("facecam_size"), 0.42)
        mode = "fit" if crop == "fit" else ("bubble" if box else "full")
        upd = {"mode": mode, "facecam_box": Box.model_validate(box) if box else None, "bubble_corner": options.get("facecam_position", "top_left"),
               "bubble_shape": "rounded", "bubble_size": size, "gameplay_focus_x": focus}
        new = edl.model_copy(update={"layout": lay.model_copy(update=upd), "zooms": [], "overlays": [], "sfx": [], "transitions": [],
                                     "music": edl.music.model_copy(update={"enabled": False}),
                                     "captions": edl.captions.model_copy(update={"enabled": False})})
        return new, f"layout set by template: {t['label']}" + (" (no webcam found; gameplay-only)" if not box else "")
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
