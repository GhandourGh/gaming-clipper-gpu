"use client";

import Link from "next/link";
import { useState } from "react";
import { ago, duration, label, media, statusTone, usd, useData } from "@/lib/api";
import type { JobSummary } from "@/lib/types";
import { ClipCard, PlayerModal, type ClipRow } from "./clips";
import { Arrow, Plus, Trash } from "./icons";
import { deleteJob } from "./job-actions";

type Overview = {
  live: Array<{ id: string; title: string; status: string; step: string; progress: number; elapsed_s: number }>;
  review: ClipRow[];
  cost: { day: number; week: number; total: number; calls_day: number };
  counts: { jobs: number; done: number; clips: number; approved: number };
  health: Record<string, { ok: boolean; detail: string }>;
};
const STEP_LABEL: Record<string, string> = { ingest: "Downloading", analyze: "Analysing", rank: "Finding moments", select: "Picking moments",
  director: "Planning the edit", assets: "Fetching assets", render: "Rendering", qc: "Quality check", review: "AI review" };
const HEALTH_LABEL: Record<string, string> = { worker: "Worker", gpu: "GPU", pipeline: "Job engine", database: "Database", disk: "Storage" };

export function JobRows({ jobs, onDeleted }: { jobs: JobSummary[]; onDeleted?: () => void }) {
  return <div className="rows">{jobs.map(job => (
    <Link href={`/jobs/${job.id}`} className="row" key={job.id}>
      <span className="thumb">{job.thumbnail ? <img src={media(job.thumbnail)} alt="" loading="lazy"/> : job.title.slice(0, 1).toUpperCase()}</span>
      <span style={{ minWidth: 0 }}>
        <div className="row-title">{job.title}</div>
        <div className="row-meta"><span className={`badge ${statusTone(job.status)}`}>{label(job.status)}</span><span>{ago(job.created_at)}</span><span>· {job.style}</span></div>
      </span>
      <span className="row-side"><span>{job.clip_count} {job.clip_count === 1 ? "clip" : "clips"}</span><span className="num">{usd(job.cost_usd)}</span>
        {onDeleted && <button className="btn btn-icon" aria-label={`Delete ${job.title}`} title="Delete this job and its files"
          onClick={e => { e.preventDefault(); e.stopPropagation(); deleteJob(job.id, job.title).then(ok => ok && onDeleted()).catch(err => window.alert(err instanceof Error ? err.message : "Could not delete")); }}><Trash size={16}/></button>}
        <Arrow size={16}/></span>
    </Link>
  ))}</div>;
}

export function Dashboard() {
  const { data, error, reload } = useData<Overview>("dashboard/overview", 6000);
  const jobs = useData<{ jobs: JobSummary[] }>("dashboard/jobs", 15000);
  const [open, setOpen] = useState<ClipRow | null>(null);
  const hour = new Date().getHours();
  const greet = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";

  return <>
    <header className="page-head">
      <div><p className="eyebrow">Overview</p><h1>{greet}</h1><p>What's running, what needs your decision, and how the system is doing.</p></div>
      <Link href="/new" className="btn btn-primary btn-lg"><Plus/>New job</Link>
    </header>
    {error && <div className="notice error" role="alert">{error}</div>}

    <section className="kpis" aria-label="Summary">
      <div className="card kpi"><span>Waiting for review</span><strong>{data ? data.review.length : "—"}</strong><small>Clips that need your decision</small></div>
      <div className="card kpi"><span>Clips made</span><strong>{data ? data.counts.clips : "—"}</strong><small>{data ? `${data.counts.approved} approved` : " "}</small></div>
      <div className="card kpi"><span>Jobs</span><strong>{data ? data.counts.jobs : "—"}</strong><small>{data ? `${data.live.length} running now` : " "}</small></div>
      <div className="card kpi"><span>AI cost, 7 days</span><strong className="num">{data ? usd(data.cost.week, 2) : "—"}</strong><small>Estimate · $0 on the free tier</small></div>
    </section>

    <section className="section" aria-label="System health">
      <div className="health">{data ? Object.entries(data.health).map(([k, h]) => (
        <div key={k}><span className={`dot ${h.ok ? "" : "bad"}`}/><span style={{ minWidth: 0 }}><b>{HEALTH_LABEL[k] || k}</b><span>{h.detail}</span></span></div>
      )) : [0, 1, 2, 3].map(i => <div key={i} className="skeleton" style={{ height: 52 }}/>)}</div>
    </section>

    <section className="section">
      <div className="section-head"><div><h2>Running now</h2></div>{data && data.live.length > 0 && <span className="badge info live">Live</span>}</div>
      <div className="card">
        {!data ? <div className="skeleton" style={{ height: 80, margin: "1rem" }}/> : data.live.length === 0 ? (
          <div className="empty" style={{ padding: "2rem 1rem" }}><span>Nothing is running.</span><Link href="/new" className="btn btn-sm"><Plus size={16}/>Start a job</Link></div>
        ) : <div className="rows">{data.live.map(j => (
          <Link href={`/jobs/${j.id}`} className="row" key={j.id} style={{ gridTemplateColumns: "minmax(0,1fr) auto" }}>
            <span style={{ minWidth: 0 }}>
              <div className="row-title">{j.title}</div>
              <div className="row-meta"><span className="badge accent live">{STEP_LABEL[j.step] || label(j.step)}</span><span className="num">{duration(j.elapsed_s)} elapsed</span></div>
              <div className="progress" style={{ marginTop: ".55rem" }}><i style={{ width: `${Math.max(6, j.progress * 100)}%` }}/></div>
            </span>
            <span className="row-side"><Arrow size={16}/></span>
          </Link>
        ))}</div>}
      </div>
    </section>

    <section className="section">
      <div className="section-head"><div><h2>Waiting for your review</h2><p>Approve the keepers, reject the rest. Nothing is published automatically.</p></div><Link href="/clips?status=review">See all</Link></div>
      {!data ? <div className="clip-grid">{[0, 1, 2, 3].map(i => <div key={i} className="skeleton" style={{ aspectRatio: "9/16" }}/>)}</div>
        : data.review.length === 0 ? <div className="card empty"><b>All caught up</b><span>New clips show up here when a job finishes.</span></div>
        : <div className="clip-grid">{data.review.slice(0, 8).map(c => <ClipCard key={`${c.job_id}-${c.idx}`} clip={c} onOpen={setOpen} onChange={reload}/>)}</div>}
    </section>

    <section className="section">
      <div className="section-head"><div><h2>Recent jobs</h2></div><Link href="/jobs">All jobs</Link></div>
      <div className="card">{jobs.data ? (jobs.data.jobs.length ? <JobRows jobs={jobs.data.jobs.slice(0, 5)}/> : <div className="empty">No jobs yet</div>) : <div className="skeleton" style={{ height: 160, margin: "1rem" }}/>}</div>
    </section>

    <PlayerModal clip={open} onClose={() => setOpen(null)} onChange={reload}/>
  </>;
}
