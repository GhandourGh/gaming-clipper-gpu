"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { ACTIVE, useData } from "@/lib/api";
import type { JobSummary } from "@/lib/types";
import { JobRows } from "./dashboard";
import { Plus, Search } from "./icons";

const FILTERS = [
  { id: "all", label: "All", test: (_: JobSummary) => true },
  { id: "running", label: "Running", test: (j: JobSummary) => ACTIVE.has(j.status) },
  { id: "review", label: "Needs review", test: (j: JobSummary) => j.ready_count > 0 },
  { id: "done", label: "Done", test: (j: JobSummary) => j.status === "done" },
  { id: "failed", label: "Failed", test: (j: JobSummary) => ["failed", "no_highlights", "expired"].includes(j.status) },
];

export function Jobs() {
  const params = useSearchParams();
  const { data, error, reload } = useData<{ jobs: JobSummary[] }>("dashboard/jobs", 6000);
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const jobs = useMemo(() => (data?.jobs || []).filter(j => FILTERS.find(f => f.id === filter)!.test(j) && j.title.toLowerCase().includes(q.toLowerCase())), [data, filter, q]);

  return <>
    <header className="page-head"><div><p className="eyebrow">Jobs</p><h1>All jobs</h1><p>Every video you've sent through the pipeline, newest first.</p></div>
      <Link href="/new" className="btn btn-primary"><Plus/>New job</Link></header>
    {params.get("started") && <div className="notice info">Job started. It appears below within a few seconds and updates live.</div>}
    {error && <div className="notice error">{error}</div>}
    <div className="toolbar">
      <div className="chips" role="group" aria-label="Filter jobs">{FILTERS.map(f => (
        <button key={f.id} className="chip" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
          {f.label}<span className="count">{(data?.jobs || []).filter(f.test).length}</span></button>
      ))}</div>
      <label className="search">
        <span className="sr-only">Search jobs</span><Search size={16}/>
        <input className="input" placeholder="Search by title" value={q} onChange={e => setQ(e.target.value)}/>
      </label>
    </div>
    <div className="card">{!data ? <div className="skeleton" style={{ height: 240, margin: "1rem" }}/> : jobs.length ? <JobRows jobs={jobs} onDeleted={reload}/> :
      <div className="empty"><b>No jobs here</b><span>Try another filter, or start a new job.</span></div>}</div>
  </>;
}
