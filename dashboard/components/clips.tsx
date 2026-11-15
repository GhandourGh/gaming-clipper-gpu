"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, label, media, statusTone } from "@/lib/api";
import { Check, Download, Play, Refresh, Send, X } from "./icons";

export type ClipRow = {
  job_id: string; idx: number; job_title?: string; title: string; description?: string; hook?: string;
  tiktok_caption?: string; hashtags?: string[];
  status: string; review: string; style?: string; content_type?: string; moment_type?: string;
  version_type?: "viral_short" | "story" | string;
  quality?: number | null; ai_score?: number | null; below_quality_bar?: boolean; duration?: number | null;
  file?: string | null; thumbnail?: string | null; created_at?: string;
};
export const STYLES = [
  { id: "viral", label: "Viral Gaming" }, { id: "funny", label: "Funny Moments" }, { id: "cinematic", label: "Cinematic" },
  { id: "fast", label: "Fast-Paced" }, { id: "minimal", label: "Minimal" },
];

function reviewBadge(c: ClipRow) {
  if (c.review === "approved") return <span className="badge ok">Approved</span>;
  if (c.review === "rejected") return <span className="badge bad">Rejected</span>;
  if (c.status === "ready_for_review") return <span className="badge accent">To review</span>;
  return <span className={`badge ${statusTone(c.status)}`}>{label(c.status)}</span>;
}
const scoreClass = (v?: number | null) => (v == null ? "" : v >= 7 ? "good" : v < 5 ? "low" : "");

export async function decide(c: ClipRow, decision: "approved" | "rejected") {
  await api(`jobs/${c.job_id}/clips/${c.idx}/review`, { method: "POST", body: JSON.stringify({ decision }) });
}

/** What a clip without a video is waiting for, in plain words (it shows as a card before it is rendered). */
export function makingLabel(status?: string | null) {
  return ({
    selected: "Being made: waiting for its edit plan",
    planned: "Being made: edit plan ready, rendering next",
    assets_ready: "Being made: rendering next",
    rendering: "Being made: rendering now",
    failed: "Not made: this clip failed",
    plan_failed: "Not made: no usable edit plan",
    qc_failed: "Made but failed the quality check",
  } as Record<string, string>)[status || ""] || "No video yet";
}

export function ClipCard({ clip, onOpen, onChange }: { clip: ClipRow; onOpen: (c: ClipRow) => void; onChange?: () => void }) {
  const [busy, setBusy] = useState(false);
  const act = async (d: "approved" | "rejected") => { setBusy(true); try { await decide(clip, d); onChange?.(); } finally { setBusy(false); } };
  return <article className="card clip">
    <button className="clip-media" onClick={() => onOpen(clip)} aria-label={`Play ${clip.title}`}>
      {clip.thumbnail ? <img src={media(clip.thumbnail)} alt="" loading="lazy"/> : null}
      <span className="badges">{reviewBadge(clip)}{clip.version_type ? <span className="badge plain">{clip.version_type === "extended_story" ? "Extended 80–120s" : clip.version_type === "story" ? "Story 60–80s" : "Viral 20–35s"}</span> : clip.below_quality_bar ? <span className="badge warn plain">Below bar</span> : <span/>}</span>
      {clip.duration ? <span className="dur">{Math.round(clip.duration)}s</span> : null}
      {clip.file ? <span className="play"><Play size={42}/></span>
        : <span className="making"><span className="dot live"/>{makingLabel(clip.status)}</span>}
    </button>
    <div className="clip-body">
      <h3>{clip.title}</h3>
      <div className="meta">{clip.job_title && <span>{clip.job_title}</span>}</div>
      <div className="scores">
        {clip.quality != null && <span className={`score ${scoreClass(clip.quality)}`} title="Moment score from ranking">Moment {Number(clip.quality).toFixed(0)}/10</span>}
        {clip.ai_score != null && <span className={`score ${scoreClass(clip.ai_score)}`} title="AI review of the finished clip">Edit {Number(clip.ai_score).toFixed(0)}/10</span>}
      </div>
      {clip.status === "ready_for_review" && clip.review === "pending" ? (
        <div className="clip-actions">
          <button className="btn btn-sm btn-success" onClick={() => act("approved")} disabled={busy} aria-label="Approve"><Check size={16}/><span className="label">Approve</span></button>
          <button className="btn btn-sm btn-danger" onClick={() => act("rejected")} disabled={busy} aria-label="Reject"><X size={16}/><span className="label">Reject</span></button>
        </div>
      ) : (
        <div className="clip-actions">
          <button className="btn btn-sm" onClick={() => onOpen(clip)}><Play size={14}/><span className="label">Open</span></button>
          {clip.file && <a className="btn btn-sm" href={media(clip.file)} download aria-label="Download"><Download size={16}/></a>}
        </div>
      )}
    </div>
  </article>;
}

export function PlayerModal({ clip, onClose, onChange }: { clip: ClipRow | null; onClose: () => void; onChange?: () => void }) {
  const [busy, setBusy] = useState("");
  const [style, setStyle] = useState("viral");
  const [note, setNote] = useState("");
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const key = clip ? `${clip.job_id}-${clip.idx}` : "";
  useEffect(() => {  // reset only when a different clip opens (parents re-render while polling)
    if (!clip) return;
    setStyle(clip.style || "viral"); setNote("");
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeRef.current(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  if (!clip) return null;
  const run = async (name: string, fn: () => Promise<unknown>, message: string) => {
    setBusy(name); try { await fn(); setNote(message); onChange?.(); } catch (e) { setNote(e instanceof Error ? e.message : "Failed"); } finally { setBusy(""); }
  };
  return <div className="modal" onClick={onClose} role="dialog" aria-modal="true" aria-label={clip.title}>
    <div className="modal-box" onClick={e => e.stopPropagation()}>
      {clip.file ? <video src={media(clip.file)} poster={media(clip.thumbnail)} controls autoPlay playsInline/> : <div className="empty">{makingLabel(clip.status)}. It appears here when it is rendered; the page updates on its own.</div>}
      <div className="modal-side">
        <div className="card-title" style={{ marginBottom: 0 }}>{reviewBadge(clip)}<button className="btn btn-ghost icon-btn" onClick={onClose} aria-label="Close"><X/></button></div>
        <h2>{clip.title}</h2>
        {clip.description && <p className="muted" style={{ fontSize: "var(--text-sm)" }}>{clip.description}</p>}
        <dl>
          {clip.hook && <><dt>Hook</dt><dd>{clip.hook}</dd></>}
          {clip.job_title && <><dt>Source</dt><dd><Link href={`/jobs/${clip.job_id}`} style={{ color: "var(--color-accent)" }}>{clip.job_title}</Link></dd></>}
          {clip.duration ? <><dt>Length</dt><dd className="num">{Math.round(clip.duration)} s</dd></> : null}
          {clip.version_type && <><dt>Test format</dt><dd>{clip.version_type === "extended_story" ? "Extended story · 80–120 s" : clip.version_type === "story" ? "Complete story · 60–80 s" : "Fast viral moment · 20–35 s"}</dd></>}
          {clip.content_type && <><dt>Video type</dt><dd>{label(clip.content_type)}</dd></>}
          {clip.moment_type && <><dt>Moment</dt><dd>{label(clip.moment_type)}</dd></>}
          {clip.quality != null && <><dt>Moment score</dt><dd className="num">{Number(clip.quality).toFixed(1)}/10</dd></>}
          {clip.ai_score != null && <><dt>AI edit review</dt><dd className="num">{Number(clip.ai_score).toFixed(1)}/10</dd></>}
        </dl>
        {clip.tiktok_caption && <div className="field">
          <span>Recommended TikTok caption</span>
          <p style={{ margin: 0 }}>{clip.tiktok_caption}</p>
          {!!clip.hashtags?.length && <p className="muted" style={{ margin: 0 }}>{clip.hashtags.join(" ")}</p>}
        </div>}
        <div style={{ display: "grid", gap: ".5rem" }}>
          {clip.status === "ready_for_review" && clip.review !== "approved" && <div style={{ display: "flex", gap: ".5rem" }}>
            <button className="btn btn-success" style={{ flex: 1 }} disabled={!!busy} onClick={() => run("approve", () => decide(clip, "approved"), "Approved")}><Check size={16}/>Approve</button>
            <button className="btn btn-danger" style={{ flex: 1 }} disabled={!!busy} onClick={() => run("reject", () => decide(clip, "rejected"), "Rejected")}><X size={16}/>Reject</button>
          </div>}
          <Link className="btn btn-primary" href={`/publish?job=${clip.job_id}&idx=${clip.idx}`}><Send size={16}/>Prepare a post</Link>
          {clip.file && <a className="btn" href={media(clip.file)} download><Download size={16}/>Download MP4</a>}
          <div className="field" style={{ marginTop: ".25rem" }}>
            <span>Regenerate in another style</span>
            <div style={{ display: "flex", gap: ".5rem" }}>
              <select value={style} onChange={e => setStyle(e.target.value)} aria-label="Editing style">{STYLES.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</select>
              <button className="btn" disabled={!!busy} onClick={() => run("regen", () => api(`jobs/${clip.job_id}/clips/${clip.idx}/regenerate?style=${style}`, { method: "POST" }), "Regenerating: the new version appears on the job page in a few minutes")}><Refresh size={16}/>Go</button>
            </div>
          </div>
          {note && <p className="notice info" style={{ margin: 0 }}>{note}</p>}
        </div>
      </div>
    </div>
  </div>;
}
