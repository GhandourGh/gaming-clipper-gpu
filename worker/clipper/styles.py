"""Editing styles: guidance for the Creative Director + hard limits enforced on the EDL.
Limits are per clip, scaled by kept duration where it makes sense."""
STYLES = {
    "viral": {
        "label": "Viral Gaming",
        "guide": "High energy but purposeful. Big pop captions with 2-5 emphasis words. Punch-in zooms on the payoff and the biggest reaction. "
                 "1-2 emoji/text overlays that mirror the emotion. SFX on impacts only. A shake or zoom_punch on the single biggest beat.",
        "limits": {"zoom_per_s": 1 / 9, "sfx_per_s": 1 / 7, "overlays": 3, "transitions": 2, "ramps_max": 1},
        "captions": "pop",
    },
    "funny": {
        "label": "Funny Moments",
        "guide": "Comedy timing first: leave a short beat (0.3-0.6s) after the punchline instead of cutting immediately. Zoom on the face/reaction, not the gameplay, when the joke is the reaction. "
                 "Emoji overlays (😂💀🤡) only right after the joke lands. 'pop' or 'ding' SFX for comedic beats; no 'riser'. Karaoke-free pop captions with funny words emphasised.",
        "limits": {"zoom_per_s": 1 / 9, "sfx_per_s": 1 / 6, "overlays": 3, "transitions": 2, "ramps_max": 1},
        "captions": "pop",
    },
    "cinematic": {
        "label": "Cinematic",
        "guide": "Let the gameplay breathe. Few, slow 'smooth' zooms (scale <= 1.25) that build tension; no emoji, no text overlays except an optional single short title. "
                 "At most one 'riser' into the payoff and one 'boom' on it. Karaoke captions in the lower position. Prefer 'fit' layout so the full scene is visible. Avoid flash/shake.",
        "limits": {"zoom_per_s": 1 / 10, "sfx_per_s": 1 / 12, "overlays": 1, "transitions": 1, "no_emoji": True, "max_zoom_scale": 1.3, "ramps_max": 1},
        "captions": "karaoke",
    },
    "fast": {
        "label": "Fast-Paced",
        "guide": "Tight pacing: cut every pause > 0.4s and any setup that does not serve the payoff; kept duration usually 15-35s. Quick punch zooms on action beats, whoosh on cuts that jump in time. "
                 "Pop captions. Still no effect without a reason.",
        "limits": {"zoom_per_s": 1 / 6, "sfx_per_s": 1 / 5, "overlays": 2, "transitions": 3, "ramps_max": 2},
        "captions": "pop",
    },
    "minimal": {
        "label": "Minimal",
        "guide": "Clean edit: only cut dead air and keep captions. At most 2 zooms where the viewer would otherwise miss something, at most 1 SFX, no overlays, no transitions. Lower caption position.",
        "limits": {"zoom_max": 2, "sfx_max": 1, "overlays": 0, "transitions": 0},
        "captions": "pop",
    },
}
DEFAULT = "viral"

MOMENT_STRATEGY = """## Strategy by moment type (pick the one that fits and set moment_type)
- funny: setup -> punchline -> reaction. Keep the reaction; the laugh/face is the payoff. Zoom on the reactor at the punchline.
- intense: clutch/fight/chase. Keep momentum, cut downtime, punch in on the decisive action, impact SFX on hits/kills.
- surprise: jump scare / unexpected event. Keep the calm before (it sells the surprise), then hit hard exactly on the reveal: zoom + boom/shake on that frame.
- fail: show the plan, then the fail, then the reaction. Comedic beat after the fail; 'pop'/'ding' rather than 'boom'.
- story: a mini narrative (plan -> complication -> outcome). Fewer effects, captions carry it, one zoom on the outcome."""


def style_of(name: str | None) -> str:
    return name if name in STYLES else DEFAULT


# What separates a professional edit from an over-edited one: every effect is a decision, and doing nothing is a decision.
DOCTRINE = """## Editing doctrine (follow it over any urge to add effects)
- A perfectly timed plain cut beats an effect. If a beat already lands, leave the footage alone.
- One hero moment per clip gets the biggest treatment (zoom + sound); everything else is quiet. Never two effects on the same beat unless it IS the hero moment.
- A zoom must answer "what should the viewer look at right now?" (a reaction, a reveal, a punchline). Zooms are never decoration, never back to back, and never longer than the beat they emphasise.
- A sound effect must match something visible at that exact frame (an impact, a cut, a text pop). No sound without a visual cause; leave silence around the payoff.
- Cut on the reaction, not on the setup: keep the 0.3-0.6 s after a punchline, drop every pause that carries no information, never end before the payoff has landed.
- The first 3 seconds carry the hook: start on the most intriguing frame, not on throat-clearing.
- Slow motion (`ramps`) is the strongest effect: use it at most once, on the hero moment (the hit, the jump, the reveal), 0.5x for 1-2 s, never over speech and never together with a zoom on the same beat. Leave it out if no moment deserves it.
- On-screen text and emoji only when the footage does not already say it."""
for st in STYLES.values():
    st["guide"] = st["guide"] + "\n\n" + DOCTRINE
