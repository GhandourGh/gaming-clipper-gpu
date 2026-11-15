"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ago, api, useData } from "@/lib/api";
import { Plus, Refresh, Trash } from "./icons";

type Stream = {
  name: string; platform: string; channel: string; url: string; topic: string; game: string; auto_clip: boolean; clips: number;
  template?: string; captions?: string; gameplay_crop?: string; facecam_size?: string; facecam_position?: string;
  style?: string | null; enabled: boolean; recording: boolean; live?: boolean | null; title?: string | null; error?: string | null;
  chunks: number; last_job?: string | null; checked?: number | null; recording_since?: number | null;
};
type Live = { enabled: boolean; streams: Stream[]; poll_s: number; chunk_s: number; twitch_clips_configured: boolean; free_gb: number };

const since = (epoch?: number | null) => (epoch ? ago(new Date(epoch * 1000).toISOString()) : "");
const clock = (seconds: number) => { const s = Math.max(0, Math.floor(seconds)); const h = Math.floor(s / 3600); const m = Math.floor((s % 3600) / 60); const sec = s % 60; return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`; };

export function LivePage() {
  const { data, error, reload } = useData<Live>("dashboard/live", 10000);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(id); }, []);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setMsg("");
    try { await fn(); await reload(); } catch (e) { setMsg(e instanceof Error ? e.message : "Failed"); }
    setBusy(false);
  }

  async function add(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const form = e.currentTarget;
    await run(async () => {
      await api("dashboard/live", { method: "POST", body: JSON.stringify({
        ref: String(f.get("ref") || "").trim(), topic: String(f.get("topic") || ""), game: String(f.get("game") || ""),
        auto_clip: f.get("auto") === "yes", clips: Number(f.get("clips") || 3), rights_confirmed: f.get("rights") === "yes",
        template: String(f.get("template") || "gta6_gameplay"), captions: String(f.get("captions") || "off"),
        gameplay_crop: String(f.get("gameplay_crop") || "auto"), facecam_size: String(f.get("facecam_size") || "medium"),
        facecam_position: String(f.get("facecam_position") || "top_left") }) });
      form.reset();
    });
  }

  const patch = (s: Stream, body: Partial<Stream>) => run(() => api(`dashboard/live/${s.name}`, { method: "PUT", body: JSON.stringify(body) }));
  const remove = (s: Stream) => { if (confirm(`Stop and remove ${s.name}? Recorded chunks already turned into jobs stay.`)) run(() => api(`dashboard/live/${s.name}`, { method: "DELETE" })); };

  return <>
    <header className="page-head"><div><p className="eyebrow">Live</p><h1>Record streams while they are live</h1>
      <p>Checks every {data?.poll_s ?? 90} s. When a channel goes live on your topic it is recorded in {Math.round((data?.chunk_s ?? 1800) / 60)}-minute pieces,
        and each finished piece becomes a clipping job, so clips can be ready while the stream is still going. Nothing is published automatically.</p></div>
      <button className="btn btn-ghost" disabled={busy} onClick={() => run(() => api("dashboard/live/check", { method: "POST", body: "{}" }))}><Refresh/>Check now</button></header>

    {error && <p className="notice error">{error}</p>}
    {msg && <p className="notice error">{msg}</p>}
    {data && !data.enabled && <p className="notice error">Live recording is switched off on the worker (LIVE_ENABLED=0).</p>}
    {data && data.free_gb < 15 && <p className="notice error">Only {data.free_gb} GB of disk is free. Recording pauses below 10 GB.</p>}

    <section className="card card-pad section">
      <div className="card-title"><div><h2>Channels</h2><p>{data ? `${data.streams.length} watched · ${data.free_gb} GB free` : "Loading…"}</p></div></div>
      {data && data.streams.length === 0 && <p className="muted">No channel yet. Add one below.</p>}
      <div style={{ display: "grid", gap: "var(--space-sm)" }}>
        {data?.streams.map(s => <div key={s.name} className="card card-pad" style={{ display: "grid", gap: 6 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <strong>{s.channel}</strong><span className="badge plain">{s.platform}</span>
            {s.recording ? <span className="badge ok live">recording{s.recording_since ? ` · ${since(s.recording_since)}` : ""}</span>
              : s.live ? <span className="badge warn">live, not recording</span>
              : <span className="badge plain">{s.enabled ? "offline" : "paused"}</span>}
            <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
              <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => patch(s, { enabled: !s.enabled })}>{s.enabled ? "Pause" : "Resume"}</button>
              <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => remove(s)} aria-label={`Remove ${s.name}`}><Trash/></button>
            </span>
          </div>
          {s.title && <p className="muted">{s.title}</p>}
          <p className="muted">Topic: {s.topic || "any"} · {s.chunks} pieces made · {s.auto_clip ? `auto-clips ${s.clips} per piece` : "records only"}
            {s.last_job && <> · <Link href={`/jobs/${s.last_job}`} style={{ color: "var(--color-accent)" }}>latest job</Link></>}</p>
          {s.recording && s.recording_since && <div className="live-stats" aria-label={`Recording for ${clock(now / 1000 - s.recording_since)}`}>
            <div><b>Recording for</b><strong className="num">{clock(now / 1000 - s.recording_since)}</strong></div>
            <div><b>Current piece</b><strong className="num">{clock((now / 1000 - s.recording_since) % (data?.chunk_s || 1800))} <small>/ {Math.round((data?.chunk_s || 1800) / 60)} min</small></strong></div>
            <div><b>Last check</b><strong>{s.checked ? since(s.checked) : "—"}</strong></div>
          </div>}
          <p className="muted">Look: {s.template === "gta6_gameplay" || !s.template ? "GTA 6 — Gameplay Focus" : s.template} · captions {s.captions || "off"} · crop {s.gameplay_crop || "auto"}</p>
          <label className="check"><input type="checkbox" checked={s.auto_clip} disabled={busy} onChange={e => patch(s, { auto_clip: e.target.checked })}/><span>Start clipping each finished piece automatically (uses AI credit within your spending limits)</span></label>
          {s.error && <p className="muted">Note: {s.error}</p>}
        </div>)}
      </div>
    </section>

    <section className="card card-pad form-card section">
      <div className="card-title"><div><h2>Add a channel</h2><p>Twitch, Kick or YouTube. Viewers' own Twitch clips are used as a signal when Twitch keys are set ({data?.twitch_clips_configured ? "set" : "not set: add TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET to .env"}).</p></div></div>
      <form className="form" onSubmit={add}>
        <div className="fields">
          <label className="field wide"><span>Channel</span><input name="ref" required placeholder="twitch:name  ·  kick:name  ·  youtube:@handle  ·  channel link"/></label>
          <label className="field"><span>Only while the title mentions <small>(comma separated, empty = always)</small></span><input name="topic" placeholder="gta 6, gta vi"/></label>
          <label className="field"><span>Game <small>(for titles and trends)</small></span><input name="game" placeholder="GTA 6"/></label>
          <label className="field"><span>Clips per piece</span><input name="clips" type="number" min={1} max={8} defaultValue={3}/></label>
          <label className="field"><span>Video layout</span><select name="template" defaultValue="gta6_gameplay"><option value="gta6_gameplay">GTA 6 — Gameplay Focus</option><option value="auto">Auto</option><option value="full">Full screen</option><option value="split_20">Split 20/80</option><option value="split_50">Split 50/50</option></select></label>
          <label className="field"><span>Captions</span><select name="captions" defaultValue="off"><option value="off">Off</option><option value="overlay">On video</option><option value="bar">Own strip</option></select></label>
          <label className="field"><span>Gameplay crop</span><select name="gameplay_crop" defaultValue="auto"><option value="auto">Smart tracking</option><option value="left">Keep left</option><option value="center">Keep center</option><option value="right">Keep right</option><option value="fit">Preserve full frame</option></select></label>
          <label className="field"><span>Facecam</span><select name="facecam_size" defaultValue="medium"><option value="small">Small</option><option value="medium">Medium</option><option value="large">Large</option></select></label>
          <label className="field"><span>Facecam position</span><select name="facecam_position" defaultValue="top_left"><option value="top_left">Upper left</option><option value="top_right">Upper right</option><option value="mid_left">Middle left</option><option value="mid_right">Middle right</option></select></label>
        </div>
        <label className="check"><input type="checkbox" name="auto" value="yes" defaultChecked/><span>Clip each finished piece automatically</span></label>
        <label className="check"><input type="checkbox" name="rights" value="yes" required/><span>I own this channel or have the creator's permission to record and clip it.</span></label>
        <div className="form-actions" style={{ justifyContent: "flex-start" }}><button className="btn btn-primary" disabled={busy}><Plus/>Add channel</button></div>
      </form>
    </section>
  </>;
}
