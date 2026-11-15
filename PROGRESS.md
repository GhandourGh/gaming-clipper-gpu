# Gaming Clipper: progress log

## 2026-11-15: ideas ported from the reference repos (hook rules, signals, live recording)
Source studies are in `~/Desktop/reference-repos` (audit: `AUDIT.md`). Everything below is our own implementation; no code copied from the AGPL repo.
- **Hook / title rules** (`worker/clipper/hookrules.py`): 8 hook structures with the study's measured view multipliers, vague/label/plain-statement checks, title checks. `edl.check(strict_hook=...)` sends the plan back for the first two director attempts, then only warns, so a clip is never lost over wording. The director prompt now includes the rules and a clip-type playbook (hook order, length, layout).
- **Signals** (`momentum.py`, wired into `analysis.score_timeline` / `_extra_signals`): loudness *jump* over the preceding seconds, YouTube replay heatmap (from `source.info.json`, shifted by the downloaded section), Twitch ad-break mask (live/Twitch sources only; keyframe-only scan, 15 min of 1080p in 3.8 s), Twitch viewer clips (`twitchclips.py`, needs TWITCH_CLIENT_ID/SECRET).
- **Clip floor**: `gemini.clip_floor` + `_top_up_to_floor`: at least MIN_CLIPS (3, one per 90 s of source) by adding moments within 2 points of the quality bar, flagged `below_quality_bar`. Skipped when an explicit clip count is requested.
- **Live recorder** (`live.py`, Live page, `/dashboard/live*`): per-channel ffmpeg segment recorder (30-min gapless chunks), topic filter, restart-safe pid adoption, disk guard, rights confirmation required, chunks remuxed into `inbox/` and submitted as jobs (`engine=moments`, `meta.live`). Auto-clip spends AI credit within the existing limits and can be switched off per channel.
- **Regression set**: `python -m clipper.evalset` (3 synthetic cases incl. an ad-break case with a negative control in pytest).
- Verified: 102 tests pass; recorder chunking + remux against a local real-time stream; real analysis job on the GTA VI trailer file (new `jump` component present, then deleted); heatmap parsed from a real yt-dlp info file; Live page rendered, auto-clip toggle persisted through dashboard proxy to worker; dashboard `tsc` clean.
- **Not verified**: a real Twitch/Kick/YouTube live stream (no live channel tested), Twitch Helix calls (no keys), a full job through Gemini with the new hook rules (no AI spend this session), the clip-floor top-up on a real ranked job, ad detection on a real Twitch ad slate (no sample; thresholds are the study's).

## 2026-11-15: context-aware storytelling engine
- New jobs default to `stories`; the New Job page accepts a YouTube link and offers Storytelling vs the legacy Quick moments engine.
- Storytelling downloads/analyzes the complete source (720p ceiling for long YouTube sources), fully transcribes it, and asks the transcript model for evidence-backed hook/setup/development/payoff beats that may be hours apart.
- Multi-hour indexing uses the local cached Whisper Base model; only selected story ranges are re-transcribed with Large v3 Turbo. This avoids spending roughly an hour on a full high-accuracy transcript before story selection.
- Sources over 20 minutes now build a distributed evidence montage (chapter samples plus full-stream audio/scene/reaction peaks, normally 20-30 minutes of material) and transcribe it in one pass. The complete signal scan is retained, while word-level precision is deferred to selected beats.
- Story candidates retain their connection explanation, evidence, narrative roles and absolute source timestamps through video ranking and the creative-director stage.
- The EDL validator rejects footage from gaps outside the approved story beats. The renderer seeks every selected range independently, so it does not decode the hours between distant beats.
- Existing moment clipping remains available and existing jobs stay isolated by engine in the idempotency key.
- Deployed the regenerated local n8n workflows; restarted worker/dashboard. Verification: 69 worker tests, dashboard production build and both renderer/dashboard TypeScript checks pass.
- First real multi-hour Storytelling job still needs to be run with a user-approved/owned YouTube URL; complete-source download and transcription time depend on YouTube throughput and source length.

Resume point for any session. Last update: 2026-11-14 ~03:45 (Europe/Paris). Branch `feat/gaming-clipper`, **nothing committed yet**.
Run: `worker/run.sh` (worker + its Postgres). Deploy workflows: `n8n/deploy.sh`. Tests: `cd worker && uv run pytest -q`.

## Verified (actual runs, outputs inspected)

| Area | Evidence |
|---|---|
| Worker API, `127.0.0.1:8787` | Token auth (401 without), API docs disabled, path traversal and bad URLs rejected, cross-site form posts blocked (403), forged review links rejected (403). |
| Jobs / idempotency / resume | Same file → same job; resubmitting resumes after the last finished stage; expired jobs restart from ingest; renders cached by EDL + renderer code + derived props. |
| Pass 1 analysis | 35 s test in 21 s (mlx-whisper large-v3-turbo + loudness + scream band + scene cuts). The unit test finds a synthetic scream at 120 s in a 180 s file. |
| Gemini rank → select | Strict schema; weak / overlapping moments dropped. |
| **Editing styles** (Viral, Funny, Cinematic, Fast, Minimal) | Style guide + moment-type strategy (funny/intense/surprise/fail/story) in the director prompt. **Hard per-style limits enforced on the EDL** (zooms, SFX, overlays, transitions, no emoji / max zoom for Cinematic). Renderer themes per style. Verified: Viral vs Cinematic regenerate of the same clip produced clearly different, rule-conforming edits (`data/review/viral_vs_cinematic.png`). |
| Renderer | fit/full/split layouts, punch/smooth zooms, word captions + emphasis, hook card, emoji/text overlays, shake/flash/zoom_punch, SFX. Captions moved into the platform safe area (bug found by frame review). |
| QC | dims, fps, A/V sync, duration, black frames, dead air, loudness, true peak, decode, caption timing, caption width, assets. Found 6 real bugs so far, all fixed. |
| n8n workflows (all published) | **MVP** (form with style picker + webhook), **Assets** (sub-workflow), **Errors** (Telegram alert), **Regenerate clip** (webhook; verified end to end in 80 s), **Maintenance** (daily 04:00 cleanup → Telegram report; schedule created, first run not yet observed). |
| Dashboard `http://127.0.0.1:8787/` | Paste URL / upload / pick from inbox → choose style → generate; history table; job page with progress, stage timings, errors, cost, thumbnails, preview, per-clip download, **Download all (zip)**, Approve/Reject, **Regenerate with a different style**, previous versions kept. |
| Unit tests | 15 passing (EDL mapping/checks/style limits, schema rejection, URL validation, inbox traversal, Gemini JSON extraction, candidate detection, QC). |

## 2026-11-14 morning: real-footage test (GTA VI Trailer 2, YouTube, test only)
- Full run fresh: download 7 s, analysis 61 s, ranking ~60 s, then per clip: plan ~15 s + render 134-210 s + AI review ~10 s. 3 clips in ~13.6 min **on a heavily loaded Mac (load avg 30-50 with Codex, Chrome and other sessions)**. Render is the bottleneck.
- Model switched to **gemini-3.1-flash-lite** (free tier; paid-rate equivalent ~$0.02 per 3-clip job).
- New: content type detected in the rank call (stream/commentary/gameplay/cinematic/montage/tutorial) with per-type scoring; trailer moments went from 2/9/2 to 8/9/9.
- New: Whisper gets title + YouTube tags as vocabulary; the director can return `caption_fixes` (it fixed "Hamlu Nation" -> "Ammu-Nation").
- New: dead-opening trim (blackdetect on the first segment).
- New: AI review of the finished MP4 (rubric: hook, clarity, pacing, captions, framing, effects, payoff). Score < REVIEW_MIN (5.0) => one redo; if the redo scores lower the first version is restored. Verified: a redo scored lower and was reverted.
- New: `clips` option (exact number of clips), pre-blurred background (render 181 s -> 151 s on the same clip).
- Fixed: stale yt-dlp (403), partial-download reuse, overlapping candidates, quota errors reported as "no highlights", missing proxies on resubmit, caption word spacing, multi-word caption fix overflowing the safe area.

## 2026-11-14 midday: owner dashboard (light theme)
- `dashboard/` (Next.js, port 3001, started with `dashboard/run.sh`): Overview (KPIs, system health, running jobs with live step + progress, clips to review), New job (URL/upload/inbox, style, clip count, length presets), Jobs (filters + search), Job page (pipeline per step with times, clips, unused moments with AI reasons, AI usage), Clip library (filters, search, player with approve/reject/regenerate/download), Publishing (per-platform drafts: title/caption/hashtags/planned time; Publish button locked), Accounts (YouTube/Instagram/TikTok: not connected, requirements listed), Costs (30-day chart, by model/step/job), Settings (defaults for new jobs, AI model, redo threshold, retention, Telegram toggle).
- Worker: new `clipper/manage.py` (token-protected `/dashboard/*` API, `posts` table, `data/settings.json` defaults applied to new jobs and to model/review/retention config).
- Browser never gets the worker token: guarded pass-through `/api/w/*` (allow-list) and `/api/media/*` (video/thumbs/zip with Range).
- Verified in the browser at 1440x900 and 375x812: every page loads with real data, drafts create/edit/save/delete, video plays through the proxy.
- Not wired yet: cutting approach + Telegram toggle are stored but not read by the pipeline/n8n; publishing/OAuth not built (locked). Dashboard listens on 127.0.0.1 only (phone access needs a secure tunnel or LAN setup).
- Seen in the library: the GTA 5 source had burned-in captions, so that clip shows two caption layers (pipeline should detect existing captions).

## 2026-11-14 afternoon: paid Gemini, model split, spending limits
- Owner added $5 of Gemini credit (billing on). Key checked: 37 Gemini models; prices taken from Google's pricing page.
- Model roles: `gemini-3.1-flash-lite` screens every candidate; `gemini-3.8-flash` re-watches the top 3 at 2 fps / low media resolution ("judge" pass), plans edits (2 fps) and reviews clips. Whisper stays for transcripts.
- Costs are priced per model (3.8 Flash doubles on 2027-01-01, built in). Estimate ≈ $0.05 per clip now, ≈ $0.08-0.10 after Jan 1.
- Spending limits (Settings): $0.10/job, $0.50/day, $4.00 total, counted from billing start. Screening is refused if the day/total budget can't cover the job; selection makes only as many clips as the per-job budget can edit+review; AI-review redos only with budget left.
- n8n: ranking now loops screen -> judge -> `{"done": true}` -> select. Deployed.
- Verified: unit tests (23), one live call each to 3.8 Flash and 3.1 Flash-Lite with fps=2 + low resolution (accepted; ~160 tokens per second of video). A full job on the new setup has NOT been run yet.

## 2026-11-14 afternoon: cut snapping + caption timing check
- New `clipper/timing.py`. Every cut in an AI edit plan is moved (in code) to a word gap, preferring sentence ends, then long pauses, then scene changes when nobody is talking; never inside a word; main segments never overlap. If the clip would still END mid-sentence, the plan goes back to the AI for repair.
- Verified offline on two real edit plans: Cinna clip cuts inside a word 5 -> 0, segments ending mid-sentence 1 -> 0; GTA 5 clip 18 cuts moved to sentence ends/pauses. 28 unit tests.
- Caption sync measured (caption start vs speech onset in the rendered audio): no constant delay (median -5 / +40 ms) but ~110 ms per-word jitter from Whisper. An energy-based re-timing did not measurably help on game audio (111->105, 110->112 ms), so it is OFF (CAPTION_REFINE=0). Then (owner OK'd): installed torch 2.15 + transformers 5.23 (worker .venv now 1.3 GB) and added `clipper/align.py`: wav2vec2-base-960h CTC forced alignment of the clip window (English; other languages keep Whisper timing), cached in analysis/aligned.json, used for captions AND cut snapping (CAPTION_ALIGN=1).
- Verified: synthetic speech with known timing, first-word error 150 -> 10 ms. Real clips (same metric): Cinna 111 -> 70 ms mean, >150 ms 24% -> 12%; GTA 5 (4 people over loud game audio) 110 -> 90 ms, 28% -> 25%. Adds ~14 s per clip.

## 2026-11-14 evening: six integrations (owner brief)
All optional; keys go in `.env` (names in `.env.example`); the dashboard Settings page shows which keys are set (yes/no only).

| Feature | Code | Verified with a real run | Not verified | Cost |
|---|---|---|---|---|
| 1. Music | `music.py`, `assets.add_music`, n8n Assets step "Pick music", renderer `Music` (ducking) | No-key path: Openverse returned real Jamendo tracks (CC BY / BY-SA); a render with music on the Cinna clip measured the music **8.7 dB lower under speech** than in pauses; licence/author/source in `assets_manifest.json`. In the end-to-end run the editor chose no music for the trailer (it has its own soundtrack), which follows the prompt. | Jamendo direct (no key). **Freesound verified 2026-11-14** with a real key: returned a CC0 track (53 s) with author and source. | $0 (free APIs) |
| 2. Burned-in captions | `burned.py` (Apple Vision OCR, on-device), run at ingest, job flag `meta.burned_captions`, director prompt, job page + Telegram | All 4 real sources classified correctly (GTA 5 = yes, bottom; Cinna, trailer, synthetic = no) after fixing a Twitch-chat and a web-text false positive; ~17 s per video. | - | $0 |
| 3. LLM fallback | `fallback.py`, `/jobs/{id}/llm/rescue`, n8n "Fallback check" after every Gemini call (rank, director, review, moderation, regen) | End-to-end run: every Gemini answer passed through unchanged. Forced a real quota error on a real job: fallback tried OpenRouter + Groq (no keys), returned a clear error, never "no highlights". OpenRouter's public model list confirms google/gemini-3.8-flash + 3.1-flash-lite take video+audio+strict JSON. | A real OpenRouter / Groq answer (no keys) | OpenRouter: Google price +~10% (3.8 Flash $0.83/$4.12 per 1M); Groq estimated |
| 4. Trends | `trends.py` (ShortsIntel `/v1/keywords`, TrendsMCP REST `get_top_trends`), `/jobs/{id}/trends`, n8n "Trend hints", daily cache | End-to-end run: game detected, step skipped cleanly ("no key"), job continued. | **ShortsIntel verified 2026-11-14** with a real key: YouTube results carry no hashtags, so it now asks TikTok (`SHORTSINTEL_PLATFORM`), ranks hashtags by views, drops filler tags (#fyp…) and passes the top captions as hook examples. TrendsMCP still not tested (no key). | ShortsIntel 3 credits ≈ $0.003/lookup (1,000 free); TrendsMCP 100 free requests/month |
| 5. Moderation (Gemini; SafeReel does not exist) | `gemini.moderation_request/save_moderation`, n8n "Moderation on?" branch, Settings toggle (off by default) | End-to-end run with it switched on: sexual low, gore low, hate none -> passed; a flagged clip fails QC (unit test). Switched back off. | A real flagged clip | ~$0.001 per clip (Flash-Lite) |
| 6. Publishing | `publish.py` (Ayrshare adapter; PostLake/Publora refuse until real docs exist), posts approve/send/publish, n8n "Gaming Clipper — Publish" (webhook + Telegram), dashboard buttons | Through dashboard -> n8n -> worker: send before approval refused, send without key refused, publish while locked refused (Telegram notice each time). | A real Ayrshare draft (no key; video posts need a paid Ayrshare plan; Ayrshare has no draft mode, `requiresApproval` is used as the hold) | Ayrshare plan price (not per call) |

- End-to-end run (GTA VI trailer from disk, 1 clip, 30-45 s, moderation on): 614 s, $0.066 (rank $0.005, judge $0.029, director $0.024, review $0.007, moderation $0.001).
- Tests: 49+ passing (`cd worker && uv run pytest -q`). The owner's own n8n workflows were not touched (last updated Nov 13).
- New deps: pyobjc-framework-Vision (~13 MB). Other sessions edited the same files in parallel today (sound library, fonts); their changes were left as they were.

## Not verified / not done

- **No real gaming footage tested yet** (blocker: owner approval to download specific CC-licensed files, or own videos in `inbox/`). Everything above ran on `inbox/synthetic_test.mp4`. Facecam split layout, multi-clip jobs, Openverse images, the YouTube path and multi-speaker/no-speech/long-video behaviour are untested on real footage.
- Dashboard submit → n8n path: route and CSRF block verified; a full submission through the UI form not yet run.
- Telegram video delivery: not confirmed by the owner.
- Background music (no licensed library), Twitch, stream monitoring, scheduled discovery, publishing, user accounts: **not built**. Publishing is intentionally off.
- Quality-scoring rubric for rendered clips (human or LLM review of the final MP4): not built yet; currently AI pass-2 quality + technical QC + human review.
- Concurrency: one stage at a time by design (M1, 16 GB); a second job queues.
- The n8n Form trigger production URL returned 404 earlier; the webhook and dashboard are the supported entry points.

## Costs
Gemini total so far: **$0.033** across all test runs (≈ $0.0034 per ranked candidate, ≈ $0.0056 per edit plan). Local compute: €0. Storage: 173 MB of job data (cleaned after 7 days). Free-tier status of the Gemini key not verified.

## CC-licensed candidates found (not downloaded; awaiting owner approval)
From Wikimedia Commons:
- *Dark Souls II – Let's Play … mit den Donots 3* (804 s, 257 MB, CC BY 3.0): multi-speaker reactions, German.
- *PAC-MAN 256 – Let's Play (gameplay)* (104 s, 117 MB, CC BY 3.0): commentary.
- *Oroboro's Dark Souls II Parry Tips* (317 s, 141 MB, CC BY 3.0): action + commentary.
- *The Witcher 3 – 35 minutes of gameplay* (2222 s, 1.8 GB, CC BY 3.0): long-video test.

## Next steps
1. Run the real-footage tests above; score each clip (entertaining? buildup/payoff? cuts? captions? zooms/SFX purposeful? framing?) and tune the prompts and detection.
2. Final-render review step: Gemini watches the rendered clip and scores it against a rubric, auto-regenerating below threshold (bounded, 1 retry).
3. Licensed music library (e.g. CC0 tracks) with ducking; test the facecam split layout on real streamer footage.
4. **Faster render: ffmpeg does video + background, Remotion draws only captions/emoji/hook on a transparent layer** (est. 1-2 h of work; target ~40-60 s per 27 s clip instead of ~2-3 min). Risks: zooms, transitions and caption timing must line up across layers; keep the current renderer as the fallback if the look is not identical. Why: Remotion's per-frame Chrome capture is ~45% of render time; ffmpeg does the same frames ~10x faster.
5. Later: Docker memory (stop unrelated containers), find why Remotion sometimes outputs 799-801 of 802 frames, cloud/batch rendering for 10-50 clips only with the owner's approval of the cost.

## 2026-11-14: render speed
- Final render of the 27 s test clip: 194 s -> ~115-170 s. Tiny stored background (180x320), 4 Chrome tabs by default (2 if RAM nearly gone), final pass copies the video and only normalises audio, render kills its Chrome children on timeout. Measured, QC passes, 49 tests pass.

## 2026-11-14: stickers, fonts and sound library
- LottieFiles creator MCP added to this project (`claude mcp add --transport http creator-mcp https://creator.mcp.lottiefiles.com/mcp`); **needs the owner to log in** (`/mcp` in a terminal session) before use.
- Microsoft Fluent Emoji cloned (shallow) to `vendor/fluentui-emoji` (gitignored, 413 MB, 1,595 emoji in 3D/Color/Flat/High-contrast, MIT). Not wired into the overlay picker yet (Twemoji still used).
- Fonts: one Google Font per editing style via `@remotion/google-fonts` (already installed; SIL OFL): Viral = Montserrat Black, Funny = Luckiest Guy, Cinematic = Bebas Neue, Fast = Anton, Minimal = Inter ExtraBold. Captions, hook card and text overlays use the style's font. Verified with one still per style from the Cinna clip.
- Sounds: 92 CC0 sounds from 4 Kenney packs (impact, interface, UI, digital) in `renderer/public/sfx/kenney/` with `index.json`, `SOURCES.json` and the CC0 licence; 15 new SFX types for the AI editor (punch, hit_metal, thud, glass_break, bell, click, confirm, error, tick, record_scratch, glitch, power_up, laser, zap, level_up), stable random variant per moment; credits go to assets_manifest.json (`clipper/sfxlib.py`). Verified: 3 s test render with glass_break peaks at -1.0 dB vs -23.8 dB without it. 40 unit tests pass.
- Cost: $0 (all local / free licences). Not verified: a full job using the new sounds/fonts end to end.

## 2026-11-14: editable clips + full-screen layouts
- Every finished clip keeps an editable Remotion project (`data/jobs/<id>/render_<n>/`). Open it with `./dev.sh edit <job_id> <clip_idx>` (Remotion Studio, port 3010). New optional props: `captionScale`, `captionOffsetY`, `zoomScale`, `logos[]` (PNG in `public/logos/`). Render from Studio, then `./dev.sh polish in.mp4 out.mp4` (loudness, video copied).
- Layout default is now full-screen crop (`full`); facecam clips use `split` with the webcam on the top ~22%; `fit` (blurred background) only when the AI says the wide frame matters; cinematic/gameplay/montage without facecam are forced to `full`.
- Not yet: multi-clip compilation editor with stock footage and music (planned).

## 2026-11-14 evening: streamer clips + fast renderer (owner: "fast, real moments, best ever")
Research (vendor blogs, treat as hints): stacked facecam-over-gameplay is the standard streamer layout; the reaction is the payoff; hook in the first 3 s; captions for sound-off viewers; **live-chat spikes are the strongest moment signal** (chat lags the moment by a few seconds). yt-dlp still gets YouTube live-chat replays; its Twitch "rechat" was removed in 2026.

| Change | Verified | Not verified | Cost |
|---|---|---|---|
| **Fast renderer** (`fastrender.py`): same props as Remotion, one ffmpeg pass (libass captions, per-frame zoom via scale+crop, VideoToolbox H.264). Default engine; Remotion is the automatic fallback and a Settings choice. | 46.7 s Cinna clip: **7-10 s vs 241 s** in Remotion. Frame-by-frame comparison: same crop, captions (size calibrated, 1.56x), hook, flash, zoom, stickers. Real job render **23.8 s** incl. cutting. A caption-overlap bug found by the AI review was fixed (+ test). | Fit layout and music ducking only checked in unit tests / earlier frames. Hook box has square corners (Remotion: rounded). | $0 |
| Homebrew `ffmpeg-full` (owner approved). It upgraded `libvpx`, which **broke the normal ffmpeg for ~20 min**; fixed with `brew upgrade ffmpeg` (now 9.0.2; openai-whisper rebuilt). No job ran meanwhile. | ffmpeg/ffprobe 9.0.2 work, all tests pass. | | $0 |
| **Facecam**: Apple Vision face detection + webcam box from the "still region" (webcam pixels change less than gameplay). | Cinna: old detector found nothing; new one found the webcam with exact borders (0,0,0.271,0.268). | Other stream layouts (cam in a corner of a busy overlay, green screen). | $0 |
| **Bubble layout** (corner circle/rounded cam, grows toward the centre on a facecam zoom = reaction zoom without cutting), plus split. AI chooses split vs bubble, corner and shape. | Rendered split, round bubble, rounded bubble with a reaction zoom (frames checked). Remotion version type-checks. | Remotion bubble not rendered. AI has not picked "bubble" yet in a real job (it picked split). | $0 |
| **Reaction + chat signals**: facecam motion from the same single hardware-decoded pass as scene cuts; YouTube live-chat replay -> chat rate + hype rate (shifted 5 s); both feed the moment score, the ranker and the director (reaction/chat times for facecam zooms). | Real job: 5 reactions found, director put 2 facecam zooms on them. Chat parsing/scoring unit-tested. | A real YouTube stream with chat replay (needs a URL you approve). | $0 |
| **Channel watch** (n8n "Gaming Clipper — Channel watch", every 30 min): YouTube RSS per channel in Settings -> Telegram message with a "Clip it" link that opens a pre-filled New job. Never downloads or clips by itself. Off by default. | Real channel (Rockstar Games): first check records, repeat finds nothing, a new video gives the link. Endpoint needs the token (401 without). Workflow deployed and active. | A Telegram message (watching is off, no channels set). | $0 |
| Director redo fix: plans were cut off (thinking counts toward the 8k output limit) -> 24k for the director; plan retries now stop when a retry would break the spending limits. | Unit test; 59 tests pass. | A real redo after the fix. | the test job cost $0.157 (> $0.10 job limit) because of 3 cut-off retries; now prevented |

Real end-to-end streamer job (47 s Cinna clip from disk): **95 s total** (was 10-15 min), 1 clip, split layout + 2 reaction zooms, QC passed, AI review 4/10 (dead air, cold open starts mid-word, letterbox bar inside the stream).

### Next (plan)
1. Long streams: download audio + chat first, find moments, then download only those video sections (`--download-sections`) -> hours-long VODs in minutes. Needs one approved test stream URL.
2. Director quality on streams: cut dead air between reactions, never start a cold open mid-word, crop letterbox bars inside the gameplay area.
3. Real YouTube stream with chat replay as the test (owner picks the video).
4. Rounded hook box in the fast renderer; render the Remotion bubble once to compare.

## 2026-11-14 late: full-frame streamer layout + captions option (owner feedback from phone)
- Bug: black bands inside the picture (a letterboxed trailer playing inside the stream) stayed in the gameplay area. Fix: `signals.gameplay_area()` finds bands that stay dark in >=90% of sampled frames (ignoring the webcam and chat/alerts sitting in the band) and both renderers crop the gameplay to the real picture. Verified on the Cinna clip (area y 0.107-0.893), frames checked, 61 tests pass.
- Captions option (Settings default + per job): `overlay` (on the video), `bar` (own 200 px strip between webcam and gameplay, text 80% size, covers neither), `off` (no captions; also skips word alignment). Passed through dashboard -> n8n -> worker. Verified: real worker renders of the test clip in all 3 modes, 7-10 s each.
- The bars above/below the video on the owner's phone are the phone's player (screen taller than 9:16); the file is 1080x1920 and fills the screen on TikTok/Shorts.
- Not verified: Remotion fallback with the strip/area (type-checks only); a new job submitted from the dashboard with the captions choice.

## 2026-11-14 night: first real stream (iShowSpeed, Fortnite, 2 h 44 min, YouTube, test only)
- Long-stream mode: no chat replay is available without login (YouTube's web client needs a token; not bypassed), so the full soundtrack was scanned (20 s) and minutes 87-127 picked as the most excited part; only that 40-min section was downloaded.
- Download: **YouTube throttles to ~0.7 MB/s** here (normal and section downloads alike); the 40-min section took 25 min. After repeated test requests YouTube answered 403 -> tests stopped. Next: download only the candidate windows (e.g. 6 x 2.5 min) instead of 40 min.
- Fixed: video scan 11x faster (no deblocking/B-frames + 4 parallel slices: 15 min of 1080p60 in 18.6 s); segment cut seeks first (98 s -> seconds; frames identical, PSNR inf); a 3.8 s teaser is trimmed to 3.5 s instead of failing the plan; emoji removed from the hook text (libass). 63 tests pass.
- Result: Gemini rated all 8 candidates 7-8.5/10 (real highlights: clutch eliminations, stream snipers, Speed vs Jynxzi). The $0.10 job limit allowed 1 clip; its first plan failed on the teaser rule (now auto-fixed) and was re-used at $0. Clip: 35 s, split layout, caption strip, 2 facecam zooms on reactions, QC passed. Final render 10 s. Job cost $0.089.
- Not verified: AI review of this clip (job limit), burned-caption flag ("middle") looks like a false positive from the stream overlay.
- Second stream, complete run (iShowSpeed "haunted house" stream, 143 min, 1 clip): section length now scales with the clips asked for (1 clip = 15 min). Download 8.5 min (throttled), analysis 80 s, Gemini + plan ~3 min, render ~40 s, QC passed, **AI review 6/10 (kept)**, total **14 min**, cost **$0.115**. Clip: "Speed Thinks Harry Kane Won Ballon d'Or" (Speed watching the ceremony), split layout, caption strip, licensed music (Jamendo, CC BY-SA). Review notes: teaser repeats footage, "d'Or" caption split, ends before the punchline. Burned-caption flag again a false positive (TV graphics, scoreboard rows).

## 2026-11-15: ideas from the top GitHub clippers (own implementation, no code copied)
Repos read: autoclip (10.4k), FunClip (6.6k), AI-Youtube-Shorts-Generator (6.1k), auto-editor (5.9k), SupoClip (3.1k), HotClip, Clips Kitty (streams/gaming). Most big ones are transcript-only podcast clippers.
- **Transcript-first scoring** (`textscore.py`, n8n "Text score request -> Gemini: score transcript -> Save text score"): the whole downloaded section is transcribed during analysis (in parallel with the video scan), one text-only flash-lite call scores up to 15 moments 0-100, fused with local signals (45/55) into a per-second 0-100 score; candidates are re-picked on it. Real run (same 40-min Speed Fortnite section): 15 moments, top fused 91/89/81; Gemini judged the chosen moment 8.8/10 (best so far). Text call cost logged as $0 (free tier).
- **Dead-air cutting** (`deadair.py`, Settings toggle): word-free stretches >= 2.5 s with no loud action and no on-camera reaction are cut (0.4 s kept each side), never below the minimum length. Unit-tested; on the real reaction clips there were no such gaps (game/trailer voices are transcribed too), so it changed nothing there.
- **Streamer always visible**: if a webcam is detected, a 'fit'/'full' plan becomes 'split'. Found because the director picked 'fit' (no webcam) on this run.
- **Per-clip webcam detection**: stream layouts change; this stream has 4 webcams and Speed's moved from top-left to bottom-left. The webcam is now detected inside each clip's window, the one nearest Gemini's "streamer cam" mark is chosen, and Gemini's rectangle is used when no clear border is found. Verified on frames: Speed on top for the whole clip.
- Timing (40-min section from disk): analysis 11 min (Whisper on 40 min), text score 1.5 min, judging 2 min, plan + render ~3 min. Job cost $0.130. AI review of the first (no-webcam) render 5.2/10; the re-render with Speed's webcam was not re-reviewed. 67 tests pass.

## Tools page: Edit guide (2026-11-15)
- Dashboard `/tools` (nav "Tools"): pick a finished job and get an edit guide instead of editing with the AI.
- Worker `clipper/guide.py`: `GET /tools/guide/{job}` turns the clip plan into a cut list (play order, stream timestamps with YouTube links, rough-cut times), a time-sorted edit sheet (zooms, on-screen text/emoji, sound effects, transitions, cuts, each with its reason), hook, layout with the webcam rectangle in pixels, caption words to highlight and fixes, music mood, plus every scored moment with its linked story beats. A still from the middle of each beat shows when the stream layout changes.
- Per beat (`clipper/beatcam.py`, cached in `analysis/guide_cams_{idx}.json`): every webcam that stays on screen is drawn on the beat's still; the one where the plan put the webcam is marked "use", else the biggest. Faces under 3.6% of the width (menu art, player cards) are ignored. People are not told apart by face (Vision image prints were not reliable), so beats with several cams say "check it is the right person". Tested on the Speed vs Dele Alli job: Speed marked in all 5 beats.
- `POST /tools/guide/{job}/clips/{idx}/export`: the untouched rough cut (beats joined in play order, so the sheet's times match), each beat with 1.5 s handles, speech `.srt` on the rough-cut timeline, `markers.csv`, `sheet.json`. Local ffmpeg only, about 20 s; nothing is re-planned, rendered or published.

## Edit guide runs before the AI edits (2026-11-15)
- `clipper/preguide.py`: when the director request is built (before the AI plans a clip), the same research as the Tools page runs for the chosen moment ($0, local): parts in story order with "N MINUTES LATER / EARLIER" jump labels, webcam per part (`beatcam.py`), layout per part, cold-open teaser (payoff peak), up to 2 zooms + 1 sound per part (reactions, screams, loud action), the loudest short line per part as on-screen text, caption words (loud + rare in this stream), dead air, hook/title options, music mood. Cached in `analysis/preguide_{idx}.json`.
- The director prompt gets it as "Edit guide prepared before editing" (follow it, deviate only with a reason).
- Render: `render.py` adds `layout.facecamTimeline` (webcam box per segment, output time) when the webcam moves between parts; `fastrender._cam_region` switches the crop per part for split and bubble. The Remotion fallback does not switch (uses `facecamBox`). The automatic "group" layout (2+ people on screen) still takes over when it triggers.
- Tools page shows "Guide the AI got before editing" with a tick for each suggestion the AI's plan used.
- Test 2026-11-15 (Speed vs Dele Alli, per-job limit raised to $0.30 by the owner): the guided plan used 17 of 22 guide suggestions (the old unguided plan: 7 of 22), came to 64.6 s after the parts were lengthened, but was rejected by the hook rule (the AI dropped the "..." that made the guide's hook a tease). The guide now marks which hook ideas pass `hookrules.check_hook` and tells the AI to keep the structure; text callouts need 3+ words with 2 real ones. The retry was blocked by the budget ($0.035 left, retry needs $0.04); the previous clip was restored.
- Test 2 (per-job limit $0.35, owner-approved): the guided plan passed on the 2nd attempt: 66 s, hook "Dele Alli made excuses against Speed..." (tease), 17 of 22 guide suggestions used (unguided plan: 7). Job total $0.322.
- Per-part webcam switching now only applies when each part shows exactly one webcam. With several people's webcams in a part, local face/colour comparisons could not tell Speed from Dele Alli (tested: wrong in 2 of 4 parts), so the guide tells the AI which webcams exist and that the streamer's is uncertain, and the renderer keeps the automatic "group" layout (everyone visible). `beatcam` merges duplicate detections of one person.
- Known: the group layout looks weak (dark top half, small circles); a reliable streamer pick per part probably needs the AI (it sees the video) to choose the webcam per part.
- AI webcam pick (2026-11-15): parts with several webcams get a still with the webcams boxed and numbered (`preguide.webcam_pictures`), sent to the director with the video; the director fills `layout.part_webcams` (one number per part). `knows_streamer`/`timeline` use it, so the renderer crops that webcam per part instead of the group layout. Test on Speed vs Dele Alli: the AI picked Speed in all 4 parts (3 had Dele's cam too), plan accepted first try ($0.037), 60.9 s, split layout with Speed on top. Spending limits set to the maximums by the owner ($5 job / $50 day / $500 total).

## Webcam tracking removed, layout templates added (2026-11-15)
- Removed on the owner's request (deleted, not switched off): the face-following "group" layout (`signals.participant_tracks`, fastrender group branch, `participantTracks` in renderer types), per-part webcam detection and switching (`beatcam.py`, preguide webcam research, `facecamTimeline`, `_cam_region`, numbered webcam pictures, `layout.part_webcams`), the Tools page per-beat webcam boxes, and `keep_streamer_visible` (which forced non-gaming videos into a 50/50 split). Split/bubble keep ONE fixed webcam rectangle per clip.
- Layout templates (`clipper/templates.py`, New job form "Layout template", `JobIn.template`, n8n Normalize): auto (AI picks), full (full screen + captions, no webcam), split_50, split_20. Enforced after the AI plan (`apply_template` in `save_edl`) and stated in the director prompt. `facecam_share` max raised to 0.5.
- Test: the FaZe Rug upload (d73f1546) re-rendered with "full": 3 clips full screen with captions; 2 passed QC, 1 qc_failed (loudness -16.9 LUFS, 1 caption word out of range).

## Look, length, sound timing, no music (2026-11-15)
- Hook: clean white Montserrat Black with a dark outline and soft shadow, last word in neon; no coloured box (fastrender `Hook` style).
- Captions: one palette for every style: white words, neon green (#7CFF4F) for the spoken word and highlighted words, red only for the first 1-2 `captions.emphasis` words (`RED_MAX`). Remotion fallback themes use the same colours.
- Length: with "AI decides" the director picks 20-90 s per clip (prompt), story finder asked for varied 20-90 s stories with build-up and reaction, candidate windows widened (40 s before / 25 s after, cap 130 s), story parts get 4 s of room to the next pause, `check()` default 15-92 s. NOT yet verified on a full run (the test job was deleted mid-analysis by someone else).
- Sound effects (`clipper/sfxtime.py`): each sound's loudest instant lands on the real action, found on a 10 ms grid of the audio (onset within +-0.6 s of the AI's time); sounds within 0.5 s of on-screen text/transitions lock to that visual instead. Impacts hit with the action, whooshes/risers build up before it.
- Music: off (Settings `music_enabled` default False and switched off live); the director is told no music.
- Fix: caption words are clamped to the rendered clip length (two clips had failed QC with "1 word out of range").
