"""Hook / title rules and the clip-type playbook.

Written from the published findings of the GTA Shorts study (hook structures that appeared on 500k+ view Shorts and how
each structure moved views against the channel's own average). The numbers are the study's, quoted in the messages so the
director knows why a rule exists. Nothing here is copied code: it is our own implementation of those rules.

Two uses:
  * `check_hook` / `check_title`  -> (fails, notes). `edl.check` turns fails into repair errors for the first attempts.
  * `prompt_rules` / `playbook_text` -> text appended to the director prompt.
"""
import re

# name, pattern, example, measured views vs channel average (None = not measured)
_STRUCTURES = [
    ("question", r"\?|^(why|how|what|who|which|where|did you|have you|do you|would you|can you|will i)\b",
     "What happens if you shoot the moon?", 1.24),
    ("tease", r"\.\.\.|…|\b(this|these|wait|watch|until|guess|happens|the reason|here'?s why)\b",
     "Wait for the cop car at the end...", None),
    ("withheld / negation", r"\b(can'?t|won'?t|didn'?t|couldn'?t|wasn'?t|isn'?t|not|never|nobody|no one|only)\b",
     "Kids can't play this", 1.41),
    ("number promise", r"^\s*(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(\w+\s+){0,2}"
                       r"(things|features|reasons|ways|secrets|details|clips|facts|changes|mistakes|easter eggs)\b",
     "4 new features coming", 2.46),
    ("shock fact", r"\b\d+[\d,.]*\s*(x|%|years?|hours?|minutes?|days?|million|billion|k|times|miles?|dollars?)\b|\$\d",
     "He's 72 years old", 1.68),
    ("first-person claim", r"^(i|i'm|im|i'll|i've|we|we're)\b", "I'm gonna play it now!", 0.94),
    ("relatable situation", r"^(me|us|my|when|whenever|pov|that moment|every|you|your)\b|"
                            r"\b(my|your|his|her) (mom|mum|dad|parents?|girlfriend|gf|boyfriend|wife|brother|sister|"
                            r"teacher|boss|friends?)\b|\bin the room\b|\bwalks? in\b",
     "Me watching this with my mom in the room", 1.48),
    ("superlative", r"\b(best|fastest|worst|biggest|craziest|smartest|most|first ever|world'?s|#1|ever)\b",
     "The craziest escape ever", None),
]
STRUCTURES = [(n, re.compile(p, re.I), ex, m) for n, p, ex, m in _STRUCTURES]
_CAPS = re.compile(r"\b(?!(?:GTA|RP|VI|POV|NPC|OK|AI|HP|FPS|UI|PC|PS5|PS|XP|NBA)\b)[A-Z][A-Z']+\b")
_SPOILERS = re.compile(r"\b(reaction|compilation|highlights?|recap|full video|part \d+|episode|clip|funny moment)\b", re.I)
_STOP = set("the a an in on of to and for is are you your this that these with at it be was just my i me we so he she "
            "his her they them him its im i'm it's what why how who when wait watch end guys nobody expected did does do "
            "can will would really actually".split())
FILLER_OPEN = re.compile(r"^(um+|uh+|so yeah|anyway|okay so|alright so|like,|hey guys|what'?s up guys|welcome back|"
                         r"first of all|before we|alright chat|okay chat|let'?s go ahead|in today'?s video)\b", re.I)
MAX_WORDS, MIN_WORDS = 8, 2


def plain(text: str) -> str:
    return re.sub(r"\*", "", text or "").strip()


def content_words(text: str) -> list[str]:
    """Words that say what is happening ("Why are the hostages singing?" has two; "Wait for it..." has none)."""
    return [w for w in re.findall(r"[a-z0-9']+", plain(text).lower()) if w not in _STOP and len(w) > 2]


def structures(hook: str) -> list[str]:
    text = plain(hook)
    found = [n for n, pat, _, _ in STRUCTURES if pat.search(text)]
    if len(text.split()) >= 3 and _CAPS.search(text) and re.search("[a-z]", text):
        found.append("premise + twist")  # a short who-does-what with one shouted word
    return found


def check_hook(hook: str) -> tuple[list[str], list[str]]:
    """(fails, notes). Fails break a rule every viral hook in the study followed; notes are judgment calls."""
    text = plain(hook)
    words = text.split()
    found = structures(hook)
    fails, notes = [], []
    limit = 9 if "relatable situation" in found else MAX_WORDS
    if not MIN_WORDS <= len(words) <= limit:
        fails.append(f"hook has {len(words)} words (keep {MIN_WORDS}-{MAX_WORDS})")
    if found and len(content_words(text)) <= 1:
        fails.append("hook is vague: say who/what, e.g. \"Why are the hostages singing?\" not \"Wait for it...\" "
                     "(vague hooks measured 0.67x views)")
    if _SPOILERS.search(text) and not found:
        fails.append("hook labels the clip instead of teasing it (drop 'reaction' / 'highlights' / 'compilation')")
    elif not found:
        if len(words) <= 4:
            fails.append("hook is a bare topic label (0.88x views): turn it into a question, number, negation or "
                         "relatable line")
        else:
            fails.append("hook is a plain statement: use one structure (question, tease, withheld/negation, number "
                         "promise, shock fact, relatable situation, superlative)")
    if found == ["first-person claim"]:
        notes.append("first-person claims measured 0.94x: add a number, question or negation")
    if found == ["premise + twist"]:
        notes.append("a shouted word alone measured 0.97x: name who it is about or add a question/negation")
    return fails, notes


def check_title(title: str, hashtags=(), topic: re.Pattern | None = None) -> tuple[list[str], list[str]]:
    fails, notes = [], []
    if len(title) > 100:
        fails.append(f"title is {len(title)} characters (max 100)")
    caps = [w for w in re.findall(r"[A-Za-z']+", title) if len(w) > 1 and w.isupper() and w not in ("GTA", "POV", "VI")]
    if len(caps) > 2:
        fails.append(f"title has {len(caps)} ALL-CAPS words: emphasise at most 1-2")
    if re.search("[A-Z]", title) and not re.search("[a-z]", title):
        fails.append("all-caps title measured 0.90x views: capitalise only 1-2 words")
    if re.search(r"\b(update|announce\w*|explained|patch|now live|available now)\b", title, re.I):
        notes.append("announcement wording measured 0.85x: lead with what changed")
    if topic and not topic.search(title):
        tagged = any(topic.search(h) for h in hashtags)
        (notes if tagged else fails).append("title should name the game (or carry its hashtag)")
    return fails, notes


def opening_type(first_line: str) -> str:
    line = (first_line or "").strip()
    if not line:
        return "silent"
    if FILLER_OPEN.match(line):
        return "filler (trim the start)"
    if re.search(r"!|^(go|run|look|wait|watch|stop|no|yo|oh|bro|get)\b", line, re.I):
        return "action cold open"
    if re.search(r"\?|^(did you|have you|do you|what|why|how|will|would|can)\b", line, re.I):
        return "question"
    return "statement"


def topic_pattern(game: str | None) -> re.Pattern | None:
    """Title must name the game when the job says which game it is. GTA 6 gets its common spellings."""
    g = (game or "").strip().lower()
    if not g:
        return None
    if re.fullmatch(r"gta ?(6|vi)|grand theft auto (6|vi)", g):
        return re.compile(r"\bgta ?(6|vi)\b|grand theft auto (6|vi)\b|#gta6|#gtavi", re.I)
    return re.compile(re.escape(g).replace(r"\ ", r"\s*"), re.I)


# What the study measured, shown to the director so it can choose a structure on purpose.
def prompt_rules() -> str:
    lines = [f"  - {n}: e.g. \"{ex}\"" + (f" ({m}x views vs the channel's average)" if m else "")
             for n, _, ex, m in STRUCTURES if n != "first-person claim"]
    return ("Hook rules (checked by code, the plan is sent back if broken): 2-8 words; use ONE of these structures and say "
            "WHO or WHAT is involved (vague hooks measured 0.67x views, bare topic labels 0.88x, plain statements fail); "
            "never describe the clip ('reaction', 'highlights', 'compilation'); never give the payoff away.\n"
            + "\n".join(lines) + "\nTitle: max 100 characters, at most 2 ALL-CAPS words (an all-caps title measured 0.90x), "
            "no 'update/announced/explained' wording (0.85x).")


# Clip type -> hook structures, length and caption/hook style. Keyed by moment_type (director) and content_type (ranking).
PLAYBOOK = {
    "reaction": {"what": "the streamer reacting; their face and voice are the payoff",
                 "hooks": ["relatable situation", "withheld / negation", "question", "tease"],
                 "length": (9, 25), "layout": "split or bubble when the facecam is clear"},
    "comedy": {"what": "an absurd or funny scene; the situation is the joke",
               "hooks": ["relatable situation", "withheld / negation", "question", "tease"],
               "length": (15, 40), "layout": "fit or full; let the bit land"},
    "news": {"what": "information (features, leaks, dates, facts), usually narrated",
             "hooks": ["number promise", "shock fact", "withheld / negation", "question"],
             "length": (30, 58), "layout": "fit"},
    "gameplay": {"what": "a stunt, fail, glitch or chaos where something surprising happens",
                 "hooks": ["shock fact", "superlative", "question", "tease"],
                 "length": (9, 25), "layout": "full, or split when the facecam reacts"},
    "showcase": {"what": "trailer or graphics with nobody talking; context must be added by the hook",
                 "hooks": ["shock fact", "number promise", "question", "tease"],
                 "length": (20, 45), "layout": "full or fit"},
    "scenario": {"what": "a curiosity gap: someone makes, finds or shows something new and the hook withholds the result",
                 "hooks": ["question", "withheld / negation", "tease", "relatable situation"],
                 "length": (15, 55), "layout": "split when the facecam is clear; cut on the reveal"},
}
_BY_MOMENT = {"funny": "comedy", "intense": "gameplay", "surprise": "reaction", "fail": "gameplay", "story": "scenario"}
_BY_CONTENT = {"stream": "reaction", "commentary": "news", "gameplay": "gameplay", "cinematic": "showcase",
               "montage": "showcase", "tutorial": "news"}


def kind_for(content_type: str | None, moment_type: str | None = None, has_voice: bool = True) -> str:
    """Playbook entry for a candidate. A source with no speaking behaves like a showcase whatever its label."""
    if content_type in ("cinematic", "montage") or (content_type == "gameplay" and not has_voice):
        return "showcase" if content_type != "gameplay" else "gameplay"
    if moment_type in _BY_MOMENT:
        return _BY_MOMENT[moment_type]
    return _BY_CONTENT.get(content_type or "", "gameplay")


def playbook_text(content_type: str | None, moment_type: str | None = None, has_voice: bool = True) -> str:
    k = kind_for(content_type, moment_type, has_voice)
    p = PLAYBOOK[k]
    return (f"Clip type '{k}': {p['what']}. Prefer these hook structures in this order: {', '.join(p['hooks'])}. "
            f"Length: follow the story, not the type (a quick punchline can be {p['length'][0]}-{p['length'][1]} s, but a "
            f"setup -> escalation -> payoff story should run longer; the length rule above wins). Layout: {p['layout']}.")
