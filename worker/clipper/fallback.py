"""LLM fallback when Gemini fails with a quota (429) or server (5xx) error after n8n's retries.
1) OpenRouter, same Gemini model through OpenRouter's own quota: video + audio + strict JSON schema.
2) Groq, last resort: 3 still frames + the prompt (no video/audio), json_schema best-effort.
Answers are returned in Gemini's response shape, so Save rank / Validate EDL / AI review work unchanged.
Cost: OpenRouter reports the exact USD per call (usage.cost); Groq is estimated from token counts."""
import base64
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import httpx

from . import budget, config, db

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
GROQ = "https://api.groq.com/openai/v1/chat/completions"
UA = {"User-Agent": "gaming-clipper/0.1", "HTTP-Referer": "http://127.0.0.1", "X-Title": "gaming-clipper"}
RETRYABLE = re.compile(r"\b(429|500|502|503|504)\b|quota|rate.?limit|too many requests|unavailable|overloaded|resource.?exhausted|timeout|timed out|ECONNRESET", re.I)


def keys() -> dict:
    return {"openrouter": bool(os.environ.get("OPENROUTER_API_KEY")), "groq": bool(os.environ.get("GROQ_API_KEY"))}


def gemini_failed(resp: dict) -> str | None:
    """Error text when n8n handed us a failed Gemini call instead of an answer; None when the answer is usable."""
    if isinstance(resp, dict) and resp.get("candidates"):
        return None
    err = resp.get("error") if isinstance(resp, dict) else None
    if isinstance(err, dict):
        return " ".join(str(err.get(k, "")) for k in ("httpCode", "code", "status", "message", "description")).strip() or "Gemini error"
    return str(err or "Gemini returned no answer")


def retryable(error: str) -> bool:
    return bool(RETRYABLE.search(error or ""))


def to_json_schema(s):
    """Gemini's OpenAPI-style schema (type: "OBJECT", nullable) -> standard JSON Schema."""
    if isinstance(s, list):
        return [to_json_schema(x) for x in s]
    if not isinstance(s, dict):
        return s
    out = {}
    for k, v in s.items():
        if k == "type" and isinstance(v, str):
            out["type"] = v.lower()
        elif k == "nullable":
            continue
        else:
            out[k] = to_json_schema(v)
    if s.get("nullable") and isinstance(out.get("type"), str):
        out["type"] = [out["type"], "null"]
    if out.get("type") == "object" or out.get("type") == ["object", "null"]:
        out.setdefault("additionalProperties", False)
    return out


def _parts(body: dict) -> tuple[bytes | None, str]:
    video, text = None, ""
    for p in body["contents"][0]["parts"]:
        if "inline_data" in p:
            video = base64.b64decode(p["inline_data"]["data"])
        elif "text" in p:
            text += p["text"]
    return video, text


def _schema(body: dict) -> dict | None:
    s = body.get("generationConfig", {}).get("responseSchema")
    return to_json_schema(s) if s else None


def _as_gemini(text: str, usage: dict, model: str, cost: float | None) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}], "modelVersion": model, "fallback": True,
            "usageMetadata": {"promptTokenCount": usage.get("prompt_tokens", 0), "candidatesTokenCount": usage.get("completion_tokens", 0)},
            **({"costUsd": float(cost)} if cost is not None else {})}


def openrouter(body: dict, model: str) -> dict:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    video, text = _parts(body)
    content = []
    if video:
        content.append({"type": "video_url", "video_url": {"url": "data:video/mp4;base64," + base64.b64encode(video).decode()}})
    content.append({"type": "text", "text": text})
    req = {"model": model, "messages": [{"role": "user", "content": content}],
           "temperature": body.get("generationConfig", {}).get("temperature", 0.3), "max_tokens": 8192,
           "provider": {"require_parameters": True}, "usage": {"include": True}}
    schema = _schema(body)
    req["response_format"] = ({"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": schema}}
                              if schema else {"type": "json_object"})
    r = httpx.post(OPENROUTER, json=req, headers={**UA, "Authorization": f"Bearer {key}"}, timeout=240)
    if r.status_code >= 400:
        raise RuntimeError(f"OpenRouter HTTP {r.status_code}: {r.text[:300]}")
    d = r.json()
    if d.get("error"):
        raise RuntimeError(f"OpenRouter error: {str(d['error'])[:300]}")
    msg = d["choices"][0]["message"]["content"]
    usage = d.get("usage") or {}
    return _as_gemini(msg, usage, f"openrouter:{model}", usage.get("cost"))


def _frames(video: bytes, n: int = 3) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        v = Path(tmp) / "v.mp4"
        v.write_bytes(video)
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(v)],
                                   capture_output=True, text=True).stdout.strip() or 0)
        out = []
        for i in range(n):
            p = Path(tmp) / f"f{i}.jpg"
            subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{dur * (i + 0.5) / n:.2f}", "-i", str(v), "-frames:v", "1",
                            "-vf", "scale=-2:480", "-q:v", "5", str(p)], check=False)
            if p.exists():
                out.append("data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode())
        return out


def groq(body: dict, model: str) -> dict:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY not set")
    video, text = _parts(body)
    content = [{"type": "image_url", "image_url": {"url": u}} for u in (_frames(video) if video else [])]
    content.append({"type": "text", "text": "You only get 3 still frames of the video (no motion, no sound). " + text})
    req = {"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0.3, "max_completion_tokens": 8192}
    schema = _schema(body)
    req["response_format"] = ({"type": "json_schema", "json_schema": {"name": "answer", "strict": False, "schema": schema}}
                              if schema else {"type": "json_object"})
    r = httpx.post(GROQ, json=req, headers={**UA, "Authorization": f"Bearer {key}"}, timeout=180)
    if r.status_code >= 400:
        raise RuntimeError(f"Groq HTTP {r.status_code}: {r.text[:300]}")
    d = r.json()
    usage = d.get("usage") or {}
    pin, pout = config.GROQ_USD_IN, config.GROQ_USD_OUT
    est = usage.get("prompt_tokens", 0) / 1e6 * pin + usage.get("completion_tokens", 0) / 1e6 * pout
    return _as_gemini(d["choices"][0]["message"]["content"], usage, f"groq:{model}", est)


def rescue(job_id: str, kind: str, resp: dict, build) -> dict:
    """Pass a good Gemini answer through; on a retryable failure try OpenRouter, then Groq. `build()` rebuilds the
    original request ({"model", "body"}) only when needed. Returns Gemini-shaped JSON (an answer or the final error)."""
    err = gemini_failed(resp)
    if err is None:
        return resp
    if not config.FALLBACK_ENABLED or not retryable(err):
        return resp
    est = budget.EST.get("judge" if kind == "rank" else kind, 0.02)
    why = budget.blocked(job_id, est)
    if why:
        db.event(job_id, "warn", kind, f"fallback skipped: {why}", {})
        return resp
    req = build()
    model = req.get("model") or config.MAIN_MODEL
    tried = []
    for name, fn, mdl in (("openrouter", openrouter, config.OPENROUTER_MODELS.get(model, f"google/{model}")),
                          ("groq", groq, config.GROQ_MODEL)):
        if not keys()[name]:
            tried.append(f"{name}: no key")
            continue
        try:
            out = fn(req["body"], mdl)
            json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", out["candidates"][0]["content"]["parts"][0]["text"].strip()))
            db.event(job_id, "warn", kind, f"Gemini failed ({err[:80]}); answered by {name} ({mdl})", {"tried": tried})
            return out
        except Exception as e:  # noqa: BLE001 - try the next provider
            tried.append(f"{name}: {str(e)[:150]}")
    db.event(job_id, "error", kind, f"Gemini failed ({err[:120]}) and no fallback answered", {"tried": tried})
    return {"error": {"message": f"Gemini: {err[:200]} | fallback: {'; '.join(tried)[:300]}"}}
