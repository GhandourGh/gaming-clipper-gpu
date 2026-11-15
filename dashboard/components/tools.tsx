"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { ago, api, duration, label, media, statusTone, useData } from "@/lib/api";
import type { JobSummary } from "@/lib/types";
import { Back, Download, Play, Plus, Sparkle } from "./icons";

type When = { source: number; stream: string; link?: string | null; cut?: number | null; cut_tc?: string | null };
type Step = { kind: string; what: string; why: string; at: When; length?: number };
type Box = { x: number; y: number; w: number; h: number };
type Cut = { n: number; role: string; reason: string; length: number; in: When; out: When; cut_start_tc: string; still?: string | null };
type Export = { name: string; url: string; mb: number };
type ClipGuide = {
  idx: number; title: string; description?: string; moment_type?: string; style?: string; length?: number; status?: string; error?: string;
  hook?: { text: string; seconds: number };
  layout?: { mode: string; tip: string; source_size: [number, number]; webcam_share: number; gameplay_focus_x: number;
    webcam_px?: { x: number; y: number; w: number; h: number; norm: { x: number; y: number; w: number; h: number } } | null;
    bubble?: { corner: string; shape: string; size: number } | null };
  captions?: { emphasis: string[]; style: string; position: string; fixes: { wrong: string; right: string }[] };
  music?: { enabled: boolean; mood?: string | null; gain_db: number };
  cuts?: Cut[]; steps?: Step[]; why?: Record<string, string>; exports?: Export[]; pre?: PreGuide | null;
};
type PreItem = { kind: string; what: string; why: string; at: When; used: boolean; kept?: boolean };
type PreGuide = { items: PreItem[]; used: number; total: number; caption_emphasis: { word: string; used: boolean }[];
  hooks: string[]; titles: string[]; music_mood?: string | null; guided_plan?: boolean };

function PreGuideView({ g }: { g: PreGuide }) {
  const [open, setOpen] = useState(false);
  const shown = open ? g.items : g.items.slice(0, 8);
  return <div className="guide-block guide-pre">
    <h3>Guide the AI got before editing <span className={`badge plain ${g.used / Math.max(1, g.total) >= 0.5 ? "ok" : "warn"}`}>used {g.used} of {g.total}</span></h3>
    <p className="muted">{g.guided_plan
      ? "Built from the stream before the AI planned this clip: the parts, teaser, zooms, sounds, text and dead air. A tick means the AI's edit uses it."
      : "This clip's current edit was planned before the guide existed (or the guided re-plan did not finish), so the ticks compare an unguided edit. Regenerate the clip to have the AI edit from this guide."}</p>
    <ol className="guide-steps">{shown.map((x, i) => <li key={i}>
      <Stamp w={x.at}/><span className={`badge plain ${x.used ? "ok" : ""}`}>{x.used ? "✓ used" : "not used"}</span>
      <div><b>{x.what}</b><p className="muted">{x.why}{x.kept === false ? " (this moment was cut out of the clip)" : ""}</p></div></li>)}</ol>
    {g.items.length > 8 && <button className="btn btn-ghost btn-sm" onClick={() => setOpen(!open)}>{open ? "Show less" : `Show all ${g.items.length}`}</button>}
    {g.caption_emphasis.length > 0 && <p>Words to highlight: {g.caption_emphasis.map(w => <span key={w.word} className={`chip guide-word${w.used ? " on" : ""}`}>{w.word}</span>)}</p>}
    {g.hooks.length > 0 && <p>Hook ideas: {g.hooks.map(h => `“${h}”`).join(" · ")}</p>}
  </div>;
}

type Moment = { idx: number; score?: number; ai_quality?: number; title?: string; kind?: string; what?: string; why?: string; hook?: string;
  start: When; end: When; length?: number; beats: { role: string; reason: string; quote?: string; start: When; end: When }[] };
type Guide = { job: { id: string; title?: string; status: string; url?: string; engine?: string; stream_length?: number }; clips: ClipGuide[]; moments: Moment[] };

const KIND_TONE: Record<string, string> = { cut: "", zoom: "accent", text: "info", emoji: "info", image: "info", sfx: "warn", transition: "ok" };
const KIND_LABEL: Record<string, string> = { cut: "Cut", zoom: "Zoom", text: "Text", emoji: "Emoji", image: "Picture", sfx: "Sound", transition: "Transition" };

function Stamp({ w, cut = false }: { w: When; cut?: boolean }) {
  return <span className="guide-stamp">
    {cut && w.cut_tc && <b className="mono">{w.cut_tc}</b>}
    {w.link ? <a href={w.link} target="_blank" rel="noreferrer" className="mono" title="Open the stream at this moment">{w.stream}</a> : <span className="mono">{w.stream}</span>}
  </span>;
}

function sheetText(c: ClipGuide) {
  const lines = [`${c.title}  (${c.length}s, ${c.layout?.mode} layout)`, `Hook: "${c.hook?.text}" for ${c.hook?.seconds}s`, `Layout: ${c.layout?.tip}`];
  if (c.layout?.webcam_px) { const b = c.layout.webcam_px; lines.push(`Webcam crop: x ${b.x}, y ${b.y}, ${b.w}x${b.h} px of ${c.layout.source_size.join("x")}`); }
  lines.push("", "Cut list (stream time):");
  c.cuts?.forEach(x => lines.push(`${x.n}. ${x.in.stream} -> ${x.out.stream} (${x.length}s, ${label(x.role)}) - ${x.reason}`));
  lines.push("", "Edit sheet (rough-cut time | stream time):");
  c.steps?.forEach(s => lines.push(`${s.at.cut_tc ?? "-"} | ${s.at.stream} | ${KIND_LABEL[s.kind] ?? s.kind}: ${s.what} - ${s.why}`));
  if (c.captions?.emphasis.length) lines.push("", `Highlight these words in the captions: ${c.captions.emphasis.join(", ")}`);
  if (c.music?.enabled) lines.push(`Music: ${c.music.mood ?? "any"}, quiet under speech (${c.music.gain_db} dB)`);
  return lines.join("\n");
}

function WebcamBox({ c }: { c: ClipGuide }) {
  const l = c.layout!;
  const b = l.webcam_px?.norm;
  return <div className="guide-frame" style={{ aspectRatio: `${l.source_size[0]} / ${l.source_size[1]}` }} aria-label="Where the webcam is in the stream picture">
    <span className="guide-frame-label">Stream frame {l.source_size.join("×")}</span>
    {b && <span className="guide-cam" style={{ left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%` }}>webcam</span>}
  </div>;
}

function BeatStill({ x }: { x: Cut }) {
  return x.still ? <img className="guide-still" src={media(x.still)} alt={`Picture in the middle of ${x.role === "cold_open" ? "the teaser" : `beat ${x.n}`}`} loading="lazy"/> : null;
}

function ClipCard({ jobId, c, onExported }: { jobId: string; c: ClipGuide; onExported: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);
  if (c.error) return <div className="card card-pad"><h3>{c.title}</h3><p className="muted">{c.error}</p></div>;
  const rough = c.exports?.find(e => e.name.endsWith("rough_cut.mp4"));
  const doExport = async () => {
    setBusy(true); setErr("");
    try { await api(`tools/guide/${jobId}/clips/${c.idx}/export`, { method: "POST" }); onExported(); }
    catch (e) { setErr(e instanceof Error ? e.message : "Export failed"); }
    finally { setBusy(false); }
  };
  const copy = async () => { await navigator.clipboard.writeText(sheetText(c)); setCopied(true); window.setTimeout(() => setCopied(false), 1500); };

  return <section className="card card-pad guide-clip">
    <div className="card-title"><div><p className="eyebrow">Clip {c.idx + 1} · {c.length}s · {label(c.moment_type)}</p><h2>{c.title}</h2>
      {c.description && <p>{c.description}</p>}</div></div>

    <div className="guide-grid">
      <div className="guide-block"><h3>Hook (first {c.hook?.seconds}s)</h3><p className="guide-hook">“{c.hook?.text}”</p>
        {c.why && Object.keys(c.why).length > 0 && <dl className="guide-why">{Object.entries(c.why).map(([k, v]) => <div key={k}><dt>{label(k)}</dt><dd>{v}</dd></div>)}</dl>}
      </div>
      <div className="guide-block"><h3>Layout: {c.layout?.mode}</h3><p>{c.layout?.tip}</p>
        <WebcamBox c={c}/>
        {c.layout?.webcam_px && <p className="muted mono">Plan's webcam crop: x {c.layout.webcam_px.x}, y {c.layout.webcam_px.y}, {c.layout.webcam_px.w}×{c.layout.webcam_px.h} px</p>}
      </div>
    </div>

    {c.pre && <PreGuideView g={c.pre}/>}

    <h3 className="guide-h">Cut list, in play order</h3>
    <ol className="guide-steps">{c.cuts?.map(x => <li key={x.n}>
      <span className="guide-stamp"><b className="mono">{x.cut_start_tc}</b><span className="mono muted">{x.length}s</span></span>
      <span className={`badge plain ${x.role === "cold_open" ? "accent" : ""}`}>{x.role === "cold_open" ? "Teaser" : `Beat ${x.n}`}</span>
      <div><b><Stamp w={x.in}/> → <Stamp w={x.out}/></b><p className="muted">{x.reason}</p>
        <BeatStill x={x}/></div></li>)}</ol>

    <h3 className="guide-h">Edit sheet</h3>
    <p className="muted">Bold time = position in the rough cut below. Second time = in the stream (tap to open it there).</p>
    <ol className="guide-steps">{c.steps?.map((s, i) => <li key={i}>
      <Stamp w={s.at} cut/><span className={`badge plain ${KIND_TONE[s.kind] ?? ""}`}>{KIND_LABEL[s.kind] ?? s.kind}</span>
      <div><b>{s.what}</b><p className="muted">{s.why}</p></div></li>)}</ol>

    <div className="guide-grid">
      <div className="guide-block"><h3>Captions</h3>
        {c.captions?.emphasis.length ? <p>Highlight: {c.captions.emphasis.map(w => <span key={w} className="chip guide-word">{w}</span>)}</p> : <p className="muted">No words to highlight.</p>}
        {!!c.captions?.fixes.length && <p className="muted">Misheard by speech-to-text: {c.captions.fixes.map(f => `“${f.wrong}” → “${f.right}”`).join(", ")}</p>}
      </div>
      <div className="guide-block"><h3>Music</h3><p>{c.music?.enabled ? `${c.music.mood ?? "Any"} mood, quiet under the voices (${c.music.gain_db} dB)` : "No music: the stream audio carries it."}</p></div>
    </div>

    <h3 className="guide-h">Footage to edit</h3>
    {err && <div className="notice error">{err}</div>}
    <div className="form-actions guide-actions">
      <button className="btn btn-primary" onClick={doExport} disabled={busy}><Download/>{busy ? "Cutting footage… (about 20 s)" : c.exports?.length ? "Export again" : "Export rough cut + files"}</button>
      <button className="btn" onClick={copy}>{copied ? "Copied" : "Copy sheet as text"}</button>
    </div>
    {rough && <video className="guide-video" src={media(rough.url)} controls playsInline preload="metadata"/>}
    {!!c.exports?.length && <ul className="guide-files">{c.exports.map(e => <li key={e.name}><a href={media(e.url)} download={e.name}><Download size={14}/>{e.name.replace(/^guide_\d+_/, "")}</a><span className="muted">{e.mb ? `${e.mb} MB` : ""}</span></li>)}</ul>}
  </section>;
}

function Moments({ moments }: { moments: Moment[] }) {
  if (!moments.length) return <div className="card empty"><b>No scored moments</b><span>This job has no analysis left.</span></div>;
  return <div className="rows card">{moments.map(m => <details key={m.idx} className="guide-moment">
    <summary><span className="score mono">{m.score ?? "–"}</span><div><b>{m.title || m.hook || `Moment ${m.idx + 1}`}</b>
      <div className="row-meta"><Stamp w={m.start}/>{m.length ? <span>{Math.round(m.length)}s</span> : null}{m.kind && <span>{m.kind}</span>}
        {m.beats.length > 1 && <span className="badge info plain">{m.beats.length} linked parts</span>}</div></div></summary>
    {m.what && <p>{m.what}</p>}
    {m.why && <p className="muted">Why it works: {m.why}</p>}
    {m.hook && <p>Hook idea: “{m.hook}”</p>}
    {m.beats.length > 0 && <ol className="guide-beats">{m.beats.map((b, i) => <li key={i}><span className="badge plain">{b.role}</span> <Stamp w={b.start}/> → <Stamp w={b.end}/>
      <p>{b.reason}</p>{b.quote && <p className="muted">“{b.quote}”</p>}</li>)}</ol>}
  </details>)}</div>;
}

function GuideView({ jobId }: { jobId: string }) {
  const { data, error, reload } = useData<Guide>(`tools/guide/${jobId}`);
  if (error) return <div className="notice error">{error}</div>;
  if (!data) return <div className="skeleton" style={{ height: 320 }}/>;
  return <>
    <div className="section-head"><h2>{data.job.title || "Untitled stream"}</h2>
      <p>{data.job.stream_length ? `${duration(data.job.stream_length)} of video` : ""}{data.job.engine ? ` · ${data.job.engine} engine` : ""}</p></div>
    {data.clips.length === 0 && <div className="notice info">No clip plans in this job yet. The moments the AI found are below.</div>}
    {data.clips.map(c => <ClipCard key={c.idx} jobId={jobId} c={c} onExported={reload}/>)}
    <div className="section-head guide-h"><h2>Every moment the AI found</h2><p>Best first. Linked parts = one story built from moments far apart in the stream.</p></div>
    <Moments moments={data.moments}/>
  </>;
}

export function Tools() {
  const params = useSearchParams();
  const router = useRouter();
  const jobId = params.get("job");
  const { data } = useData<{ jobs: JobSummary[] }>(jobId ? null : "dashboard/jobs", 15000);
  const jobs = (data?.jobs || []).filter(j => j.clip_count > 0 || j.status === "done");

  return <>
    <header className="page-head"><div><p className="eyebrow">Tools</p><h1>Edit guide</h1>
      <p>The AI finds the moments and plans the edit: cuts, layout, zooms, on-screen text, sounds and the hook. You do the editing yourself.</p></div>
      {jobId ? <button className="btn" onClick={() => router.push("/tools")}><Back/>All videos</button>
        : <Link href="/new" className="btn btn-primary"><Plus/>Find moments in a new video</Link>}</header>
    {jobId ? <GuideView jobId={jobId}/> : <>
      <div className="notice info"><Sparkle size={16}/> Start a job on New job (the Stories engine links related moments across a whole stream). When it finishes, open it here to get the guide and the raw footage.</div>
      {!data ? <div className="skeleton" style={{ height: 200 }}/> : jobs.length === 0
        ? <div className="card empty"><b>No finished videos yet</b><span>Run a job first, then come back.</span></div>
        : <div className="rows card">{jobs.map(j => <Link key={j.id} href={`/tools?job=${j.id}`} className="row">
          {j.thumbnail ? <span className="thumb"><img src={media(j.thumbnail)} alt=""/></span> : <span className="thumb"><Play/></span>}
          <div><div className="row-title">{j.title || "Untitled"}</div><div className="row-meta"><span className={`badge ${statusTone(j.status)}`}>{label(j.status)}</span>
            <span>{j.clip_count} clip{j.clip_count === 1 ? "" : "s"}</span><span>{duration(j.duration)}</span><span>{ago(j.created_at)}</span></div></div>
          <span className="row-side">Open guide</span></Link>)}</div>}
    </>}
  </>;
}
