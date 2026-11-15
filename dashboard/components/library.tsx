"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useData } from "@/lib/api";
import { ClipCard, PlayerModal, type ClipRow } from "./clips";
import { Search } from "./icons";

const TABS = [
  { id: "", label: "All" }, { id: "review", label: "To review" }, { id: "approved", label: "Approved" },
  { id: "rejected", label: "Rejected" },
];

export function Library() {
  const params = useSearchParams();
  const [status, setStatus] = useState(params.get("status") || "");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => { const t = window.setTimeout(() => setQuery(q), 250); return () => window.clearTimeout(t); }, [q]);
  const path = `dashboard/clips?${new URLSearchParams({ ...(status ? { status } : {}), ...(query ? { q: query } : {}) })}`;
  const { data, error, reload } = useData<{ clips: ClipRow[] }>(path, 15000);
  const [open, setOpen] = useState<ClipRow | null>(null);

  return <>
    <header className="page-head"><div><p className="eyebrow">Clip library</p><h1>Every clip, in one place</h1><p>Watch, approve, regenerate, download, or prepare a post.</p></div></header>
    {error && <div className="notice error">{error}</div>}
    <div className="toolbar">
      <div className="chips" role="group" aria-label="Filter clips">{TABS.map(t => <button key={t.id || "all"} className="chip" aria-pressed={status === t.id} onClick={() => setStatus(t.id)}>{t.label}</button>)}</div>
      <label className="search"><span className="sr-only">Search clips</span><Search size={16}/><input className="input" placeholder="Search titles" value={q} onChange={e => setQ(e.target.value)}/></label>
    </div>
    {!data ? <div className="clip-grid">{[0, 1, 2, 3, 4].map(i => <div key={i} className="skeleton" style={{ aspectRatio: "9/16" }}/>)}</div>
      : data.clips.length === 0 ? <div className="card empty"><b>No clips here</b><span>{status === "review" ? "Nothing waiting for review." : "Try another filter."}</span></div>
      : <div className="clip-grid">{data.clips.map(c => <ClipCard key={`${c.job_id}-${c.idx}`} clip={c} onOpen={setOpen} onChange={reload}/>)}</div>}
    <PlayerModal clip={open} onClose={() => setOpen(null)} onChange={reload}/>
  </>;
}
