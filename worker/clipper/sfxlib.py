"""Sound effects: 6 original synthesised sounds + a categorised CC0 library (Kenney, renderer/public/sfx/kenney)."""
import hashlib
import json
from functools import lru_cache

from . import config

BASE = ("whoosh", "boom", "ding", "pop", "riser", "bass_drop")
LIB_DIR = config.RENDERER / "public" / "sfx" / "kenney"
# what each library sound is for (shown to the AI editor)
USES = {
    "punch": "a hit, a takedown, a slap",
    "hit_metal": "a car crash, metal impact, gun clank",
    "thud": "a fall, a body drop, something heavy landing",
    "glass_break": "glass shattering, a window, a total fail",
    "bell": "a round end, a 'ding ding' moment, irony",
    "click": "a menu, a selection, a quick UI beat",
    "confirm": "a success, mission complete, a good choice",
    "error": "a mistake, a fail, 'wrong answer'",
    "tick": "a countdown, tension, waiting",
    "record_scratch": "a sudden stop, 'wait, what?', a freeze-frame",
    "glitch": "a weird bug, a jump cut, a digital glitch",
    "power_up": "a buff, a new item, getting stronger",
    "laser": "sci-fi shots, a fast move",
    "zap": "an electric moment, a quick transition",
    "level_up": "a win, an achievement, a level up",
}


@lru_cache(maxsize=1)
def _index() -> dict:
    try:
        return json.loads((LIB_DIR / "index.json").read_text())
    except (OSError, ValueError):
        return {}


def names() -> tuple[str, ...]:
    return BASE + tuple(k for k in USES if _index().get(k))


def file_for(sound: str, at: float) -> str:
    """Public-dir relative file for a sound; library sounds pick a stable variant per moment (no repeats)."""
    if sound in BASE:
        return f"sfx/{sound}.wav"
    variants = _index().get(sound) or []
    if not variants:
        return "sfx/pop.wav"
    k = int(hashlib.md5(f"{sound}:{at:.2f}".encode()).hexdigest(), 16) % len(variants)
    return f"sfx/kenney/{variants[k]}"


def credit(file: str) -> dict:
    if file.startswith("sfx/kenney/"):
        return {"source": "Kenney sound packs (kenney.nl)", "license": "CC0 1.0", "author": "Kenney"}
    return {"source": "synthesised by this project (ffmpeg)", "license": "original work", "author": "gaming-clipper"}


def prompt_list() -> str:
    lib = ", ".join(f"{k} ({v})" for k, v in USES.items() if _index().get(k))
    return ("whoosh (movement/cut), boom (impact), ding (success), pop (overlay appears), riser (tension before payoff), "
            "bass_drop (the payoff)" + (f", {lib}" if lib else ""))
