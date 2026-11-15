"""Forced alignment: pin each Whisper word to the exact time it is spoken, using a wav2vec2 CTC model
(facebook/wav2vec2-base-960h, English). Whisper's own word times are ~0.1 s off on average.
Runs only on the clip window, in chunks of <= 25 s. Words that can't be aligned keep Whisper's timing."""
import json
import re
import wave
from pathlib import Path

import numpy as np

MODEL_ID = "facebook/wav2vec2-base-960h"
FRAME_S = 320 / 16000  # wav2vec2 base: one output frame per 20 ms
_model = None


def _load():
    global _model
    if _model is None:
        import torch
        from transformers import AutoModelForCTC, AutoProcessor
        torch.set_num_threads(4)
        proc = AutoProcessor.from_pretrained(MODEL_ID)
        model = AutoModelForCTC.from_pretrained(MODEL_ID).eval()
        vocab = proc.tokenizer.get_vocab()
        _model = (proc, model, vocab)
    return _model


def _read(wav: Path, start: float, end: float) -> np.ndarray:
    with wave.open(str(wav)) as w:
        sr = w.getframerate()
        w.setpos(max(0, int(start * sr)))
        raw = w.readframes(max(0, int((end - start) * sr)))
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


def _norm(word: str) -> str:
    return re.sub(r"[^A-Z']", "", word.upper())


def _viterbi(emission: np.ndarray, tokens: list[int], blank: int) -> list[int] | None:
    """Most likely frame for each token (CTC alignment). Returns one frame index per token."""
    T, N = emission.shape[0], len(tokens)
    if N == 0 or T < N:
        return None
    tok = np.array(tokens)
    trellis = np.full((T + 1, N + 1), -np.inf)
    trellis[0, 0] = 0.0
    for t in range(T):
        stay = trellis[t] + emission[t, blank]
        change = np.full(N + 1, -np.inf)
        change[1:] = trellis[t, :-1] + emission[t, tok]
        trellis[t + 1] = np.maximum(stay, change)
    if not np.isfinite(trellis[T, N]):
        return None
    frames, j = [0] * N, N
    for t in range(T, 0, -1):
        if j == 0:
            break
        changed = trellis[t - 1, j - 1] + emission[t - 1, tok[j - 1]]
        stayed = trellis[t - 1, j] + emission[t - 1, blank]
        if changed >= stayed:
            frames[j - 1] = t - 1
            j -= 1
    return frames if j == 0 else None


def _align_chunk(words: list[dict], wav: Path, start: float, end: float) -> int:
    """Align words (in place) inside [start, end]. Returns how many words were re-timed."""
    import torch
    proc, model, vocab = _load()
    audio = _read(wav, start, end)
    if len(audio) < 1600:
        return 0
    with torch.inference_mode():
        inputs = proc(audio, sampling_rate=16000, return_tensors="pt")
        logits = model(inputs.input_values).logits[0]
        emission = torch.log_softmax(logits, dim=-1).numpy()
    blank, delim = vocab.get("<pad>", 0), vocab.get("|")
    tokens, owner = [], []  # owner[k] = index into words for token k (None for the word delimiter)
    for i, w in enumerate(words):
        chars = [c for c in _norm(w["w"]) if c in vocab]
        if not chars:
            continue
        if tokens and delim is not None:
            tokens.append(delim); owner.append(None)
        for c in chars:
            tokens.append(vocab[c]); owner.append(i)
    frames = _viterbi(emission, tokens, blank)
    if frames is None:
        return 0
    span: dict[int, list[int]] = {}
    for k, i in enumerate(owner):
        if i is not None:
            span.setdefault(i, []).append(frames[k])
    for i, fs in span.items():
        s, e = start + min(fs) * FRAME_S, start + (max(fs) + 1) * FRAME_S
        words[i]["s"], words[i]["e"] = round(s, 3), round(max(e, s + 0.06), 3)
    return len(span)


def align_words(words: list[dict], wav: Path, lo: float, hi: float, language: str | None, cache: Path | None = None) -> list[dict]:
    """Return copies of the words in [lo, hi] with aligned times (English only; others unchanged)."""
    ws = [dict(w) for w in words if w["e"] > lo and w["s"] < hi]
    if not ws or not Path(wav).exists() or (language and not language.startswith("en")):
        return ws
    key = f"{lo:.2f}_{hi:.2f}_{len(ws)}"
    if cache and cache.exists():
        try:
            hit = json.loads(cache.read_text()).get(key)
            if hit and len(hit) == len(ws):
                return hit
        except (OSError, ValueError):
            pass
    chunks, cur = [], [ws[0]]  # split at pauses so each chunk stays <= 25 s
    for a, b in zip(ws, ws[1:]):
        if b["e"] - cur[0]["s"] > 25 or (b["s"] - a["e"] > 1.0 and b["e"] - cur[0]["s"] > 12):
            chunks.append(cur); cur = []
        cur.append(b)
    chunks.append(cur)
    for ch in chunks:
        _align_chunk(ch, Path(wav), max(0.0, ch[0]["s"] - 0.3), ch[-1]["e"] + 0.3)
    prev = -1.0
    for w in ws:  # keep order, no overlaps
        w["s"] = max(w["s"], prev + 0.01)
        w["e"] = max(w["e"], w["s"] + 0.06)
        prev = w["e"]
    if cache:
        try:
            data = json.loads(cache.read_text()) if cache.exists() else {}
        except (OSError, ValueError):
            data = {}
        data[key] = ws
        cache.write_text(json.dumps(data))
    return ws
