"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useData } from "@/lib/api";
import { Back, Check, Sparkle, Upload } from "./icons";
import { browserSender, uploadChunked } from "@/lib/chunked-upload";

type Setup = { inbox: string[]; styles: Array<{ id: string; label: string }> };
const LENGTHS = [
  { id: "auto", label: "AI decides", min: null, max: null },
  { id: "30-60", label: "30–60 s", min: 30, max: 60 },
  { id: "60-90", label: "60–90 s", min: 60, max: 90 },
  { id: "90-120", label: "90–120 s", min: 90, max: 120 },
  { id: "custom", label: "Custom", min: null, max: null },
] as const;
const CLIP_COUNTS = [{ id: "auto", label: "AI decides" }, { id: "1", label: "1" }, { id: "2", label: "2" }, { id: "3", label: "3" }, { id: "5", label: "5" }, { id: "custom", label: "Custom" }];
const MODES = [
  { id: "viral", label: "Viral moments", hint: "The funniest and most intense moments, edited with energy: smart cuts, zooms, sounds and captions." },
  { id: "continuous", label: "Continuous gameplay", hint: "Keeps the story flowing for story-mode games. Only silence, loading screens and awkward pauses are cut." },
  { id: "highlights", label: "Highlights", hint: "Combines related moments of the same fight, joke or objective into one coherent clip." },
  { id: "custom", label: "Custom", hint: "You tell the AI exactly how to edit. Everything below still applies." },
] as const;
const LEVELS = [{ id: "off", label: "Off" }, { id: "low", label: "Low" }, { id: "medium", label: "Medium" }, { id: "high", label: "High" }];
const EDITS = [{ id: "minimal", label: "Minimal" }, { id: "balanced", label: "Balanced" }, { id: "dynamic", label: "Dynamic" }];
const DEFAULTS: Record<string, { edit: string; silence: string; zoom: string; sfx: string; length: string }> = {
  viral: { edit: "balanced", silence: "medium", zoom: "medium", sfx: "medium", length: "auto" },
  continuous: { edit: "minimal", silence: "low", zoom: "low", sfx: "off", length: "60-90" },
  highlights: { edit: "balanced", silence: "medium", zoom: "low", sfx: "low", length: "auto" },
  custom: { edit: "balanced", silence: "medium", zoom: "medium", sfx: "medium", length: "auto" },
};
const CONTENT_TYPES = [["auto", "Auto-detect"], ["gameplay", "Gameplay"], ["stream", "Stream"], ["reaction", "Reaction"], ["podcast", "Podcast"], ["other", "Other"]];

function Seg({ name, value, onChange, options, label }: { name: string; value: string; onChange: (v: string) => void; options: ReadonlyArray<{ id: string; label: string }>; label: string }) {
  return <div className="segmented" role="radiogroup" aria-label={label}>{options.map(o => (
    <label key={o.id}><input type="radio" name={name} value={o.id} checked={value === o.id} onChange={() => onChange(o.id)}/><span>{o.label}</span></label>
  ))}</div>;
}

type Up = { pct: number; loaded: number; total: number; speed: number; eta: number; phase: "uploading" | "saving" };

const mb = (n: number) => n >= 1e9 ? `${(n / 1e9).toFixed(2)} GB` : `${Math.round(n / 1e6)} MB`;
const eta = (s: number) => !isFinite(s) || s <= 0 ? "" : s < 90 ? `${Math.ceil(s)} s left` : `${Math.ceil(s / 60)} min left`;

function uploadVideo(file: File, onProgress: (u: Up) => void): Promise<string> {
  // Sent as 16 MB pieces (lib/chunked-upload.ts): Runpod's web address cuts any single request after ~100 s / ~100 MB.
  const t0 = Date.now();
  let base = -1;  // bytes already on the Pod before this attempt started (a resumed upload): speed counts only what we send now
  return uploadChunked(file, browserSender, p => {
    if (base < 0) base = p.loaded;
    const speed = Math.max(1, (p.loaded - base) / Math.max(0.5, (Date.now() - t0) / 1000));
    onProgress({ pct: Math.round((p.loaded / p.total) * 100), loaded: p.loaded, total: p.total, speed, eta: (p.total - p.loaded) / speed, phase: p.phase });
  });
}

async function uploadLogo(file: File): Promise<string> {
  const response = await fetch("/api/upload-logo", { method: "POST", headers: { "x-filename": encodeURIComponent(file.name) }, body: file });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body.slug) throw new Error(body.error || "Logo upload failed");
  return body.slug;
}

const TEMPLATES = [
  { id: "auto", label: "Auto (AI picks)" },
  { id: "full", label: "Full screen + captions" },
  { id: "split_50", label: "Split 50/50" },
  { id: "split_20", label: "Split 20/80" },
  { id: "gta6_gameplay", label: "GTA 6 — Gameplay Focus" },
  { id: "gta_cam", label: "GTA VI — Framed reaction cam" },
  { id: "gta_hook", label: "GTA VI — Widescreen + hook card" },
  { id: "gta_mix", label: "GTA VI — AI mix (a layout per clip)" },
];

export function NewJob() {
  const router = useRouter();
  const setup = useData<Setup>("dashboard/jobs");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [fileName, setFileName] = useState("");
  const [mode, setMode] = useState("viral");
  const [opt, setOpt] = useState(DEFAULTS.viral);
  const [length, setLength] = useState("auto");
  const [count, setCount] = useState("auto");
  const [customCount, setCustomCount] = useState("4");
  const [customLen, setCustomLen] = useState({ min: "45", max: "90" });
  const [captions, setCaptions] = useState("overlay");
  const [template, setTemplate] = useState("auto");
  const [clipStrategy, setClipStrategy] = useState("single");
  const [look, setLook] = useState("pop");
  const pickTemplate = (value: string) => {
    setTemplate(value);
    if (value === "gta6_gameplay" || value === "gta_cam" || value === "gta_hook" || value === "gta_mix") {
      setCaptions("overlay");   // captions stay on: karaoke, right under the webcam card
      setLook("karaoke");
      setOpt(o => ({ ...o, zoom: "off", sfx: "off" }));
    } else setClipStrategy("single");
  };
  const setO = (k: string) => (v: string) => setOpt(o => ({ ...o, [k]: v }));
  const pickMode = (m: string) => { setMode(m); setOpt(DEFAULTS[m]); setLength(DEFAULTS[m].length); setLook(m === "continuous" ? "karaoke" : "pop"); };
  const [over, setOver] = useState(false);
  const [up, setUp] = useState<Up | null>(null);
  const [starting, setStarting] = useState(false);
  const [pre, setPre] = useState({ url: "", title: "" });  // pre-filled from a channel-watch link (?url=…&title=…)
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const url = q.get("url") || "";
    if (/^https:\/\/(?:www\.|m\.|go\.)?(?:youtube\.com|youtu\.be|twitch\.tv)\//.test(url)) setPre({ url, title: (q.get("title") || "").slice(0, 120) });
  }, []);


  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSubmitting(true); setError("");
    const form = new FormData(event.currentTarget);
    const file = form.get("file");
    const logoFile = form.get("logo_file");
    form.delete("file");
    form.delete("logo_file");
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);  // closing the tab mid-upload would lose it
    if (file instanceof File && file.size) {  // stream the video to the inbox first, then start the job by name
      try { form.set("inbox", await uploadVideo(file, setUp)); form.set("fresh", "1"); }  // a new upload is always a new job
      catch (reason) { window.removeEventListener("beforeunload", warn); setSubmitting(false); setUp(null); setError(reason instanceof Error ? reason.message : "Upload failed"); return; }
      setUp(null);
    }
    if (template === "gta6_gameplay" && logoFile instanceof File && logoFile.size) {
      try { form.set("game_logo", await uploadLogo(logoFile)); }
      catch (reason) { window.removeEventListener("beforeunload", warn); setSubmitting(false); setError(reason instanceof Error ? reason.message : "Logo upload failed"); return; }
    }
    setStarting(true);
    form.set("fresh", "1");  // every submit is a deliberate new job, so a different mode on the same video is never skipped
    const l = LENGTHS.find(x => x.id === length);
    if (clipStrategy === "gta_launch_mix") { form.delete("min_seconds"); form.delete("max_seconds"); }
    else if (length === "custom") { form.set("min_seconds", customLen.min); form.set("max_seconds", customLen.max); }
    else if (l?.min) { form.set("min_seconds", String(l.min)); form.set("max_seconds", String(l.max)); }
    form.set("clips", count === "custom" ? customCount : count === "auto" ? "" : count);
    const response = await fetch("/api/submit", { method: "POST", body: form });
    const body = await response.json().catch(() => ({}));
    window.removeEventListener("beforeunload", warn);
    if (!response.ok) { setSubmitting(false); setStarting(false); setError(body.error || body.detail || "Could not create the job"); return; }
    router.push("/jobs?started=1");
  }

  return <>
    <header className="page-head"><div><Link href="/" className="back"><Back size={16}/>Overview</Link><p className="eyebrow">New job</p><h1>Turn one video into clips</h1>
      <p>Add a YouTube video, finished Twitch VOD, or local file. The AI scans the full source for chat spikes, screams, reactions and connected story moments, then edits the strongest clips for your review.</p></div></header>
    {error && <div className="notice error" role="alert">{error}</div>}
    <form onSubmit={submit} className="form">
      <section className="card card-pad form-card">
        <div><h2><span className="step-num">1</span>Video</h2><p>A YouTube video, finished Twitch VOD, local file, or one already in the inbox folder. One source is enough.</p></div>
        <div className="fields">
          <label className="field wide"><span>YouTube or Twitch VOD link</span><input name="url" type="url" inputMode="url" placeholder="https://www.youtube.com/watch?v=… or https://www.twitch.tv/videos/…" key={pre.url} defaultValue={pre.url}/>
            <small>For Twitch, paste a completed VOD—not a channel page that is still live. Replay chat is analyzed when Twitch makes it available.</small></label>
          <div className="or wide">or a local file</div>
          <label className={`dropzone ${over ? "over" : ""}`} onDragOver={() => setOver(true)} onDragLeave={() => setOver(false)} onDrop={() => setOver(false)}>
            <Upload size={26}/><b>{fileName || "Choose or drop a video"}</b><small>MP4, MOV, MKV or WebM</small>
            <input name="file" type="file" accept="video/*" onChange={e => setFileName(e.target.files?.[0]?.name || "")}/>
          </label>
          <label className="field"><span>From the inbox folder</span>
            <select name="inbox" defaultValue=""><option value="">Choose a file</option>{setup.data?.inbox.map(name => <option key={name}>{name}</option>)}</select>
            <small>Files placed in gaming-clipper/inbox/</small></label>
        </div>
      </section>

      <section className="card card-pad form-card">
        <div><h2><span className="step-num">2</span>Edit type</h2><p>Pick what kind of video this is. The AI edits very differently for a funny moment than for a story-mode playthrough.</p></div>
        <div className="fields">
          <label className="field wide"><span>Title <small>(optional)</small></span><input name="title" placeholder="e.g. GTA 5 story mission, part 2" key={pre.title} defaultValue={pre.title}/></label>
          <div className="field wide"><span>Mode</span>
            <Seg name="mode" value={mode} onChange={pickMode} options={MODES} label="Mode"/>
            <small>{MODES.find(m => m.id === mode)?.hint}</small></div>
          {mode === "custom" && <label className="field wide"><span>How should the AI edit it?</span>
            <textarea name="instructions" rows={3} maxLength={600} placeholder="e.g. Keep full conversations, cut only the driving, no sound effects, end each clip on a cliffhanger."/></label>}
          <label className="field"><span>Content type</span>
            <select name="content_type" defaultValue="auto">{CONTENT_TYPES.map(([id, label]) => <option value={id} key={id}>{label}</option>)}</select></label>
          <div className="field"><span>Number of clips</span>
            <Seg name="clips_choice" value={count} onChange={setCount} options={CLIP_COUNTS} label="Number of clips"/>
            {count === "custom" && <input type="number" min={1} max={12} value={customCount} onChange={e => setCustomCount(e.target.value)} aria-label="Custom number of clips"/>}</div>
          <div className="field wide"><span>Clip length</span>
            <Seg name="length_choice" value={length} onChange={setLength} options={LENGTHS} label="Clip length"/>
            {length === "custom" && <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input type="number" min={8} max={150} value={customLen.min} onChange={e => setCustomLen(v => ({ ...v, min: e.target.value }))} aria-label="Shortest clip in seconds" style={{ width: 90 }}/> to
              <input type="number" min={10} max={180} value={customLen.max} onChange={e => setCustomLen(v => ({ ...v, max: e.target.value }))} aria-label="Longest clip in seconds" style={{ width: 90 }}/> seconds</div>}
            <small>The AI may run a clip up to the longest value when the scene needs it.</small></div>
        </div>
      </section>

      <section className="card card-pad form-card">
        <div><h2><span className="step-num">3</span>Look and feel</h2><p>Set by the mode above. Change anything for this job only.</p></div>
        <div className="fields">
          <div className="field wide"><span>Layout</span>
            <div className="segmented tmpl" role="radiogroup" aria-label="Layout">{TEMPLATES.map(t => (
              <label key={t.id}><input type="radio" name="template" value={t.id} checked={template === t.id} onChange={() => pickTemplate(t.id)}/>
                <span><i className={`tmpl-pic ${t.id}`} aria-hidden="true"><b/><b/></i>{t.label}</span></label>
            ))}</div>
            <small>{template === "gta6_gameplay" ? "Wide, centred gameplay on a blurred copy of the video (no black bars), the streamer's webcam in a card at the top centre and your captions right under it. Clips without a webcam show the gameplay only." : "Auto adapts to the footage and keeps the gameplay visible. Split puts the webcam on top."}</small></div>
          {(template === "gta_cam" || template === "gta_hook" || template === "gta_mix") && <div className="template-settings wide" aria-label="GTA VI preset settings">
            <div className="template-controls">
              <label className="field"><span>Header text</span><input name="header_text" defaultValue="SPEED ×" maxLength={40}/><small>Shown big at the top, next to the GTA VI logo. Clear it to show only the logo.</small></label>
              {template === "gta_hook" && <label className="field"><span>Hook text (optional)</span><input name="hook_text" maxLength={80} placeholder="Empty: the AI writes one for each clip"/><small>Goes in the white card. A single emoji at the end works.</small></label>}
                            {template === "gta_mix" && <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}><span>Layouts to mix</span>
                <label style={{ display: "flex", gap: ".5rem", alignItems: "center" }}><input type="checkbox" name="mix_layouts" value="gta_cam" defaultChecked/> Framed reaction cam</label>
                <label style={{ display: "flex", gap: ".5rem", alignItems: "center" }}><input type="checkbox" name="mix_layouts" value="gta_hook" defaultChecked/> Widescreen + hook card</label>
                <small>Each clip gets the layout that suits it (the streamer reacting: framed cam; mostly action: hook card), and neighbouring clips look different. You can still change any clip afterwards.</small></fieldset>}
              {(template === "gta_cam" || template === "gta_mix") && <label className="field"><span>Webcam frame</span><select name="frame" defaultValue="neon_palm"><option value="neon_palm">Neon palm (glowing)</option><option value="none">No frame</option></select><small>Clips with no webcam found use the widescreen layout.</small></label>}
            </div>
          </div>}
          {template === "gta6_gameplay" && <div className="template-settings wide" aria-label="GTA 6 template settings">
            <div className="template-preview" aria-label="Preview: webcam card top centre, captions under it, wide gameplay card below, logo upper right"><i/><b>VI</b></div>
            <div className="template-controls">
              <label className="field"><span>Facecam size</span><select name="facecam_size" defaultValue="medium"><option value="small">Small (15%)</option><option value="medium">Medium (18%)</option><option value="large">Large (20%)</option></select></label>
              <div className="field"><span>Game logo</span><select name="game_logo" defaultValue="gta6"><option value="gta6">GTA 6</option><option value="none">Hidden</option></select><label className="logo-upload"><Upload size={18}/><span>Upload another logo</span><input name="logo_file" type="file" accept="image/png,image/webp,image/jpeg"/></label><small>PNG, WebP, or JPG up to 8 MB. An uploaded image replaces the selection above.</small></div>
              <label className="field"><span>Gameplay crop</span><select name="gameplay_crop" defaultValue="center"><option value="center">Centred (wide)</option><option value="fit">Whole frame, no crop</option><option value="auto">Smart tracking</option><option value="left">Keep left</option><option value="right">Keep right</option></select></label>
              <div className="field wide"><span>Clip strategy</span>
                <select name="clip_strategy" value={clipStrategy} onChange={e => setClipStrategy(e.target.value)}>
                  <option value="single">Use the length selected above</option>
                  <option value="gta_launch_mix">GTA VI Launch Test — short + story</option>
                </select>
                <small>{clipStrategy === "gta_launch_mix" ? "Creates 20–35 second viral moments, 60–80 second stories, and up to 120 seconds only when a story needs it. Strong multi-part stories may become Part 1 and Part 2." : "Creates clips using the job's normal length setting."}</small>
              </div>
            </div>
          </div>}
          <div className="field wide"><span>Captions</span>
            <Seg name="captions" value={captions} onChange={setCaptions} options={[{ id: "overlay", label: "On the video" }, { id: "bar", label: "Strip" }, { id: "off", label: "Off" }]} label="Captions"/></div>
          <div className="field wide"><span>Editing style</span>
            <Seg name="edit" value={opt.edit} onChange={setO("edit")} options={EDITS} label="Editing style"/></div>
          <details className="wide" style={{ gridColumn: "1 / -1" }}>
            <summary style={{ cursor: "pointer", fontWeight: 600 }}>Fine-tune</summary>
            <div className="fields" style={{ marginTop: 12 }}>
              <div className="field wide"><span>Auto-cut silence</span><Seg name="silence" value={opt.silence} onChange={setO("silence")} options={LEVELS} label="Auto-cut silence"/>
                <small>Higher cuts shorter pauses. Gameplay action and reactions are never cut.</small></div>
              <div className="field wide"><span>Zooms</span><Seg name="zoom" value={opt.zoom} onChange={setO("zoom")} options={LEVELS} label="Zooms"/></div>
              <div className="field wide"><span>Sound effects</span><Seg name="sfx" value={opt.sfx} onChange={setO("sfx")} options={LEVELS} label="Sound effects"/></div>
              {captions !== "off" && <div className="field wide"><span>Caption look</span>
                <Seg name="caption_look" value={look} onChange={setLook} options={[{ id: "pop", label: "Bold pop" }, { id: "karaoke", label: "Karaoke" }, { id: "clean", label: "Clean, lower" }]} label="Caption look"/></div>}
            </div>
          </details>
        </div>
      </section>

      <section className="card card-pad form-card">
        <div><h2><span className="step-num">4</span>Confirm</h2><p>Only use videos you own or have permission to edit.</p></div>
        <div style={{ display: "grid", gap: "var(--space-sm)" }}>
          <input type="hidden" name="rights" value="yes"/><p className="muted">By generating, you confirm you own this video or have permission to edit it.</p>
          <div className="form-actions" style={{ justifyContent: "flex-start" }}>
            <button className="btn btn-primary btn-lg" disabled={submitting}><Sparkle/>{up ? (up.phase === "saving" ? "Saving…" : `Uploading ${up.pct}%…`) : submitting ? "Starting…" : "Generate clips"}</button>
            <span className="muted" style={{ fontSize: "var(--text-sm)" }}>{up ? "Keep this tab open until the upload finishes." : "Progress shows on the Jobs page."}</span>
          </div>
          {(up || starting) && <div role="status" aria-live="polite" style={{ display: "grid", gap: 6 }}>
            <div className="progress" aria-label={up ? `Upload ${up.pct}%` : "Preparing"}><i style={{ width: `${up ? up.pct : 100}%`, opacity: up?.phase === "saving" || starting ? 0.55 : 1 }}/></div>
            <span className="muted" style={{ fontSize: "var(--text-sm)" }}>{up
              ? (up.phase === "saving" ? "Upload received: saving the file on this computer…"
                : `Uploading ${mb(up.loaded)} of ${mb(up.total)} · ${mb(up.speed)}/s ${eta(up.eta) ? "· " + eta(up.eta) : ""}`)
              : "Getting ready: starting the job (download, speech and moment detection come next). You'll land on the Jobs page."}</span>
          </div>}
        </div>
      </section>
    </form>
  </>;
}
