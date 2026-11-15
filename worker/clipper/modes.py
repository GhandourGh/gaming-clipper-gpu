"""Clip modes and the owner's per-job options.

One job = one MODE (what kind of edit this is) + a few simple options. This module turns them into
  - rules for the story picker (which parts of the video become clips),
  - rules for the Creative Director (how each clip is edited),
  - hard limits on zooms / sound effects, the silence-cutting threshold and the style.
Everything is stored in the job's `meta`, so regenerating a clip keeps the same intent.

  viral        the funniest / most entertaining moments, dynamic editing (the original behaviour)
  continuous   keep the natural flow (story-mode gameplay): cut only silence, loading screens, awkward pauses
  highlights   several related moments (same fight, same joke, same objective) in one coherent clip
  custom       the owner's own instructions on top of the chosen options
"""
MODES = {
    "viral": "Viral / Funny moments",
    "continuous": "Continuous gameplay",
    "highlights": "Highlights",
    "custom": "Custom",
}
CONTENT = ("auto", "gameplay", "podcast", "stream", "reaction", "other")
EDIT = ("minimal", "balanced", "dynamic")
LEVEL = ("off", "low", "medium", "high")
CAPTION_LOOK = ("pop", "karaoke", "clean")

# seconds of silence that count as dead air (low sensitivity = only long silences are cut)
SILENCE_GAP = {"off": None, "low": 4.5, "medium": 3.0, "high": 1.8}
FX_SCALE = {"off": 0.0, "low": 0.5, "medium": 1.0, "high": 1.6}
ZOOM_CAP = {"low": 1.15, "medium": 1.3, "high": 1.45}

# each mode starts from sensible defaults; anything the owner sets explicitly wins
DEFAULTS = {
    "viral":      {"edit": "balanced", "silence": "medium", "zoom": "medium", "sfx": "medium"},
    "continuous": {"edit": "minimal",  "silence": "low",    "zoom": "low",    "sfx": "off"},
    "highlights": {"edit": "balanced", "silence": "medium", "zoom": "low",    "sfx": "low"},
    "custom":     {"edit": "balanced", "silence": "medium", "zoom": "medium", "sfx": "medium"},
}


def _pick(v, allowed, default):
    v = str(v).lower() if v is not None else ""
    return v if v in allowed else default


def normalize(o: dict | None) -> dict:
    """Validated option dict (only known keys, only known values). Missing values come from the mode's defaults."""
    o = o or {}
    mode = _pick(o.get("mode"), MODES, "viral")
    d = DEFAULTS[mode]
    out = {
        "mode": mode,
        "content_type": _pick(o.get("content_type"), CONTENT, "auto"),
        "edit": _pick(o.get("edit"), EDIT, d["edit"]),
        "silence": _pick(o.get("silence"), LEVEL, d["silence"]),
        "zoom": _pick(o.get("zoom"), LEVEL, d["zoom"]),
        "sfx": _pick(o.get("sfx"), LEVEL, d["sfx"]),
        "caption_look": _pick(o.get("caption_look"), CAPTION_LOOK, "karaoke" if mode == "continuous" else "pop"),
    }
    text = " ".join(str(o.get("instructions") or "").split())[:600]
    if text:
        out["instructions"] = text
    return out


def style_for(mode: str, edit: str) -> str:
    """The existing editing style (look + limits) that fits the mode and how much editing the owner wants."""
    if edit == "minimal":
        return "minimal"
    if mode == "continuous":
        return "cinematic" if edit == "balanced" else "viral"
    return "viral" if edit == "balanced" else "fast"


def effective_limits(meta: dict | None, lim: dict) -> dict:
    """The style's hard limits, scaled by the owner's zoom / sound-effect intensity."""
    meta = meta or {}
    out = dict(lim)
    for key, level in (("zoom", meta.get("zoom")), ("sfx", meta.get("sfx"))):
        if level not in FX_SCALE:
            continue
        k = FX_SCALE[level]
        if f"{key}_max" in out:
            out[f"{key}_max"] = int(round(out[f"{key}_max"] * k))
        elif f"{key}_per_s" in out:
            out[f"{key}_per_s"] = out[f"{key}_per_s"] * k
        if k == 0:
            out[f"{key}_max"] = 0
    if meta.get("zoom") in ZOOM_CAP:
        out["zoom_scale_cap"] = ZOOM_CAP[meta["zoom"]]
    if meta.get("zoom") == "off":
        out["ramps_max"] = 0
    if meta.get("mode") == "continuous":
        out["overlays"], out["transitions"], out["ramps_max"] = 0, 0, 0  # a continuous edit has no montage furniture
    return out


def silence_gap(meta: dict | None) -> float | None | bool:
    """Seconds of silence to cut; None = silence cutting is off; False = the owner did not choose (use Settings)."""
    s = (meta or {}).get("silence")
    return SILENCE_GAP[s] if s in SILENCE_GAP else False


def _length_text(meta: dict) -> str:
    ln = meta.get("length")
    return f"{ln[0]}-{ln[1]} seconds" if ln else ""


def story_rule(meta: dict | None) -> str:
    """Appended to the story-picking prompt: what a good clip is for this mode."""
    meta = meta or {}
    mode = meta.get("mode") or "viral"
    ln = _length_text(meta)
    length = (f"\nEach story's total duration MUST be {ln} (this overrides the usual 20-90 s guidance; "
              "never pad: pick a scene that is naturally that long).\n") if ln else ""
    custom = f"\nOwner's instructions (follow them): {meta['instructions']}\n" if meta.get("instructions") else ""
    launch_mix = ("\n## GTA VI LAUNCH TEST\nBuild a balanced shortlist with: quick 20-35 second reactions, chaos or "
                  "discoveries; complete 60-80 second mini-stories; and, only when the story genuinely needs the context, "
                  "80-120 second extended stories. Compress longer source events with chronological cuts that remove menus, "
                  "loading, silence, repetitive travel and failed attempts while preserving essential dialogue, action, payoff "
                  "and the full reaction. A story may jump forward in source time when the connection is obvious. If one story "
                  "is too large, propose Part 1 and Part 2 as separate candidates ONLY when Part 1 ends on a real development or "
                  "cliffhanger and Part 2 has its own setup and payoff; label their titles clearly. Never split at an arbitrary "
                  "timestamp. If a moment cannot support a satisfying story, return it as a short clip instead of padding it.\n"
                  if meta.get("clip_strategy") == "gta_launch_mix" else "")
    if mode == "continuous":
        body = ("\n## Mode: CONTINUOUS GAMEPLAY\nViewers want to follow the story, not see random highlights. Each story is ONE "
                "continuous stretch of the video (a scene, a mission beat, a conversation, a chase from start to finish): "
                "give it a SINGLE beat, or at most two beats that are directly adjacent (gap under 20 s). Start where the "
                "scene naturally starts and end where it naturally resolves or pauses. NEVER combine distant parts, never "
                "build a montage. Prefer story, dialogue and cutscenes that make sense on their own; avoid menus, loading "
                "screens, map screens, long walking or driving with nothing happening. Stories must not overlap. Score "
                "continuity and how well the scene stands on its own.\n")
    elif mode == "highlights":
        body = ("\n## Mode: HIGHLIGHTS\nEach story combines RELATED moments into one coherent clip: the same fight, the same "
                "running joke, the same objective or character, in chronological order, even when they are minutes apart. "
                "`connection` must say what ties them together. Never mix unrelated scenes just to fill the time.\n")
    elif mode == "custom":
        body = "\n## Mode: CUSTOM\nChoose the stories that best fit the owner's instructions below.\n"
    else:
        body = ("\n## Mode: VIRAL / FUNNY MOMENTS\nFind the funniest, most surprising or most intense moments. Quick, punchy, "
                "with a clear setup and payoff.\n")
    return body + launch_mix + length + custom


def director_rule(meta: dict | None) -> str:
    """Appended to the Creative Director prompt: how to edit this clip."""
    meta = meta or {}
    mode = meta.get("mode") or "viral"
    custom = f"\n- Owner's instructions (follow them): {meta['instructions']}" if meta.get("instructions") else ""
    look = {"pop": "pop", "karaoke": "karaoke", "clean": "karaoke"}.get(meta.get("caption_look") or "")
    cap = f"\n- captions.style must be \"{look}\"." if look else ""
    if mode == "continuous":
        return ("\n## Mode: CONTINUOUS GAMEPLAY (overrides the urge to make this punchy)\n"
                "- Preserve the natural flow: keep the clip as ONE continuous stretch of source footage in original order. "
                "No cold_open, no reordering, no montage, no speed changes.\n"
                "- Cut only what hurts the viewer: silence with nothing happening, loading or menu screens, an awkward pause "
                "or dead walk. Every cut must land between sentences or actions, never mid-word, never inside dialogue, never "
                "through a reaction or a cutscene. When in doubt do not cut.\n"
                "- Never remove dialogue, story or a reaction just to shorten the clip. The clip may be as long as the scene "
                "needs (up to the allowed length).\n"
                "- Effects are optional and rare: no overlays, no emoji, no transitions, at most the zooms/sounds allowed by the "
                "limits and only when they help the viewer see something. The hook is a short plain title, not a gimmick.\n"
                "- Prefer layout 'full' or 'fit' so the gameplay stays visible and uncropped." + cap + custom + "\n")
    if mode == "highlights":
        return ("\n## Mode: HIGHLIGHTS\n- The segments are related moments of the same thing; keep them in chronological order and "
                "make each transition feel motivated (a hard cut on the action is enough).\n"
                "- Keep every segment complete: never cut dialogue or a payoff in half. Do not add unrelated footage.\n"
                "- Moderate editing: effects only on the strongest beat." + cap + custom + "\n")
    if mode == "custom":
        return "\n## Mode: CUSTOM\n- Follow the owner's instructions first, then the limits." + cap + custom + "\n"
    return ("\n## Mode: VIRAL / FUNNY MOMENTS\n- Dynamic, entertaining edit: smart cuts, purposeful zooms on the payoff, sound "
            "effects on visible impacts, strong hook." + cap + custom + "\n")
