# Prompt for Codex: improve the Gaming Clipper dashboard

Paste everything below the line into Codex, opened at the folder `/Users/macos/Desktop/ai-automation-agency/gaming-clipper`.

---

Improve the local dashboard of this project so a non-technical owner understands how the pipeline works and where each job is.

## Where things are
- **Dashboard code (the only file you should need to change):** `worker/clipper/ui.py`.
  - FastAPI routes, server-rendered HTML strings and the `CSS` constant. No JS framework.
  - Pages: `/` (submit form + history), `/jobs/{id}/view` (job page with clips).
- **Read-only context (do not change behaviour):**
  - `worker/clipper/main.py`: API and auth.
  - `worker/clipper/db.py`: Postgres schema. Tables `jobs`, `stages`, `clips`, `costs`, `events`.
  - `worker/clipper/styles.py`: the 5 editing styles.
- **Pipeline stages, in order:**
  `ingest` → `analyze` (Whisper, loudness, scene cuts) → `rank` (Gemini scores each candidate moment) → `select` → per clip: `director` (Gemini writes the edit plan) → `assets` → `render` (ffmpeg + Remotion) → `qc` → human review.
  - Stage rows live in the `stages` table: `(job_id, name, clip_idx, status, attempts, error, …)`, where `clip_idx = -1` means job-level.
  - Clip rows live in the `clips` table: `status`, `candidate` (has `quality`, `title`, `start`, `end`, `below_quality_bar`), `edl` (has `style`, `moment_type`, `hook.text`), `qc` (has `passed`, `checks`), `review`, `versions`.
  - Costs live in the `costs` table, one row per Gemini call, with `item` like `rank:0` or `director:1`, `units` = `{in, out, model}` and `usd`.
- **Run it:** `cd worker && ./run.sh`, then open http://127.0.0.1:8787/.
- **Tests:** `cd worker && uv run pytest -q`. They must stay green.

## What to build
1. **Pipeline diagram on the job page.**
   - A horizontal step bar: Download → Analyse → Find moments → Pick → Edit plan → Assets → Render → Quality check → Review.
   - Each step shows its state: waiting, running (animated), done (with seconds taken), or failed (red, with the error text expandable).
   - Per-clip steps (Edit plan → Review) appear as one row per clip under the shared steps.
   - Data comes from the `stages` and `clips` tables.
2. **"Why this clip" panel on each clip card.**
   - Show the AI score (quality /10), moment type, the source time range (e.g. "1:06–1:46 of the original") and the hook text.
   - Show a clear badge "Below quality bar (added because you asked for N clips)" when `candidate.below_quality_bar` is true.
   - Show the QC result as a checklist (green or red per check) from `clips.qc.checks`.
3. **Rejected moments.**
   - A small section listing candidates that were not turned into clips, with their score and the AI's one-line reason.
   - Read `data/jobs/<id>/analysis/rank_*.json`, fields `quality`, `why_engaging`, `title`.
4. **Cost per job.** A small table on the job page: calls, tokens and USD, grouped by model. Note "Free tier: actual charge may be $0".
5. **Home page.**
   - Add a 1-paragraph "How it works" explainer with the same step bar (static).
   - History rows: show a thumbnail of the first clip, the number of clips, total duration and status as coloured pills.
6. **Polish.**
   - Readable on a phone (single column under 700 px).
   - Light and dark mode with the existing CSS variables.
   - No layout shift while the page auto-refreshes.

## Constraints (important)
- **Security stays exactly as it is:**
  - Keep every existing route, URL and form field name.
  - Keep `_same_origin()` checks on every POST route.
  - Keep the HMAC-signed review links.
  - Keep the `X-Clipper-Token` auth on API routes.
  - Don't add unauthenticated JSON endpoints that expose job data.
- **Everything is HTML-escaped** with the existing `e()` helper. Text from the AI and from file names is untrusted.
- **No new Python or JS dependencies, and no external CDNs or fonts.** The app must work offline on localhost. Inline SVG/CSS only.
- **Scope:**
  - Don't touch `n8n/`, `renderer/`, `.env` or the database schema.
  - Don't change pipeline behaviour.
  - Read-only DB queries only from the UI.
- **Size:** keep `ui.py` readable. If it grows past about 450 lines, split the HTML builders into `worker/clipper/ui_parts.py`.
- **When done:**
  - Run the tests.
  - Restart the worker and load `/` and one job page, checking that both return 200 with no errors in the worker log.
  - List what you changed.
