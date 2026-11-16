"""Edit Decision List: the contract between the AI Creative Director and the renderer.
All times are absolute seconds in the SOURCE video. The renderer maps them to
output time after cuts. Every creative element must carry a `reason`."""
import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SFX = Literal["whoosh", "boom", "ding", "pop", "riser", "bass_drop",  # original synthesised sounds
              "punch", "hit_metal", "thud", "glass_break", "bell", "click", "confirm", "error", "tick",  # CC0 library
              "record_scratch", "glitch", "power_up", "laser", "zap", "level_up"]
Reason = Field(min_length=8, max_length=200, description="Why this element helps the viewer at this moment")


class Box(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class Segment(BaseModel):
    start: float
    end: float
    role: Literal["main", "cold_open"] = Field("main", description=(
        "cold_open = a 1-3.5 s teaser of the payoff shown FIRST, before going back to the setup; it may repeat "
        "footage used again later. main = the story itself; main segments play in list order (reordering allowed) "
        "and must not overlap each other."))
    reason: str = Reason
    speed: float = Field(1.0, ge=0.35, le=1.0, description="playback speed; set only by the code from `ramps` (slow motion)")

    @model_validator(mode="after")
    def _order(self):
        if self.end - self.start < 0.4:
            raise ValueError("segment shorter than 0.4 s")
        return self

    @property
    def out_len(self) -> float:
        """Seconds this segment lasts in the finished clip (a slowed segment lasts longer than its source range)."""
        return (self.end - self.start) / self.speed


class Ramp(BaseModel):
    """A short slow-motion beat on the hero moment. Source times; the code splits the segment around it."""
    start: float
    end: float
    speed: float = Field(0.5, ge=0.35, le=0.8, description="0.5 = half speed")
    reason: str = Reason


class Layout(BaseModel):
    mode: Literal["split", "bubble", "full", "fit"] = Field(description="split = facecam on top (~20-25% of the height) + gameplay below (~75-80%); bubble = full-bleed gameplay with the streamer's webcam in a small corner bubble (always visible, never the centre of attention; a facecam zoom makes the bubble grow for the reaction, then shrink back); full = one full-bleed 9:16 crop of the frame (shows ~1/3 of a 16:9 frame width); fit = wider gameplay crop centred over a blurred background (keeps HUD/context readable)")
    facecam_box: Box | None = Field(None, description="Facecam rectangle in the source frame (normalised). Required for split and bubble.")
    bubble_corner: Literal["top_left", "top_right", "mid_left", "mid_right"] = Field("top_right", description="bubble mode: where the webcam bubble sits. Pick the side that does not cover the action/HUD that matters.")
    bubble_shape: Literal["circle", "rounded"] = Field("circle", description="bubble mode: circle for a face close-up, rounded (rounded rectangle) when the cam shows more than the face")
    bubble_size: float = Field(0.34, ge=0.25, le=0.45, description="bubble mode: bubble width as a fraction of the screen width")
    gameplay_focus_x: float = Field(0.5, ge=0, le=1, description="Horizontal centre of the gameplay crop")
    facecam_share: float = Field(0.22, ge=0.15, le=0.5, description="Fraction of the output height used by the facecam in split mode")

    @model_validator(mode="after")
    def _split_needs_box(self):
        if self.mode in ("split", "bubble") and self.facecam_box is None:
            raise ValueError(f"layout.mode={self.mode} requires facecam_box")
        return self


class Zoom(BaseModel):
    start: float
    end: float
    target: Literal["gameplay", "facecam", "frame"] = Field(description="facecam (split/bubble only): in bubble mode the bubble grows for the reaction")
    scale: float = Field(ge=1.05, le=1.8)
    focus_x: float = Field(0.5, ge=0, le=1)
    focus_y: float = Field(0.5, ge=0, le=1)
    style: Literal["punch", "smooth"] = "punch"
    reason: str = Reason


class Captions(BaseModel):
    enabled: bool = True
    style: Literal["pop", "karaoke"] = "pop"
    position: Literal["middle", "lower"] = "middle"
    emphasis: list[str] = Field(default_factory=list, max_length=15, description="Words to highlight in the accent colour")


class Overlay(BaseModel):
    kind: Literal["emoji", "text", "image"]
    start: float
    end: float
    value: str = Field(min_length=1, max_length=80, description="emoji character, short text, or an image search query")
    position: Literal["top", "center", "upper_left", "upper_right"] = "upper_right"
    animation: Literal["pop", "slide_up", "shake"] = "pop"
    asset_ref: str | None = Field(None, description="Filled by the asset stage, leave null")
    reason: str = Reason


class Sfx(BaseModel):
    at: float
    sound: SFX
    gain_db: float = Field(-8, ge=-24, le=0)
    reason: str = Reason


class Transition(BaseModel):
    at: float
    kind: Literal["flash", "zoom_punch", "shake"]
    reason: str = Reason


class Music(BaseModel):
    enabled: bool = False
    mood: str | None = None
    asset_ref: str | None = None
    gain_db: float = Field(-22, ge=-40, le=-10)


class Hook(BaseModel):
    text: str = Field(min_length=3, max_length=60)
    duration: float = Field(2.5, ge=1.5, le=4)


class CaptionFix(BaseModel):
    wrong: str = Field(min_length=1, max_length=60, description="misheard word(s) exactly as in the transcript")
    right: str = Field(min_length=1, max_length=60, description="what was actually said")


class EDL(BaseModel):
    version: Literal[1] = 1
    style: Literal["viral", "funny", "cinematic", "fast", "minimal"] = "viral"
    moment_type: Literal["funny", "intense", "surprise", "fail", "story"] = Field(
        "story", description="What kind of moment this is; drives the editing strategy")
    title: str = Field(min_length=3, max_length=90)
    description: str = Field(min_length=3, max_length=300)
    tiktok_caption: str = Field("", max_length=300,
                                 description="Recommended TikTok caption, without hashtags or emoji")
    hashtags: list[str] = Field(default_factory=list, min_length=0, max_length=5,
                                description="Exactly five relevant TikTok hashtags when supplied")
    hook: Hook
    segments: list[Segment] = Field(min_length=1, max_length=25)
    layout: Layout
    zooms: list[Zoom] = Field(default_factory=list, max_length=20)
    ramps: list[Ramp] = Field(default_factory=list, max_length=3, description=(
        "optional slow-motion beat (0.5x for 0.8-2.5 s) on the single hero moment, e.g. the hit or the jump; "
        "never over dialogue, at most one"))
    captions: Captions = Field(default_factory=Captions)
    overlays: list[Overlay] = Field(default_factory=list, max_length=12)
    sfx: list[Sfx] = Field(default_factory=list, max_length=15)
    transitions: list[Transition] = Field(default_factory=list, max_length=10)
    music: Music = Field(default_factory=Music)
    caption_fixes: list[CaptionFix] = Field(default_factory=list, max_length=12,
                                            description="corrections for words the speech recognition misheard")


def social_copy(edl: dict) -> tuple[str, list[str]]:
    """Return publish-ready copy for old and new clips. New plans provide five tags; legacy clips get safe defaults."""
    caption = (edl.get("tiktok_caption") or edl.get("description") or edl.get("title") or "").strip()
    tags, seen = [], set()
    for raw in edl.get("hashtags") or []:
        tag = "#" + re.sub(r"[^A-Za-z0-9_]", "", str(raw).lstrip("#"))
        if len(tag) > 1 and tag.lower() not in seen:
            tags.append(tag)
            seen.add(tag.lower())
    for tag in ("#gaming", "#gamingclips", "#streamer", "#viral", "#tiktokgaming"):
        if len(tags) == 5:
            break
        if tag.lower() not in seen:
            tags.append(tag)
            seen.add(tag.lower())
    return caption, tags[:5]


def kept_duration(edl: EDL) -> float:
    return sum(s.out_len for s in edl.segments)


def to_output_time(segments: list[Segment], t: float) -> float | None:
    """Map a source time to output time (its occurrence in the main story); None if t was cut out.
    Segments play in list order; a cold-open teaser only shifts the timeline."""
    acc = 0.0
    for s in segments:
        if s.role == "main" and s.start <= t <= s.end:
            return acc + (t - s.start) / s.speed
        acc += s.out_len
    return None


def apply_ramps(edl: EDL, cap: int) -> tuple[EDL, list[str]]:
    """Split main segments around each slow-motion ramp (idempotent: the ramps list is emptied). A ramp must lie inside one
    main segment, be 0.8-2.5 s long and not touch a segment edge by less than 0.3 s; the rest are dropped with a note."""
    notes, segs = [], list(edl.segments)
    for r in sorted(edl.ramps, key=lambda r: r.start)[:max(cap, 0)]:
        host = next((i for i, s in enumerate(segs) if s.role == "main" and s.speed == 1.0 and s.start + 0.3 <= r.start and r.end <= s.end - 0.3), None)
        if host is None or not 0.8 <= r.end - r.start <= 2.5:
            notes.append(f"slow-motion at {r.start:.2f}s dropped (must sit 0.3 s inside one main segment and last 0.8-2.5 s)")
            continue
        s = segs[host]
        parts = [s.model_copy(update={"end": r.start}),
                 s.model_copy(update={"start": r.start, "end": r.end, "speed": r.speed, "reason": r.reason[:200] if len(r.reason) >= 8 else s.reason}),
                 s.model_copy(update={"start": r.end})]
        segs[host:host + 1] = parts
    if len(edl.ramps) > max(cap, 0) and cap >= 0:
        notes.append(f"{len(edl.ramps) - max(cap, 0)} slow-motion beat(s) dropped (limit {max(cap, 0)})")
    return edl.model_copy(update={"segments": segs, "ramps": []}), notes


SNAP_SLACK = 2.5  # word/sentence snapping may move a cut this far past a story range's edge


def fit_story_ranges(edl: EDL, allowed_ranges) -> tuple[EDL, list[str]]:
    """Repair instead of reject: trim a segment into the story range it overlaps (>= 0.8 s), drop one with no overlap.
    Runs BEFORE word snapping, so the snapped cuts still land in word gaps."""
    if not allowed_ranges:
        return edl, []
    notes, fixed = [], []
    for s in edl.segments:
        if any(s.start >= a - SNAP_SLACK and s.end <= b + SNAP_SLACK for a, b in allowed_ranges):
            fixed.append(s)
            continue
        best = max(allowed_ranges, key=lambda r: min(s.end, r[1]) - max(s.start, r[0]))
        ov_start, ov_end = max(s.start, best[0]), min(s.end, best[1])
        if ov_end - ov_start >= 0.8:
            notes.append(f"segment {s.start:.2f}-{s.end:.2f} trimmed to {ov_start:.2f}-{ov_end:.2f} (inside the story range)")
            fixed.append(s.model_copy(update={"start": round(ov_start, 3), "end": round(ov_end, 3)}))
        else:
            notes.append(f"segment {s.start:.2f}-{s.end:.2f} dropped (outside the evidence-backed story ranges)")
    if not any(x.role == "main" for x in fixed):
        return edl, []  # nothing usable left: let check() report it
    return edl.model_copy(update={"segments": fixed}), notes


def check(edl: EDL, win_start: float, win_end: float, length: tuple[float, float] | None = None,
          allowed_ranges: list[tuple[float, float]] | None = None, strict_hook: bool = False,
          game: str | None = None, job_meta: dict | None = None) -> tuple[list[str], list[str], EDL]:
    """Semantic checks beyond the schema. Returns (errors, warnings, cleaned_edl).
    Errors need a repair round; warnings are auto-fixed (items dropped/clamped).
    Segment ORDER is the edit (reordering and a cold open are allowed), so it is kept as given."""
    errors, warnings = [], []
    if edl.ramps:
        from .modes import effective_limits as _eff
        from .styles import STYLES as _ST
        edl, ramp_notes = apply_ramps(edl, int(_eff(job_meta, _ST[edl.style]["limits"]).get("ramps_max", 0)))
        warnings += ramp_notes
    segs = list(edl.segments)
    colds = [s for s in segs if s.role == "cold_open"]
    mains = sorted((s for s in segs if s.role == "main"), key=lambda s: s.start)
    if not mains:
        errors.append("at least one main segment is required")
    if len(colds) > 1:
        errors.append("use at most one cold_open segment")
    if colds and segs[0].role != "cold_open":
        errors.append("the cold_open segment must be the FIRST segment")
    for c in colds:
        if 3.5 < c.end - c.start <= 8.0:  # a slightly long teaser is trimmed, not a reason to throw the whole plan away
            warnings.append(f"cold_open trimmed from {c.end - c.start:.1f}s to 3.5s")
            c.end = round(c.start + 3.5, 3)
        if not 0.8 <= c.end - c.start <= 3.5:
            errors.append(f"cold_open must be 0.8-3.5 s long (is {c.end - c.start:.1f}s)")
    for a, b in zip(mains, mains[1:]):
        if b.start < a.end - 0.01:
            errors.append(f"main segments overlap at {b.start:.2f}s (each moment may appear once; only the cold_open may repeat footage)")
    if min(s.start for s in segs) < win_start - 1.0 or max(s.end for s in segs) > win_end + 1.0:
        errors.append(f"segments must stay inside the clip window {win_start:.2f}-{win_end:.2f}s")
    if allowed_ranges:
        last_main = max((s for s in segs if s.role == "main"), key=lambda s: s.end, default=None)
        for s in segs:
            tail = 8.5 if s is last_main else SNAP_SLACK  # the last line may run on until its sentence ends
            if not any(s.start >= a - SNAP_SLACK and s.end <= b + tail for a, b in allowed_ranges):
                errors.append(f"segment {s.start:.2f}-{s.end:.2f} is outside the evidence-backed story ranges")
    total = sum(s.out_len for s in segs)
    lo, hi = length if length else (15, 92)
    # Cut snapping and complete-sentence endings can move a good story slightly outside the requested preset.
    # Never reject an otherwise complete minute-long story over a few frames/words.
    if not lo - 2.5 <= total <= hi + 2.5:
        errors.append(f"total duration is {total:.1f}s; it must be {lo:.0f}-{hi:.0f}s (sum of all segments incl. cold_open)")

    def inside(t):
        return to_output_time(segs, t) is not None

    def keep(items, name, tfn):
        out = []
        for it in items:
            if inside(tfn(it)):
                out.append(it)
            else:
                warnings.append(f"dropped {name} at {tfn(it):.2f}s (falls in a cut)")
        return out

    zooms = keep(edl.zooms, "zoom", lambda z: z.start)
    overlays = keep(edl.overlays, "overlay", lambda o: o.start)
    sfx = keep(edl.sfx, "sfx", lambda s: s.at)
    trans = keep(edl.transitions, "transition", lambda t: t.at)
    for z in zooms:
        if z.end <= z.start:
            errors.append(f"zoom at {z.start:.2f}s has end <= start")
    zooms.sort(key=lambda z: z.start)
    for a, b in zip(zooms, zooms[1:]):
        if b.start < a.end:
            warnings.append(f"zooms overlap at {b.start:.2f}s; second one dropped")
    zooms = [z for i, z in enumerate(zooms) if i == 0 or z.start >= zooms[i - 1].end]
    # purpose over quantity: cap effect density per editing style
    from .styles import STYLES
    from .modes import effective_limits
    lim = effective_limits(job_meta, STYLES[edl.style]["limits"])
    zmax = lim.get("zoom_max", max(1, int(total * lim.get("zoom_per_s", 0.25)) + 1))
    smax = lim.get("sfx_max", max(1, int(total * lim.get("sfx_per_s", 1 / 3)) + 1))
    # Restraint: a small overshoot is trimmed here (keeping the strongest, most purposeful items) instead of paying for
    # another AI round; a large overshoot means the plan is over-edited and goes back.
    def trim(items, cap, name, key):
        if cap <= 0:  # switched off by the owner: drop silently, no repair round
            if items:
                warnings.append(f"{len(items)} {name} dropped (turned off)")
            return []
        if len(items) > cap + 2:
            errors.append(f"too many {name} ({len(items)}) for a {total:.0f}s '{edl.style}' clip (max {cap}); keep only the most purposeful")
            return items
        if len(items) > cap:
            keep = sorted(items, key=key)[:cap]
            warnings.append(f"trimmed {len(items) - cap} {name} to the limit ({cap})")
            return [i for i in items if i in keep]
        return items

    zooms = trim(zooms, zmax, "zooms", lambda z: (-(z.scale), z.start))
    sfx = trim(sfx, smax, "sound effects", lambda s: (s.gain_db * -1, s.at))
    # no zoom stacked on another: a breath of at least 1.5 s between them; no zoom scale beyond 1.45 (more reads as cheap)
    spaced = []
    for z in sorted(zooms, key=lambda z: z.start):
        if spaced and z.start < spaced[-1].end + 1.5:
            warnings.append(f"zoom at {z.start:.2f}s dropped (too close to the previous zoom)")
            continue
        cap_s = lim.get("zoom_scale_cap", 1.45)
        if z.scale > cap_s:
            warnings.append(f"zoom at {z.start:.2f}s scale {z.scale:.2f} reduced to {cap_s}")
            z = z.model_copy(update={"scale": cap_s})
        spaced.append(z)
    zooms = spaced
    gapped = []
    for x in sorted(sfx, key=lambda x: x.at):
        if gapped and x.at < gapped[-1].at + 0.6:
            warnings.append(f"sound effect at {x.at:.2f}s dropped (stacked on the previous one)")
            continue
        gapped.append(x)
    sfx = gapped
    if len(overlays) > lim["overlays"]:
        errors.append(f"too many overlays ({len(overlays)}) for style '{edl.style}' (max {lim['overlays']})")
    if len(trans) > lim["transitions"]:
        errors.append(f"too many transitions ({len(trans)}) for style '{edl.style}' (max {lim['transitions']})")
    if lim.get("no_emoji") and any(o.kind == "emoji" for o in overlays):
        errors.append(f"style '{edl.style}' does not use emoji overlays")
    if lim.get("max_zoom_scale") and any(z.scale > lim["max_zoom_scale"] for z in zooms):
        errors.append(f"style '{edl.style}' keeps zoom scale <= {lim['max_zoom_scale']}")
    if edl.layout.mode == "split" and any(z.target == "frame" for z in zooms):
        warnings.append("zoom target 'frame' treated as 'gameplay' in split layout")
    # hook / title rules: broken rules send the plan back only while strict (the first attempts); after that they are
    # reported as warnings so a clip is never lost over wording
    from . import hookrules
    h_fail, h_notes = hookrules.check_hook(edl.hook.text)
    t_fail, t_notes = hookrules.check_title(edl.title, topic=hookrules.topic_pattern(game))
    rule_msgs = [f"hook \"{edl.hook.text}\": {m}" for m in h_fail] + [f"title: {m}" for m in t_fail]
    (errors if strict_hook else warnings).extend(rule_msgs)
    warnings.extend(f"hook: {m}" for m in h_notes + t_notes)
    return errors, warnings, edl.model_copy(update={"zooms": zooms, "overlays": overlays, "sfx": sfx, "transitions": trans})
