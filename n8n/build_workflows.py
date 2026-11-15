"""Generate the n8n workflows for the gaming clipper.
Usage: python3 build_workflows.py <telegram_chat_id>   -> writes data/n8n_build/*.workflow.json
Credentials are referenced by ID only; no secret is written to these files."""
import json
import sys
import uuid
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "n8n_build"  # gitignored: contains your chat id
WORKER = "http://host.docker.internal:8787"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/{{ $json.model }}:generateContent"
CRED_TOKEN = {"httpHeaderAuth": {"id": "clipperWorkerTk1", "name": "Clipper worker token"}}
CRED_GEMINI = {"googlePalmApi": {"id": "FNSfjIWeCx9X7gZ9", "name": "Google Gemini(PaLM) Api account"}}
CRED_TG = {"telegramApi": {"id": "4HiGABEOPZHO5FDz", "name": "Telegram account"}}
IDS = {"main": "gclipMainFlow001", "assets": "gclipAssetsFlw01", "errors": "gclipErrorsFlw01", "regen": "gclipRegenFlow01", "maint": "gclipMaintFlow01", "publish": "gclipPublishFl01", "watch": "gclipWatchFlow01"}
JOB = "{{ $('Job ID').item.json.job_id }}"


class WF:
    def __init__(self, name, wid):
        self.name, self.id, self.nodes, self.conns, self.x = name, wid, [], {}, 0

    def node(self, name, typ, ver, params, pos, **extra):
        n = {"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, self.id + name)), "name": name, "type": typ,
             "typeVersion": ver, "position": list(pos), "parameters": params}
        n.update(extra)
        self.nodes.append(n)
        return name

    def link(self, a, b, out=0, inp=0):
        c = self.conns.setdefault(a, {"main": []})["main"]
        while len(c) <= out:
            c.append([])
        c[out].append({"node": b, "type": "main", "index": inp})

    def chain(self, *names):
        for a, b in zip(names, names[1:]):
            self.link(a, b)

    def dump(self, settings, fname):
        wf = {"id": self.id, "name": self.name, "nodes": self.nodes, "connections": self.conns, "active": False,
              "settings": {"executionOrder": "v1", **settings}, "pinData": {}, "tags": []}
        (OUT / fname).write_text(json.dumps(wf, indent=1, ensure_ascii=False))


def http(method, url, *, body=None, cred="token", timeout=60000, never_error=True, binary=False, batch_ms=0):
    p = {"method": method, "url": url, "options": {"timeout": timeout,
         "response": {"response": {"neverError": never_error, "responseFormat": "file" if binary else "autodetect"}}}}
    if batch_ms:
        p["options"]["batching"] = {"batch": {"batchSize": 1, "batchInterval": batch_ms}}
    if cred == "token":
        p.update(authentication="genericCredentialType", genericAuthType="httpHeaderAuth")
    elif cred == "gemini":
        p.update(authentication="predefinedCredentialType", nodeCredentialType="googlePalmApi")
    if body is not None:
        p.update(sendBody=True, specifyBody="json", jsonBody=body)
    return p


def creds(kind):
    return {"credentials": CRED_TOKEN if kind == "token" else CRED_GEMINI if kind == "gemini" else CRED_TG}


RETRY = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 5000}
H = "n8n-nodes-base.httpRequest"


def tg(text, chat):
    return {"chatId": chat, "text": text, "additionalFields": {"appendAttribution": False, "parse_mode": "HTML"}}


def main_wf(chat):
    w = WF("Gaming Clipper — MVP", IDS["main"])
    # ---- triggers ----
    w.node("Form", "n8n-nodes-base.formTrigger", 2.2, {
        "path": "gaming-clipper", "formTitle": "Gaming Clipper",
        "formDescription": "Submit a gaming video you own (or have rights to edit). Clips arrive in Telegram for review. Nothing is published.",
        "formFields": {"values": [
            {"fieldLabel": "Source", "fieldType": "dropdown", "requiredField": True,
             "fieldOptions": {"values": [{"option": "File in inbox folder"}, {"option": "YouTube URL (my own / CC-licensed video)"}, {"option": "Twitch VOD URL (finished stream)"}]}},
            {"fieldLabel": "Video URL", "placeholder": "https://youtube.com/watch?v=... or https://twitch.tv/videos/..."},
            {"fieldLabel": "Inbox filename", "placeholder": "my_stream.mp4 (placed in gaming-clipper/inbox/)"},
            {"fieldLabel": "Editing style", "fieldType": "dropdown", "requiredField": True,
             "fieldOptions": {"values": [{"option": "Viral Gaming"}, {"option": "Funny Moments"}, {"option": "Cinematic"}, {"option": "Fast-Paced"}, {"option": "Minimal"}]}},
            {"fieldLabel": "Title", "placeholder": "optional"},
            {"fieldLabel": "Rights", "fieldType": "dropdown", "requiredField": True,
             "fieldOptions": {"values": [{"option": "I own this video or have the rights to edit it"}]}},
        ]},
        "responseMode": "onReceived", "options": {"respondWithOptions": {"values": {"formSubmittedText": "Job submitted. You will get a Telegram message when clips are ready."}}},
    }, (0, 0), webhookId=str(uuid.uuid5(uuid.NAMESPACE_DNS, "gclip-form")))
    w.node("Webhook", "n8n-nodes-base.webhook", 2, {
        "httpMethod": "POST", "path": "gaming-clipper", "authentication": "headerAuth", "responseMode": "onReceived", "options": {}},
        (0, 220), webhookId=str(uuid.uuid5(uuid.NAMESPACE_DNS, "gclip-webhook")), **creds("token"))
    w.node("Normalize input", "n8n-nodes-base.code", 2, {"jsCode": """
const j = $json.body ?? $json;   // webhook puts payload in body; form at top level
const url = j['Video URL'] ?? j['YouTube URL'] ?? j.url ?? '';
const file = j['Inbox filename'] ?? j.filename ?? '';
const src = String(j.Source ?? j.source_type ?? '').toLowerCase();
const isTwitch = /^https?:[/][/](?:www[.]|m[.]|go[.])?twitch[.]tv[/]/i.test(String(url));
const source_type = src.startsWith('twitch') ? 'twitch' : src.startsWith('youtube') ? 'youtube' : src.startsWith('file') ? 'file' : (url ? (isTwitch ? 'twitch' : 'youtube') : 'file');
const rights = j.Rights ? String(j.Rights).startsWith('I own') : j.rights_confirmed === true;
const styleMap = { 'Viral Gaming': 'viral', 'Funny Moments': 'funny', 'Cinematic': 'cinematic', 'Fast-Paced': 'fast', 'Minimal': 'minimal' };
const style = styleMap[j['Editing style']] ?? (['viral','funny','cinematic','fast','minimal'].includes(j.style) ? j.style : 'viral');
return [{ json: { source_type, url: String(url).trim() || null, filename: file.trim() || null, title: (j.Title ?? j.title ?? '').trim() || null, rights_confirmed: rights, style, game: (j.game ?? j.Game ?? '').trim() || null, fresh: ['true','1','yes'].includes(String(j.fresh ?? '').toLowerCase()), min_seconds: Number(j.min_seconds) >= 8 ? Math.floor(Number(j.min_seconds)) : null, max_seconds: Number(j.max_seconds) >= 10 ? Math.floor(Number(j.max_seconds)) : null, clips: (Number(j.clips ?? j['Number of clips']) >= 1 ? Math.min(8, Math.floor(Number(j.clips ?? j['Number of clips']))) : null), captions: ['overlay','bar','off'].includes(j.captions) ? j.captions : null, engine: ['stories','moments'].includes(j.engine) ? j.engine : 'stories', template: ['auto','full','split_50','split_20','gta6_gameplay'].includes(j.template) ? j.template : 'auto' } }];
"""}, (220, 100))
    w.node("Create job", H, 4.2, http("POST", f"{WORKER}/jobs", body="={{ JSON.stringify($json) }}"), (440, 100), **creds("token"), **RETRY)
    w.node("Accepted?", "n8n-nodes-base.if", 2.2, {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"},
        "combinator": "and", "conditions": [{"leftValue": "={{ $json.job_id }}", "rightValue": "", "operator": {"type": "string", "operation": "notEmpty", "singleValue": True}}]}, "options": {}}, (660, 100))
    w.node("Tell rejected", "n8n-nodes-base.telegram", 1.2, tg("=❌ <b>Clip job rejected</b>\n{{ $json.detail ?? JSON.stringify($json) }}", chat), (880, 260), **creds("tg"))
    w.node("Job ID", "n8n-nodes-base.set", 3.4, {"assignments": {"assignments": [
        {"id": "a1", "name": "job_id", "value": "={{ $json.job_id }}", "type": "string"},
        {"id": "a2", "name": "deduplicated", "value": "={{ $json.deduplicated }}", "type": "boolean"}]}, "options": {}}, (880, 40))
    w.node("Tell started", "n8n-nodes-base.telegram", 1.2, tg("=🎬 Clip job <code>{{ $json.job_id.slice(0,8) }}</code> started{{ $json.deduplicated ? ' (same video as an earlier job: reusing its results)' : '' }}.", chat), (1100, -120), **creds("tg"))
    # ---- wait for analysis ----
    w.node("Wait analysis", "n8n-nodes-base.wait", 1.1, {"amount": 20}, (1100, 40), webhookId=str(uuid.uuid5(uuid.NAMESPACE_DNS, "gclip-w1")))
    w.node("Get job", H, 4.2, http("GET", f"={WORKER}/jobs/{JOB}"), (1320, 40), **creds("token"), **RETRY)
    w.node("Analysis state", "n8n-nodes-base.switch", 3.2, {"rules": {"values": [
        {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"}, "combinator": "or", "conditions": [
            {"leftValue": "={{ $json.status }}", "rightValue": "analyzed", "operator": {"type": "string", "operation": "equals"}},
            {"leftValue": "={{ $json.status }}", "rightValue": "ranking", "operator": {"type": "string", "operation": "equals"}}]}, "renameOutput": True, "outputKey": "ready"},
        {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"}, "combinator": "or", "conditions": [
            {"leftValue": "={{ $json.status }}", "rightValue": "failed", "operator": {"type": "string", "operation": "equals"}},
            {"leftValue": "={{ $json.elapsed_s }}", "rightValue": 7200, "operator": {"type": "number", "operation": "gt"}}]}, "renameOutput": True, "outputKey": "failed"},
        {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"}, "combinator": "or", "conditions": [
            {"leftValue": "={{ ['selected','done','no_highlights'].includes($json.status) }}", "rightValue": True, "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}, "renameOutput": True, "outputKey": "already done"},
    ]}, "options": {"fallbackOutput": "extra", "renameFallbackOutput": "waiting"}}, (1540, 40))
    w.node("Tell failed", "n8n-nodes-base.telegram", 1.2, tg("=⚠️ Clip job <code>{{ $json.job_id.slice(0,8) }}</code> failed\n{{ $json.error ?? 'timed out' }}\n{{ ($json.failed ?? []).map(f => f.stage + ': ' + f.error).join('\\n').slice(0, 800) }}", chat), (1760, 200), **creds("tg"))
    # ---- pass 2: Gemini ranking ----
    w.node("Rank requests", H, 4.2, http("GET", f"={WORKER}/jobs/{JOB}/rank-requests", timeout=120000), (1760, -60), **creds("token"), **RETRY, executeOnce=True)
    w.node("Has requests?", "n8n-nodes-base.if", 2.2, {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"},
        "combinator": "and", "conditions": [{"leftValue": "={{ $json.idx }}", "rightValue": "", "operator": {"type": "number", "operation": "exists", "singleValue": True}}]}, "options": {}}, (1980, -60), alwaysOutputData=False)
    w.node("Gemini: rank moment", H, 4.2, http("POST", "=" + GEMINI, body="={{ JSON.stringify($json.body) }}", cred="gemini", timeout=180000, never_error=False, batch_ms=7000),
           (2200, -140), **creds("gemini"), retryOnFail=True, maxTries=4, waitBetweenTries=5000, onError="continueRegularOutput")
    w.node("Save rank", H, 4.2, http("POST", f"={WORKER}/jobs/{JOB}/rank/{{{{ $('Has requests?').item.json.idx }}}}?pass={{{{ $('Has requests?').item.json.pass || 'screen' }}}}", body="={{ JSON.stringify($json) }}"),
           (2420, -140), **creds("token"), **RETRY)
    w.node("Select clips", H, 4.2, http("POST", f"={WORKER}/jobs/{JOB}/select"), (2640, -60), **creds("token"), **RETRY, executeOnce=True)
    w.node("Clips to items", "n8n-nodes-base.code", 2, {"jsCode": """
const sel = $input.first().json.selected ?? [];
if (!sel.length) return [{ json: { none: true, job_id: $('Job ID').first().json.job_id } }];
return sel.map(c => ({ json: c }));
"""}, (2860, -60))
    w.node("Any clips?", "n8n-nodes-base.if", 2.2, {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"},
        "combinator": "and", "conditions": [{"leftValue": "={{ $json.none !== true }}", "rightValue": True, "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}, "options": {}}, (3080, -60))
    w.node("Tell no highlights", "n8n-nodes-base.telegram", 1.2, tg("=🤷 Job <code>{{ $json.job_id.slice(0,8) }}</code>: no moment was strong enough to clip. Nothing rendered (quality over quantity).", chat), (3300, 120), **creds("tg"))
    # ---- per clip: director -> assets -> render ----
    w.node("Each clip", "n8n-nodes-base.splitInBatches", 3, {"options": {}}, (3300, -60))
    w.node("Director request", H, 4.2, http("GET", f"={WORKER}/jobs/{JOB}/clips/{{{{ $('Each clip').item.json.idx }}}}/director-request", timeout=180000), (3520, -60), **creds("token"), **RETRY)
    w.node("Gemini: creative director", H, 4.2, http("POST", "=" + GEMINI, body="={{ JSON.stringify($json.body) }}", cred="gemini", timeout=240000, never_error=False),
           (3740, -60), **creds("gemini"), retryOnFail=True, maxTries=4, waitBetweenTries=8000, onError="continueRegularOutput")
    w.node("Validate EDL", H, 4.2, http("POST", f"={WORKER}/jobs/{JOB}/clips/{{{{ $('Each clip').item.json.idx }}}}/edl", body="={{ JSON.stringify($json) }}"), (3960, -60), **creds("token"), **RETRY)
    w.node("EDL valid?", "n8n-nodes-base.switch", 3.2, {"rules": {"values": [
        {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"}, "combinator": "and", "conditions": [
            {"leftValue": "={{ $json.valid }}", "rightValue": True, "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}, "renameOutput": True, "outputKey": "valid"},
        {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"}, "combinator": "and", "conditions": [
            {"leftValue": "={{ $json.retry }}", "rightValue": True, "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}, "renameOutput": True, "outputKey": "repair"},
    ]}, "options": {"fallbackOutput": "extra", "renameFallbackOutput": "give up"}}, (4180, -60))
    w.node("Assets (sub-workflow)", "n8n-nodes-base.executeWorkflow", 1.2, {"workflowId": {"__rl": True, "mode": "id", "value": IDS["assets"]},
        "workflowInputs": {"mappingMode": "defineBelow", "value": {"job_id": f"={JOB}", "idx": "={{ $('Each clip').item.json.idx }}"},
                           "matchingColumns": [], "schema": [
                               {"id": "job_id", "displayName": "job_id", "required": False, "defaultMatch": False, "display": True, "canBeUsedToMatch": True, "type": "string", "removed": False},
                               {"id": "idx", "displayName": "idx", "required": False, "defaultMatch": False, "display": True, "canBeUsedToMatch": True, "type": "number", "removed": False}],
                           "attemptToConvertTypes": False, "convertFieldsToString": False}, "options": {"waitForSubWorkflow": True}}, (4400, -160))
    w.node("Start render", H, 4.2, http("POST", f"={WORKER}/jobs/{JOB}/clips/{{{{ $('Each clip').item.json.idx }}}}/render"), (4620, -160), **creds("token"), **RETRY)
    w.node("Wait render", "n8n-nodes-base.wait", 1.1, {"amount": 15}, (4840, -160), webhookId=str(uuid.uuid5(uuid.NAMESPACE_DNS, "gclip-w2")))
    w.node("Clip status", H, 4.2, http("GET", f"={WORKER}/jobs/{JOB}/clips/{{{{ $('Each clip').item.json.idx }}}}"), (5060, -160), **creds("token"), **RETRY)
    w.node("Render busy?", "n8n-nodes-base.if", 2.2, {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"},
        "combinator": "and", "conditions": [{"leftValue": "={{ $json.busy }}", "rightValue": True, "operator": {"type": "boolean", "operation": "true", "singleValue": True}},
                                              {"leftValue": "={{ $runIndex }}", "rightValue": 240, "operator": {"type": "number", "operation": "lt"}}]}, "options": {}}, (5280, -160))
    # ---- AI review of the finished clip (one bounded redo) ----
    CI = "{{ $('Each clip').item.json.idx }}"
    w.node("Review request", H, 4.2, http("GET", f"={WORKER}/jobs/{JOB}/clips/{CI}/review-request", timeout=180000), (5500, -160), **creds("token"), **RETRY)
    w.node("Needs review?", "n8n-nodes-base.if", 2.2, {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"},
        "combinator": "and", "conditions": [{"leftValue": "={{ $json.skip !== true && !!$json.body }}", "rightValue": True,
                                              "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}, "options": {}}, (5720, -160))
    w.node("Gemini: review clip", H, 4.2, http("POST", "=" + GEMINI, body="={{ JSON.stringify($json.body) }}", cred="gemini", timeout=180000, never_error=False),
           (5940, -240), **creds("gemini"), retryOnFail=True, maxTries=3, waitBetweenTries=5000, onError="continueRegularOutput")
    w.node("Save review", H, 4.2, http("POST", f"={WORKER}/jobs/{JOB}/clips/{CI}/ai-review", body="={{ JSON.stringify($json) }}"),
           (6160, -240), **creds("token"), **RETRY)
    w.node("Redo?", "n8n-nodes-base.if", 2.2, {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"},
        "combinator": "and", "conditions": [{"leftValue": "={{ $json.redo === true }}", "rightValue": True,
                                              "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}, "options": {}}, (6380, -240))
    w.node("Next clip", "n8n-nodes-base.noOp", 1, {}, (6600, -60))
    # ---- finish + delivery + human review ----
    w.node("Finish job", H, 4.2, http("POST", f"={WORKER}/jobs/{JOB}/finish"), (3520, -360), **creds("token"), **RETRY, executeOnce=True)
    w.node("Summary + review items", "n8n-nodes-base.code", 2, {"jsCode": """
const s = $input.first().json;
const esc = t => String(t ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const fmt = x => `${Math.floor(x/60)}:${String(Math.round(x%60)).padStart(2,'0')}`;
const ok = (s.clips ?? []).filter(c => c.status === 'ready_for_review');
const bad = (s.clips ?? []).filter(c => c.status !== 'ready_for_review');
let text = `✅ <b>Clip job ${s.job_id.slice(0,8)}</b> — ${esc(s.status)}\\n${ok.length} clip(s) ready for review, ${bad.length} failed.\\n` +
  `Processing: ${Math.round(s.elapsed_s ?? 0)}s · est. AI cost $${(s.cost_usd ?? 0).toFixed(4)}\\n`;
for (const c of s.clips ?? []) text += `\\n• <b>${esc(c.title)}</b> (${fmt(c.source_start)}–${fmt(c.source_end)}) quality ${c.quality.toFixed(1)}/10${c.ai_review_score != null ? ' · AI review ' + c.ai_review_score.toFixed(1) + '/10' + (c.ai_review_verdict && c.ai_review_verdict !== 'keep' ? ' (' + esc(c.ai_review_verdict) + ')' : '') : ''} · ${esc(c.status)}${c.qc_fails?.length ? ' [' + esc(c.qc_fails.join(', ')) + ']' : ''}`;
if (s.burned_captions) text += `\\n\\nℹ️ The source already has burned-in captions, so the editor skipped or moved ours.`;
text += `\\n\\nReview, regenerate and download on this Mac: ${s.view}`;
return [{ json: { summary: text.slice(0, 4000), clips: ok, job_id: s.job_id } }];
"""}, (3740, -360))
    w.node("Telegram summary", "n8n-nodes-base.telegram", 1.2, tg("={{ $json.summary }}", chat), (3960, -460), **creds("tg"))
    w.node("Review items", "n8n-nodes-base.splitOut", 1, {"fieldToSplitOut": "clips", "options": {}}, (3960, -300))
    w.node("Each review", "n8n-nodes-base.splitInBatches", 3, {"options": {}}, (4180, -300))
    w.node("Download clip", H, 4.2, http("GET", "={{ $json.file.replace('http://127.0.0.1:8787', '" + WORKER + "') }}", cred=None, timeout=120000, binary=True), (4400, -400), **RETRY)
    w.node("Send clip", "n8n-nodes-base.telegram", 1.2, {"operation": "sendVideo", "chatId": chat, "binaryData": True, "binaryPropertyName": "data",
        "additionalFields": {"caption": "={{ $('Each review').item.json.title }}\n{{ $('Each review').item.json.description }}".replace("\n", "\n"), "supports_streaming": True}},
        (4620, -400), **creds("tg"), retryOnFail=True, maxTries=2, waitBetweenTries=5000, onError="continueRegularOutput")

    w.link("Form", "Normalize input"); w.link("Webhook", "Normalize input")
    w.chain("Normalize input", "Create job", "Accepted?")
    w.link("Accepted?", "Job ID", 0); w.link("Accepted?", "Tell rejected", 1)
    w.link("Job ID", "Tell started"); w.link("Job ID", "Wait analysis")
    w.chain("Wait analysis", "Get job", "Analysis state")
    # transcript-first scoring: one cheap text-only Gemini call over the whole transcript, then candidates are re-picked
    w.node("Text score request", H, 4.2, http("GET", f"={WORKER}/jobs/{JOB}/text-score-request", timeout=120000), (1540, -300), **creds("token"), **RETRY)
    w.node("Text score on?", "n8n-nodes-base.if", 2.2, {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"},
        "combinator": "and", "conditions": [{"leftValue": "={{ !$json.skip && !!$json.body }}", "rightValue": True,
                                              "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}, "options": {}}, (1650, -300))
    w.node("Gemini: score transcript", H, 4.2, http("POST", "=" + GEMINI, body="={{ JSON.stringify($json.body) }}", cred="gemini", timeout=180000, never_error=False),
           (1760, -400), **creds("gemini"), retryOnFail=True, maxTries=3, waitBetweenTries=5000, onError="continueRegularOutput")
    w.node("Fallback check (text)", H, 4.2, http("POST", f"={WORKER}/jobs/{JOB}/llm/rescue?kind=textscore&idx=0",
           body="={{ JSON.stringify($json) }}", timeout=300000), (1870, -400), **creds("token"), **RETRY)
    w.node("Save text score", H, 4.2, http("POST", f"={WORKER}/jobs/{JOB}/text-score", body="={{ JSON.stringify($json) }}", timeout=600000),
           (1980, -400), **creds("token"), **RETRY)
    w.link("Analysis state", "Text score request", 0); w.link("Analysis state", "Tell failed", 1)
    w.chain("Text score request", "Text score on?")
    w.link("Text score on?", "Gemini: score transcript", 0); w.link("Text score on?", "Rank requests", 1)
    w.chain("Gemini: score transcript", "Fallback check (text)", "Save text score", "Rank requests")
    w.link("Analysis state", "Select clips", 2); w.link("Analysis state", "Wait analysis", 3)
    w.link("Rank requests", "Has requests?")
    w.link("Has requests?", "Gemini: rank moment", 0); w.link("Has requests?", "Select clips", 1)
    # screen pass -> judge pass (shortlist re-watched by the main model) -> worker answers {"done": true} -> select
    w.node("Fallback check (rank)", H, 4.2, http("POST", f"={WORKER}/jobs/{JOB}/llm/rescue?kind=rank&idx={{{{ $('Has requests?').item.json.idx }}}}&pass={{{{ $('Has requests?').item.json.pass || 'screen' }}}}",
           body="={{ JSON.stringify($json) }}", timeout=300000), (2310, -240), **creds("token"), **RETRY)
    w.chain("Gemini: rank moment", "Fallback check (rank)", "Save rank", "Rank requests")
    w.chain("Select clips", "Clips to items", "Any clips?")
    w.node("Trend hints", H, 4.2, http("POST", f"={WORKER}/jobs/{JOB}/trends", timeout=90000), (3190, -200), **creds("token"), **RETRY, executeOnce=True)
    w.link("Any clips?", "Trend hints", 0); w.link("Any clips?", "Tell no highlights", 1)
    w.node("Clips again", "n8n-nodes-base.code", 2, {"jsCode": "return $('Clips to items').all();"}, (3250, -60))
    w.chain("Trend hints", "Clips again", "Each clip")
    w.link("Each clip", "Finish job", 0); w.link("Each clip", "Director request", 1)
    w.node("Fallback check (director)", H, 4.2, http("POST", f"={WORKER}/jobs/{JOB}/llm/rescue?kind=director&idx={{{{ $('Each clip').item.json.idx }}}}",
           body="={{ JSON.stringify($json) }}", timeout=300000), (3850, 60), **creds("token"), **RETRY)
    w.chain("Director request", "Gemini: creative director", "Fallback check (director)", "Validate EDL", "EDL valid?")
    w.link("EDL valid?", "Assets (sub-workflow)", 0); w.link("EDL valid?", "Director request", 1); w.link("EDL valid?", "Next clip", 2)
    w.chain("Assets (sub-workflow)", "Start render", "Wait render", "Clip status", "Render busy?")
    # optional moderation (worker answers {"skip": true} when it is off in Settings)
    w.node("Moderation request", H, 4.2, http("GET", f"={WORKER}/jobs/{JOB}/clips/{CI}/moderation-request", timeout=180000), (5390, -40), **creds("token"), **RETRY)
    w.node("Moderation on?", "n8n-nodes-base.if", 2.2, {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"},
        "combinator": "and", "conditions": [{"leftValue": "={{ $json.skip !== true && !!$json.body }}", "rightValue": True,
                                              "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}, "options": {}}, (5500, -40))
    w.node("Gemini: moderate clip", H, 4.2, http("POST", "=" + GEMINI, body="={{ JSON.stringify($json.body) }}", cred="gemini", timeout=180000, never_error=False),
           (5610, 60), **creds("gemini"), retryOnFail=True, maxTries=3, waitBetweenTries=5000, onError="continueRegularOutput")
    w.node("Fallback check (moderation)", H, 4.2, http("POST", f"={WORKER}/jobs/{JOB}/llm/rescue?kind=moderation&idx={CI}",
           body="={{ JSON.stringify($json) }}", timeout=300000), (5720, 60), **creds("token"), **RETRY)
    w.node("Save moderation", H, 4.2, http("POST", f"={WORKER}/jobs/{JOB}/clips/{CI}/moderation", body="={{ JSON.stringify($json) }}"),
           (5830, 60), **creds("token"), **RETRY)
    w.link("Render busy?", "Wait render", 0); w.link("Render busy?", "Moderation request", 1)
    w.chain("Moderation request", "Moderation on?")
    w.link("Moderation on?", "Gemini: moderate clip", 0); w.link("Moderation on?", "Review request", 1)
    w.chain("Gemini: moderate clip", "Fallback check (moderation)", "Save moderation", "Review request")
    w.chain("Review request", "Needs review?")
    w.link("Needs review?", "Gemini: review clip", 0); w.link("Needs review?", "Next clip", 1)
    w.node("Fallback check (review)", H, 4.2, http("POST", f"={WORKER}/jobs/{JOB}/llm/rescue?kind=review&idx={CI}",
           body="={{ JSON.stringify($json) }}", timeout=300000), (6050, -340), **creds("token"), **RETRY)
    w.chain("Gemini: review clip", "Fallback check (review)", "Save review", "Redo?")
    w.link("Redo?", "Director request", 0); w.link("Redo?", "Next clip", 1)
    w.link("Next clip", "Each clip")
    w.chain("Finish job", "Summary + review items")
    w.link("Summary + review items", "Telegram summary"); w.link("Summary + review items", "Review items")
    w.link("Review items", "Each review")
    w.link("Each review", "Download clip", 1)
    w.chain("Download clip", "Send clip", "Each review")
    w.dump({"errorWorkflow": IDS["errors"], "executionTimeout": 14400, "saveManualExecutions": True, "callerPolicy": "workflowsFromSameOwner"}, "gaming-clipper-main.workflow.json")


def assets_wf():
    w = WF("Gaming Clipper — Assets", IDS["assets"])
    w.node("Called by main", "n8n-nodes-base.executeWorkflowTrigger", 1.1, {"workflowInputs": {"values": [{"name": "job_id"}, {"name": "idx", "type": "number"}]}}, (0, 0))
    w.node("Resolve licensed assets", H, 4.2, http("POST", f"={WORKER}/jobs/{{{{ $json.job_id }}}}/clips/{{{{ $json.idx }}}}/assets", timeout=120000), (220, 0), **creds("token"), **RETRY)
    w.node("Asset report", "n8n-nodes-base.code", 2, {"jsCode": """
const a = $json;
const r = $('Resolve licensed assets').first().json;
return [{ json: { assets: (r.manifest ?? []).length, dropped: r.dropped ?? [], licences: [...new Set((r.manifest ?? []).map(m => m.license))], music: a.track ?? null, music_reason: a.reason ?? null } }];
"""}, (440, 0))
    w.node("Pick music", H, 4.2, http("POST", f"={WORKER}/jobs/{{{{ $('Called by main').item.json.job_id }}}}/clips/{{{{ $('Called by main').item.json.idx }}}}/music", timeout=120000),
           (330, 0), **creds("token"), **RETRY)
    w.chain("Called by main", "Resolve licensed assets", "Pick music", "Asset report")
    w.dump({"errorWorkflow": IDS["errors"], "callerPolicy": "workflowsFromSameOwner"}, "gaming-clipper-assets.workflow.json")


def publish_wf(chat):
    """Owner-triggered only (dashboard button -> this webhook). send = held draft at the provider; publish = make it live."""
    w = WF("Gaming Clipper — Publish", IDS["publish"])
    w.node("Publish webhook", "n8n-nodes-base.webhook", 2, {"httpMethod": "POST", "path": "gaming-clipper-publish", "authentication": "headerAuth",
        "responseMode": "lastNode", "options": {}}, (0, 0), webhookId=str(uuid.uuid5(uuid.NAMESPACE_DNS, "gclip-publish")), **creds("token"))
    w.node("Check request", "n8n-nodes-base.code", 2, {"jsCode": """
const b = $json.body ?? $json;
if (!/^[0-9a-f-]{36}$/.test(String(b.post_id)) || !['send', 'publish'].includes(b.action)) throw new Error('invalid publish request');
return [{ json: { post_id: String(b.post_id), action: b.action } }];
"""}, (220, 0))
    w.node("Run action", H, 4.2, http("POST", f"={WORKER}/dashboard/posts/{{{{ $json.post_id }}}}/action", body="={{ JSON.stringify({ action: $json.action }) }}", timeout=900000),
           (440, 0), **creds("token"))
    w.node("Tell result", "n8n-nodes-base.telegram", 1.2, tg("""={{ $json.detail ? '⚠️ ' + ($('Check request').item.json.action === 'publish' ? 'Publish' : 'Draft') + ' failed: ' + String($json.detail).slice(0, 300)
  : $('Check request').item.json.action === 'publish' ? '✅ Published on ' + $json.platform + ': ' + ($json.title || $json.caption || '').slice(0, 80)
  : '📤 Draft held at ' + $json.provider + ' for ' + $json.platform + ' (not live yet): ' + ($json.title || $json.caption || '').slice(0, 80) }}""", chat),
           (660, -100), **creds("tg"), retryOnFail=True, maxTries=2, waitBetweenTries=5000, onError="continueRegularOutput")
    w.node("Result", "n8n-nodes-base.code", 2, {"jsCode": "return [{ json: $('Run action').first().json }];"}, (880, 0))
    w.chain("Publish webhook", "Check request", "Run action", "Tell result", "Result")
    w.dump({"errorWorkflow": IDS["errors"], "callerPolicy": "workflowsFromSameOwner"}, "gaming-clipper-publish.workflow.json")


def errors_wf(chat):
    w = WF("Gaming Clipper — Errors", IDS["errors"])
    w.node("On error", "n8n-nodes-base.errorTrigger", 1, {}, (0, 0))
    w.node("Alert", "n8n-nodes-base.telegram", 1.2, tg("=🚨 <b>{{ $json.workflow.name }}</b> failed\nNode: {{ $json.execution.lastNodeExecuted }}\n{{ String($json.execution.error?.message ?? '').slice(0, 600) }}\nExecution: {{ $json.execution.id }}", chat), (220, 0), **creds("tg"))
    w.chain("On error", "Alert")
    w.dump({}, "gaming-clipper-errors.workflow.json")



def regen_wf(chat):
    w = WF("Gaming Clipper — Regenerate clip", IDS["regen"])
    R = "{{ $('Regen input').item.json.job_id }}"
    I = "{{ $('Regen input').item.json.idx }}"
    w.node("Regenerate webhook", "n8n-nodes-base.webhook", 2, {"httpMethod": "POST", "path": "gaming-clipper-regen", "authentication": "headerAuth",
        "responseMode": "onReceived", "options": {}}, (0, 0), webhookId=str(uuid.uuid5(uuid.NAMESPACE_DNS, "gclip-regen")), **creds("token"))
    w.node("Regen input", "n8n-nodes-base.code", 2, {"jsCode": """
const b = $json.body ?? $json;
if (!/^[0-9a-f-]{36}$/.test(String(b.job_id)) || !Number.isInteger(Number(b.idx))) throw new Error('invalid regenerate payload');
return [{ json: { job_id: String(b.job_id), idx: Number(b.idx) } }];
"""}, (220, 0))
    w.node("Director request", H, 4.2, http("GET", f"={WORKER}/jobs/{R}/clips/{I}/director-request", timeout=180000), (440, 0), **creds("token"), **RETRY)
    w.node("Gemini: creative director", H, 4.2, http("POST", "=" + GEMINI, body="={{ JSON.stringify($json.body) }}", cred="gemini", timeout=240000, never_error=False),
           (660, 0), **creds("gemini"), retryOnFail=True, maxTries=4, waitBetweenTries=8000, onError="continueRegularOutput")
    w.node("Validate EDL", H, 4.2, http("POST", f"={WORKER}/jobs/{R}/clips/{I}/edl", body="={{ JSON.stringify($json) }}"), (880, 0), **creds("token"), **RETRY)
    w.node("EDL valid?", "n8n-nodes-base.switch", 3.2, {"rules": {"values": [
        {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"}, "combinator": "and", "conditions": [
            {"leftValue": "={{ $json.valid }}", "rightValue": True, "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}, "renameOutput": True, "outputKey": "valid"},
        {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"}, "combinator": "and", "conditions": [
            {"leftValue": "={{ $json.retry }}", "rightValue": True, "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}, "renameOutput": True, "outputKey": "repair"},
    ]}, "options": {"fallbackOutput": "extra", "renameFallbackOutput": "give up"}}, (1100, 0))
    w.node("Tell plan failed", "n8n-nodes-base.telegram", 1.2, tg("=⚠️ Regenerate: the AI edit plan was rejected 3 times for clip {{ $('Regen input').item.json.idx }}.\n{{ ($json.errors ?? []).slice(0,4).join('\\n') }}", chat), (1320, 200), **creds("tg"))
    w.node("Assets (sub-workflow)", "n8n-nodes-base.executeWorkflow", 1.2, {"workflowId": {"__rl": True, "mode": "id", "value": IDS["assets"]},
        "workflowInputs": {"mappingMode": "defineBelow", "value": {"job_id": f"={R}", "idx": f"={I}"}, "matchingColumns": [], "schema": [
            {"id": "job_id", "displayName": "job_id", "required": False, "defaultMatch": False, "display": True, "canBeUsedToMatch": True, "type": "string", "removed": False},
            {"id": "idx", "displayName": "idx", "required": False, "defaultMatch": False, "display": True, "canBeUsedToMatch": True, "type": "number", "removed": False}],
            "attemptToConvertTypes": False, "convertFieldsToString": False}, "options": {"waitForSubWorkflow": True}}, (1320, -120))
    w.node("Start render", H, 4.2, http("POST", f"={WORKER}/jobs/{R}/clips/{I}/render"), (1540, -120), **creds("token"), **RETRY)
    w.node("Wait render", "n8n-nodes-base.wait", 1.1, {"amount": 15}, (1760, -120), webhookId=str(uuid.uuid5(uuid.NAMESPACE_DNS, "gclip-rw")))
    w.node("Clip status", H, 4.2, http("GET", f"={WORKER}/jobs/{R}/clips/{I}"), (1980, -120), **creds("token"), **RETRY)
    w.node("Render busy?", "n8n-nodes-base.if", 2.2, {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"},
        "combinator": "and", "conditions": [{"leftValue": "={{ $json.busy }}", "rightValue": True, "operator": {"type": "boolean", "operation": "true", "singleValue": True}},
                                              {"leftValue": "={{ $runIndex }}", "rightValue": 240, "operator": {"type": "number", "operation": "lt"}}]}, "options": {}}, (2200, -120))
    w.node("QC passed?", "n8n-nodes-base.if", 2.2, {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"},
        "combinator": "and", "conditions": [{"leftValue": "={{ $json.status }}", "rightValue": "ready_for_review", "operator": {"type": "string", "operation": "equals"}}]}, "options": {}}, (2420, -40))
    w.node("Tell QC failed", "n8n-nodes-base.telegram", 1.2, tg("=⚠️ Regenerated clip {{ $json.idx }} failed QC: {{ ($json.qc?.fails ?? [$json.status]).join(', ') }}", chat), (2640, 80), **creds("tg"))
    w.node("Download clip", H, 4.2, http("GET", "={{ $json.file.replace('http://127.0.0.1:8787', '" + WORKER + "') }}", cred=None, timeout=120000, binary=True), (2640, -140), **RETRY)
    w.node("Send clip", "n8n-nodes-base.telegram", 1.2, {"operation": "sendVideo", "chatId": chat, "binaryData": True, "binaryPropertyName": "data",
        "additionalFields": {"caption": "=↻ Regenerated ({{ $('Clip status').item.json.edl.style }}): {{ $('Clip status').item.json.edl.title }}\nReview on this Mac: http://127.0.0.1:8787/jobs/{{ $('Regen input').item.json.job_id }}/view", "supports_streaming": True}},
        (2860, -140), **creds("tg"), retryOnFail=True, maxTries=2, waitBetweenTries=5000, onError="continueRegularOutput")
    w.node("Fallback check (director)", H, 4.2, http("POST", f"={WORKER}/jobs/{R}/llm/rescue?kind=director&idx={I}",
           body="={{ JSON.stringify($json) }}", timeout=300000), (1210, -120), **creds("token"), **RETRY)
    w.chain("Regenerate webhook", "Regen input", "Director request", "Gemini: creative director", "Fallback check (director)", "Validate EDL", "EDL valid?")
    w.link("EDL valid?", "Assets (sub-workflow)", 0); w.link("EDL valid?", "Director request", 1); w.link("EDL valid?", "Tell plan failed", 2)
    w.chain("Assets (sub-workflow)", "Start render", "Wait render", "Clip status", "Render busy?")
    w.link("Render busy?", "Wait render", 0); w.link("Render busy?", "QC passed?", 1)
    w.link("QC passed?", "Download clip", 0); w.link("QC passed?", "Tell QC failed", 1)
    w.link("Download clip", "Send clip")
    w.dump({"errorWorkflow": IDS["errors"], "executionTimeout": 3600, "callerPolicy": "workflowsFromSameOwner"}, "gaming-clipper-regen.workflow.json")


def maint_wf(chat):
    w = WF("Gaming Clipper — Maintenance", IDS["maint"])
    w.node("Daily 04:00", "n8n-nodes-base.scheduleTrigger", 1.2, {"rule": {"interval": [{"field": "cronExpression", "expression": "0 4 * * *"}]}}, (0, 0))
    w.node("Cleanup old jobs", H, 4.2, http("POST", f"{WORKER}/cleanup", timeout=300000), (220, 0), **creds("token"), **RETRY)
    w.node("Anything freed?", "n8n-nodes-base.if", 2.2, {"conditions": {"options": {"version": 2, "caseSensitive": True, "typeValidation": "loose"},
        "combinator": "and", "conditions": [{"leftValue": "={{ $json.expired_jobs }}", "rightValue": 0, "operator": {"type": "number", "operation": "gt"}}]}, "options": {}}, (440, 0))
    w.node("Report", "n8n-nodes-base.telegram", 1.2, tg("=🧹 Clipper cleanup: {{ $json.expired_jobs }} old job(s) removed, {{ $json.freed_mb }} MB freed.", chat), (660, -60), **creds("tg"))
    w.chain("Daily 04:00", "Cleanup old jobs", "Anything freed?")
    w.link("Anything freed?", "Report", 0)
    w.dump({"errorWorkflow": IDS["errors"]}, "gaming-clipper-maintenance.workflow.json")

def watch_wf(chat):
    """Every 30 min: new videos on the channels in Settings -> one Telegram message each, with a link that opens a
    pre-filled New job form. Notify-only: nothing is downloaded or clipped until the owner starts the job."""
    w = WF("Gaming Clipper — Channel watch", IDS["watch"])
    w.node("Every 30 min", "n8n-nodes-base.scheduleTrigger", 1.2, {"rule": {"interval": [{"field": "minutes", "minutesInterval": 30}]}}, (0, 0))
    w.node("Check channels", H, 4.2, http("POST", f"{WORKER}/watch/check", timeout=120000), (220, 0), **creds("token"), **RETRY)
    w.node("One item per new video", "n8n-nodes-base.code", 2, {"jsCode": "return ($input.first().json.new || []).map(v => ({ json: v }));"}, (440, 0))
    w.node("Tell new video", "n8n-nodes-base.telegram", 1.2, tg(
        "=📺 <b>New on {{ $json.channel }}</b>\n{{ $json.title }}\n{{ $json.url }}\n\n✂️ <a href=\"{{ $json.clip_link }}\">Clip it</a> (opens the New job form on your computer)", chat),
        (660, 0), **creds("tg"))
    w.chain("Every 30 min", "Check channels", "One item per new video", "Tell new video")
    w.dump({"errorWorkflow": IDS["errors"]}, "gaming-clipper-watch.workflow.json")


if __name__ == "__main__":
    chat = sys.argv[1]
    OUT.mkdir(parents=True, exist_ok=True)
    main_wf(chat); assets_wf(); errors_wf(chat); regen_wf(chat); maint_wf(chat); publish_wf(chat); watch_wf(chat)
    print("written")
