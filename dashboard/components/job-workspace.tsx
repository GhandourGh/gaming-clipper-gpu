"use client";

import Link from "next/link";
import { useState } from "react";
import { ACTIVE, duration, label, media, statusTone, usd, useData } from "@/lib/api";
import type { Clip, JobDetail, Stage } from "@/lib/types";
import { ClipCard, PlayerModal, type ClipRow } from "./clips";
import { Back, Check, Download, X } from "./icons";
import { JobActions } from "./job-actions";

type Step = { label: string; state: "waiting" | "running" | "done" | "failed" | "skipped"; detail?: string; error?: string };

function stageStep(lbl: string, s?: Stage, fallback: Step["state"] = "waiting"): Step {
  if (!s) return { label: lbl, state: fallback };
  const state = s.status === "done" ? "done" : s.status === "failed" ? "failed" : s.status === "running" || s.status === "queued" ? "running" : "waiting";
  const secs = s.started_at && s.finished_at ? (new Date(s.finished_at).getTime() - new Date(s.started_at).getTime()) / 1000 : s.result?.seconds;
  return { label: lbl, state, detail: state === "done" && secs ? `${Math.round(secs)}s` : state === "running" ? (s.status === "queued" ? "queued" : "working") : undefined, error: s.error };
}

function toRow(jobId: string, title: string, c: Clip & { ai_review?: { score?: number } | null; style?: string }): ClipRow {
  const cand = (c.candidate || {}) as Record<string, unknown>, edl = (c.edl || {}) as Record<string, unknown>;
  const segs = (edl.segments as Array<{ start: number; end: number }>) || [];
  return { job_id: jobId, idx: c.idx, job_title: title, title: String(edl.title || cand.title || "Untitled clip"),
    description: String(edl.description || cand.why_engaging || ""), hook: (edl.hook as { text?: string })?.text, status: c.status, review: c.review,
    tiktok_caption: String(edl.tiktok_caption || edl.description || ""), hashtags: (edl.hashtags as string[]) || [],
    style: String(edl.style || c.style || "viral"), content_type: cand.content_type as string, moment_type: edl.moment_type as string,
    version_type: cand.version_type as string,
    quality: cand.quality as number, ai_score: c.ai_review?.score ?? null, below_quality_bar: Boolean(cand.below_quality_bar),
    duration: segs.length ? segs.reduce((n, s) => n + s.end - s.start, 0) : null, file: c.file, thumbnail: c.thumbnail };
}

function Pipeline({ steps }: { steps: Step[] }) {
  return <div className="pipeline" role="list">{steps.map((s, index) => (
    <div key={s.label} className={`step ${s.state}`} title={s.error || undefined} role="listitem">
      <div className="step-track">
        <span className="step-indicator" aria-label={`${s.label}: ${s.state}`}>
          {s.state === "done" ? <Check size={13}/> : s.state === "failed" ? <X size={13}/> : s.state === "running" ? <i className="step-spinner"/> : <i className="step-dot"/>}
        </span>
        {index < steps.length - 1 && <span className="step-separator" aria-hidden="true"/>}
      </div>
      <div className="step-copy"><b>{s.label}</b><span>{s.state === "failed" ? "failed" : s.detail || (s.state === "done" ? "done" : s.state === "skipped" ? "—" : "waiting")}</span></div>
    </div>
  ))}</div>;
}

export function JobWorkspace({ id }: { id: string }) {
  const { data, error, reload } = useData<JobDetail>(`dashboard/jobs/${id}`, 5000);
  const [open, setOpen] = useState<ClipRow | null>(null);
  if (!data) return <div>{error ? <div className="notice error">{error}</div> : <div className="skeleton" style={{ height: 320 }}/>}</div>;
  const { job, clips, stages, costs, rejected } = data;
  const st = (name: string, idx = -1) => stages.find(s => s.name === name && s.clip_idx === idx);
  const past = (statuses: string[]) => !statuses.includes(job.status);
  const shared: Step[] = [
    stageStep("Download", st("ingest")),
    stageStep("Analyse", st("analyze")),
    { label: "Find moments", state: job.status === "ranking" ? "running" : past(["created", "ingested", "analyzed", "ranking"]) ? "done" : "waiting" },
    { label: "Pick", state: past(["created", "ingested", "analyzed", "ranking"]) ? "done" : "waiting", detail: clips.length ? `${clips.length} picked` : undefined },
  ];
  const rows = clips.map(c => toRow(id, job.title, c as Clip & { ai_review?: { score?: number } | null }));
  const failedStages = stages.filter(s => s.status === "failed" && s.error);
  const running = ACTIVE.has(job.status);

  return <>
    <header className="page-head">
      <div><Link href="/jobs" className="back"><Back size={16}/>All jobs</Link><p className="eyebrow">Job {job.id.slice(0, 8)}</p><h1>{job.title}</h1>
        <div className="row-meta" style={{ fontSize: "var(--text-sm)", marginTop: ".6rem" }}>
          <span className={`badge ${statusTone(job.status)} ${running ? "live" : ""}`}>{label(job.status)}</span>
          <span>{job.style}</span><span>· {clips.length} {clips.length === 1 ? "clip" : "clips"}</span>
          {job.elapsed_s ? <span className="num">· {duration(job.elapsed_s)} total</span> : null}<span className="num">· {usd(job.cost_usd)} est.</span>
        </div></div>
      <div style={{ display: "grid", gap: "var(--space-sm)", justifyItems: "end" }}>
        <div style={{ display: "flex", gap: "var(--space-sm)", flexWrap: "wrap", justifyContent: "flex-end" }}>
          {job.source_available && <a className="btn" href={media(`/jobs/${id}/source`)}><Download/>Download raw footage</a>}
          {clips.some(c => c.file) && <a className="btn btn-primary" href={media(`/jobs/${id}/zip`)}><Download/>Download all clips</a>}
        </div>
        <JobActions id={id} title={job.title} running={running} onChange={reload}/>
      </div>
    </header>
    {job.error && <div className="notice error">{job.error}</div>}
    {job.burned_captions?.present && <div className="notice info"><b>The source already has captions</b> in the {job.burned_captions.where} of the picture
      ({Math.round((job.burned_captions.coverage || 0) * 100)}% of sampled frames, e.g. “{(job.burned_captions.examples || []).slice(0, 2).join("”, “")}”). The editor was told to skip or move ours so they don't overlap.</div>}

    <section className="card pipeline-card">
      <div className="pipeline-head"><div><span className={`badge ${running ? "accent live" : failedStages.length ? "bad" : "ok"}`}>{running ? "Live" : failedStages.length ? "Needs attention" : "Complete"}</span><h2>Pipeline</h2><p>Live progress of every step. Times show how long completed work took.</p></div><strong>{shared.filter(s => s.state === "done").length}/{shared.length}</strong></div>
      <div className="pipeline-shared"><span className="pipeline-label">Shared processing</span><Pipeline steps={shared}/></div>
      <div className="pipeline-clips">
      {clips.map(c => {
        const cc = c as Clip & { ai_review?: { score?: number; verdict?: string } | null };
        const steps: Step[] = [
          { label: "Edit plan", state: c.edl ? "done" : c.status === "failed" ? "failed" : "waiting" },
          { label: "Assets", state: ["assets_ready", "rendering", "rendered", "ready_for_review", "qc_failed"].includes(c.status) || c.file ? "done" : "waiting" },
          stageStep("Render", st("render", c.idx)),
          stageStep("Quality check", st("qc", c.idx)),
          cc.ai_review?.score != null ? { label: "AI review", state: "done", detail: `${cc.ai_review.score.toFixed(0)}/10` } : { label: "AI review", state: "waiting" },
        ];
        return <article className="pipeline-clip" key={c.idx}><header><span>Clip {c.idx + 1}</span><b>{steps.filter(s => s.state === "done").length}/{steps.length}</b></header><Pipeline steps={steps}/></article>;
      })}
      </div>
      {failedStages.length > 0 && <details className="pipeline-errors"><summary>Error details ({failedStages.length})</summary>
        {failedStages.map(s => <p key={`${s.name}${s.clip_idx}`} className="notice error" style={{ marginTop: ".5rem", whiteSpace: "pre-wrap" }}>{s.name}{s.clip_idx >= 0 ? ` (clip ${s.clip_idx + 1})` : ""}: {s.error}</p>)}</details>}
    </section>

    <section className="section">
      <div className="section-head"><div><h2>Clips</h2><p>Click a clip to watch it, approve it, regenerate it in another style or prepare a post.</p></div></div>
      {rows.length ? <div className="clip-grid">{rows.map(r => <ClipCard key={r.idx} clip={r} onOpen={setOpen} onChange={reload}/>)}</div>
        : <div className="card empty">{running ? <><b>Working on it</b><span>Clips appear here as soon as they're rendered.</span></> : <><b>No clips</b><span>No moment was strong enough, or the job failed.</span></>}</div>}
    </section>

    <div className="grid-2 section">
      <section className="card">
        <div className="card-pad" style={{ paddingBottom: 0 }}><div className="card-title"><div><h2>Moments not used</h2><p>Candidates the AI ranked lower, with its reason.</p></div></div></div>
        {rejected.length ? <div className="rows">{rejected.map(r => (
          <div className="row" key={r.idx} style={{ gridTemplateColumns: "minmax(0,1fr) auto", alignItems: "start" }}>
            <span style={{ minWidth: 0 }}><div className="row-title">{r.title || "Untitled moment"}</div><p className="muted" style={{ fontSize: "var(--text-sm)", marginTop: ".2rem" }}>{r.why_engaging}</p></span>
            <span className="score">{Number(r.quality).toFixed(0)}/10</span></div>
        ))}</div> : <div className="empty" style={{ padding: "1.5rem" }}>None</div>}
      </section>
      <section className="card">
        <div className="card-pad" style={{ paddingBottom: 0 }}><div className="card-title"><div><h2>AI usage</h2><p>Estimated at paid rates. $0 on the free tier.</p></div></div></div>
        <div className="table-wrap"><table><thead><tr><th>Model</th><th className="num">Calls</th><th className="num">Tokens</th><th className="num">USD</th></tr></thead>
          <tbody>{costs.map(c => <tr key={c.model}><td>{c.model}</td><td className="num">{c.calls}</td><td className="num">{Number(c.tokens).toLocaleString()}</td><td className="num">{usd(c.usd, 4)}</td></tr>)}</tbody></table></div>
      </section>
    </div>
    <PlayerModal clip={open} onClose={() => setOpen(null)} onChange={reload}/>
  </>;
}
