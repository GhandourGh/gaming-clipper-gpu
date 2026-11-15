"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { api, usd, useData } from "@/lib/api";
import { Check } from "./icons";

type Costs = {
  budget: Budget;
  days: Array<{ day: string; calls: number; usd: number }>;
  models: Array<{ model: string; calls: number; tokens_in: number; tokens_out: number; usd: number }>;
  kinds: Array<{ kind: string; calls: number; usd: number }>;
  jobs: Array<{ id: string; title: string; created_at: string; calls: number; usd: number; clips: number }>;
  note: string;
};
const KIND: Record<string, string> = { rank: "Screening moments", judge: "Judging the shortlist", director: "Planning edits", review: "AI review of clips",
  moderation: "Safety check", music: "Music lookups (free)", trends: "Trend hints", publish: "Publishing calls" };

export function CostsPage() {
  const { data, error } = useData<Costs>("dashboard/costs", 30000);
  const days: Array<{ day: string; usd: number; calls: number }> = [];
  for (let i = 29; i >= 0; i--) {
    const d = new Date(); d.setDate(d.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    const hit = data?.days.find(x => x.day === key);
    days.push({ day: key, usd: hit?.usd || 0, calls: hit?.calls || 0 });
  }
  const peak = Math.max(...days.map(d => d.usd), 0.0001);
  const total = (data?.models || []).reduce((n, m) => n + m.usd, 0);
  const calls = (data?.models || []).reduce((n, m) => n + m.calls, 0);
  const clips = (data?.jobs || []).reduce((n, j) => n + j.clips, 0);

  return <>
    <header className="page-head"><div><p className="eyebrow">Costs & usage</p><h1>What the AI costs</h1><p>{data?.note || "Estimated at paid Gemini rates."} Rendering and transcription run on this Mac for free.</p></div></header>
    {error && <div className="notice error">{error}</div>}
    {data?.budget && <section className="card card-pad" style={{ marginBottom: "var(--space-md)" }}>
      <div className="card-title"><div><h2>Spending limits</h2><p>Counted since billing started. Change them in <Link href="/settings" style={{ color: "var(--color-accent)" }}>Settings</Link>.</p></div></div>
      <div className="grid-3">{[
        { name: "Today", spent: data.budget.spent.day, limit: data.budget.limits.day },
        { name: "Total", spent: data.budget.spent.total, limit: data.budget.limits.total },
      ].map(b => (
        <div key={b.name}><div style={{ display: "flex", justifyContent: "space-between", fontSize: "var(--text-sm)", marginBottom: ".4rem" }}>
          <b>{b.name}</b><span className="num muted">{usd(b.spent, 3)} of {usd(b.limit, 2)}</span></div>
          <div className="progress"><i style={{ width: `${Math.min(100, (b.spent / b.limit) * 100)}%`, background: b.spent / b.limit > .85 ? "var(--color-danger)" : undefined }}/></div></div>
      ))}
        <div style={{ fontSize: "var(--text-sm)" }}><b>Per job</b><p className="muted num" style={{ marginTop: ".4rem" }}>max {usd(data.budget.limits.job, 2)}</p></div></div>
    </section>}
    <section className="kpis">
      <div className="card kpi"><span>All time (estimate)</span><strong className="num">{usd(total, 3)}</strong><small>Includes earlier free-tier usage</small></div>
      <div className="card kpi"><span>AI calls</span><strong className="num">{calls}</strong><small>Ranking, planning and reviews</small></div>
      <div className="card kpi"><span>Per clip</span><strong className="num">{clips ? usd(total / clips, 3) : "—"}</strong><small>{clips} clips made</small></div>
      <div className="card kpi"><span>Last 30 days</span><strong className="num">{usd(days.reduce((n, d) => n + d.usd, 0), 3)}</strong><small>{days.reduce((n, d) => n + d.calls, 0)} calls</small></div>
    </section>
    <section className="card card-pad section">
      <div className="card-title"><div><h2>Daily estimate, last 30 days</h2></div></div>
      <div className="bars" role="img" aria-label="Daily AI cost estimate for the last 30 days">{days.map(d => (
        <div key={d.day} title={`${d.day}: ${usd(d.usd, 4)} · ${d.calls} calls`}><i style={{ height: `${(d.usd / peak) * 100}%` }}/></div>
      ))}</div>
      <div className="bars-axis"><span>{days[0].day.slice(5)}</span><span>today</span></div>
    </section>
    <div className="grid-2 section">
      <section className="card"><div className="card-pad" style={{ paddingBottom: 0 }}><h2>By model</h2></div><div className="table-wrap"><table>
        <thead><tr><th>Model</th><th className="num">Calls</th><th className="num">Tokens in / out</th><th className="num">USD</th></tr></thead>
        <tbody>{data?.models.map(m => <tr key={m.model}><td>{m.model}</td><td className="num">{m.calls}</td><td className="num">{m.tokens_in.toLocaleString()} / {m.tokens_out.toLocaleString()}</td><td className="num">{usd(m.usd, 4)}</td></tr>)}</tbody>
      </table></div></section>
      <section className="card"><div className="card-pad" style={{ paddingBottom: 0 }}><h2>By step</h2></div><div className="table-wrap"><table>
        <thead><tr><th>Step</th><th className="num">Calls</th><th className="num">USD</th></tr></thead>
        <tbody>{data?.kinds.map(k => <tr key={k.kind}><td>{KIND[k.kind] || k.kind}</td><td className="num">{k.calls}</td><td className="num">{usd(k.usd, 4)}</td></tr>)}</tbody>
      </table></div></section>
    </div>
    <section className="card section"><div className="card-pad" style={{ paddingBottom: 0 }}><h2>By job</h2></div><div className="table-wrap"><table>
      <thead><tr><th>Job</th><th>Date</th><th className="num">Clips</th><th className="num">Calls</th><th className="num">USD</th></tr></thead>
      <tbody>{data?.jobs.map(j => <tr key={j.id}><td><Link href={`/jobs/${j.id}`} style={{ color: "var(--color-accent)", fontWeight: 600 }}>{j.title || j.id.slice(0, 8)}</Link></td>
        <td className="muted">{new Date(j.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</td><td className="num">{j.clips}</td><td className="num">{j.calls}</td><td className="num">{usd(j.usd, 4)}</td></tr>)}</tbody>
    </table></div></section>
  </>;
}

type Settings = { style: string; clips: number | null; min_seconds: number | null; max_seconds: number | null; cut_mode: string;
  review_min: number; review_redos: number; screen_model: string; main_model: string; judge_top: number;
  budget_job: number; budget_day: number; budget_total: number; retention_days: number; telegram_notify: boolean;
  music_enabled: boolean; music_gain_db: number; music_duck_db: number; detect_burned_captions: boolean; fallback_enabled: boolean;
  trends_enabled: boolean; trends_provider: string; moderation_enabled: boolean; publishing_enabled: boolean; publish_provider: string; render_engine?: string; watch_enabled?: boolean; watch_channels?: string[]; captions?: string; transcript_scoring?: boolean; dead_air_cut?: boolean };
type Model = { id: string; usd_in: number; usd_out: number };
type SettingsData = { settings: Settings; screen_models: Model[]; main_models: Model[]; styles: Array<{ id: string; label: string }>; integrations: Record<string, boolean> };
const Key = ({ on, name }: { on?: boolean; name: string }) => <span className={`badge ${on ? "ok" : ""}`} title={on ? `${name} key is set in .env` : `No ${name} key in .env`}>{name}: {on ? "key set" : "no key"}</span>;
const Toggle = ({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) => (
  <span style={{ display: "flex", alignItems: "center", gap: ".6rem", minHeight: 42 }}>
    <span className="switch"><input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} aria-label={label}/><i/></span>
    <span className="muted" style={{ fontWeight: 400 }}>{checked ? "On" : "Off"}</span></span>);
export type Budget = { since: string; limits: { job: number; day: number; total: number }; spent: { day: number; total: number }; left: { day: number; total: number } };
type Health = { health: Record<string, { ok: boolean; detail: string }> };

export function SettingsPage() {
  const { data, error } = useData<SettingsData>("dashboard/settings");
  const health = useData<Health>("dashboard/overview", 20000);
  const [s, setS] = useState<Settings | null>(null);
  const [msg, setMsg] = useState(""); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { if (data && !s) setS(data.settings); }, [data, s]);
  useEffect(() => { if (!msg) return; const t = window.setTimeout(() => setMsg(""), 2500); return () => window.clearTimeout(t); }, [msg]);
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setS(prev => (prev ? { ...prev, [k]: v } : prev));
  const save = async (e: FormEvent) => {
    e.preventDefault(); if (!s) return; setBusy(true); setErr("");
    try { const r = await api<{ settings: Settings }>("dashboard/settings", { method: "PUT", body: JSON.stringify(s) }); setS(r.settings); setMsg("Settings saved"); }
    catch (reason) { setErr(reason instanceof Error ? reason.message : "Could not save"); } finally { setBusy(false); }
  };
  const num = (v: string) => (v === "" ? null : Number(v));
  const n8nOk = health.data?.health.n8n?.ok;

  return <>
    <header className="page-head"><div><p className="eyebrow">Settings</p><h1>Set up your clipper</h1><p>Start with the short checklist. Change everyday defaults below; technical controls stay tucked away.</p></div></header>
    {(error || err) && <div className="notice error">{error || err}</div>}
    {!s ? <div className="skeleton" style={{ height: 400 }}/> : <form className="form" onSubmit={save}>
      <section className="card card-pad setup-guide" aria-labelledby="setup-guide-title">
        <div className="setup-guide-head"><div><p className="eyebrow">Short guide</p><h2 id="setup-guide-title">What you actually need</h2><p>Normal YouTube and finished Twitch links need no extra source key.</p></div>
          <span className={`badge ${n8nOk ? "ok" : "warn"}`}>{n8nOk ? "Core system ready" : "Check system below"}</span></div>
        <ol className="setup-steps">
          <li><span>1</span><div><b>Gemini AI — required</b><p>The AI key lives in the n8n credential named “Google Gemini API.” It finds moments and plans every edit.</p></div><em className={n8nOk ? "ready" : ""}>{n8nOk ? "Connected" : "Check n8n"}</em></li>
          <li><span>2</span><div><b>Add a video source</b><p>Paste a YouTube video or finished Twitch VOD on New job. For automatic live Twitch recording, add a Twitch app’s client ID and secret.</p></div><em>No key for VODs</em></li>
          <li><span>3</span><div><b>Optional extras</b><p>Only add these if you use the feature: backup AI, trend research, music, or publishing.</p>
            <div className="chips"><Key on={data?.integrations.llm_openrouter || data?.integrations.llm_groq} name="Backup AI"/><Key on={data?.integrations.trends_shortsintel || data?.integrations.trends_trendsmcp} name="Trends"/><Key on={data?.integrations.publish_ayrshare} name="Publishing"/></div></div><em>Optional</em></li>
        </ol>
        <details className="key-help"><summary>How to add a key</summary><div><p>1. Create the key on the provider’s website. 2. Add it to the hosted app’s <code>.env</code> file using the exact name below. 3. Restart the worker. Keys are never displayed on this page.</p>
          <p><code>OPENROUTER_API_KEY</code> or <code>GROQ_API_KEY</code> · <code>SHORTSINTEL_API_KEY</code> or <code>TRENDSMCP_API_KEY</code> · <code>TWITCH_CLIENT_ID</code> + <code>TWITCH_CLIENT_SECRET</code> · <code>AYRSHARE_API_KEY</code></p></div></details>
      </section>
      <section className="card card-pad form-card">
        <div><p className="eyebrow">Everyday</p><h2>New job defaults</h2><p>Starting values on the New job page. Each job can still override them.</p></div>
        <div className="fields">
          <label className="field"><span>Editing style</span><select value={s.style} onChange={e => set("style", e.target.value)}>{data?.styles.map(x => <option key={x.id} value={x.id}>{x.label}</option>)}</select></label>
          <label className="field"><span>Number of clips</span><select value={s.clips ?? ""} onChange={e => set("clips", num(e.target.value))}><option value="">AI decides</option>{[1, 2, 3, 4, 5, 6, 7, 8].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
          <label className="field"><span>Shortest clip <small>(seconds, empty = AI decides)</small></span><input type="number" min={8} max={150} value={s.min_seconds ?? ""} onChange={e => set("min_seconds", num(e.target.value))}/></label>
          <label className="field"><span>Longest clip <small>(seconds)</small></span><input type="number" min={10} max={180} value={s.max_seconds ?? ""} onChange={e => set("max_seconds", num(e.target.value))}/></label>
          <label className="field wide"><span>Cutting approach</span>
            <select value={s.cut_mode} onChange={e => set("cut_mode", e.target.value)}>
              <option value="auto">AI decides from the video type (recommended)</option><option value="continuous">Continuous: story mode, almost no cuts</option>
              <option value="light">Light: a few cuts for downtime</option><option value="tight">Tight: fast cuts (podcasts, IRL streams)</option></select>
            <small>Saved now; the editor starts using it with the planned cutting update.</small></label>
        </div>
      </section>
      <details className="settings-group">
        <summary><span><b>AI quality & spending</b><small>Models, automatic redos, and hard cost limits</small></span><i>Advanced</i></summary>
        <div className="settings-group-body"><section className="card card-pad form-card">
        <div><h2>AI</h2><p>Model and the quality bar for automatic redos.</p></div>
        <div className="fields">
          <label className="field"><span>Screening model <small>(watches every candidate)</small></span><select value={s.screen_model} onChange={e => set("screen_model", e.target.value)}>
            {data?.screen_models.map(m => <option key={m.id} value={m.id}>{m.id} (${m.usd_in} / ${m.usd_out} per 1M)</option>)}</select></label>
          <label className="field"><span>Main model <small>(picks moments, edits, reviews)</small></span><select value={s.main_model} onChange={e => set("main_model", e.target.value)}>
            {data?.main_models.map(m => <option key={m.id} value={m.id}>{m.id} (${m.usd_in} / ${m.usd_out} per 1M)</option>)}</select>
            <small>Prices are estimates from Google's pricing page; 3.8 Flash doubles on Jan 1, 2027.</small></label>
          <label className="field"><span>Moments re-judged by the main model</span><select value={s.judge_top} onChange={e => set("judge_top", Number(e.target.value))}>
            {[0, 1, 2, 3, 4, 5, 6].map(n => <option key={n} value={n}>{n === 0 ? "None (screening only)" : `Top ${n}`}</option>)}</select></label>
          <label className="field"><span>Redo a clip below <small>(AI review score /10)</small></span><input type="number" step={0.5} min={0} max={10} value={s.review_min} onChange={e => set("review_min", Number(e.target.value))}/></label>
          <label className="field"><span>Automatic redos per clip</span><select value={s.review_redos} onChange={e => set("review_redos", Number(e.target.value))}>
            <option value={0}>0 (fastest)</option><option value={1}>1</option><option value={2}>2</option></select><small>Each redo is another full render.</small></label>
        </div>
        </section>
      <section className="card card-pad form-card">
        <div><h2>Spending limits</h2><p>Hard caps on AI spend (estimated at paid rates). Work that would cross a limit isn't started.</p></div>
        <div className="fields">
          <label className="field"><span>Per job <small>(USD)</small></span><input type="number" step={0.01} min={0.01} value={s.budget_job} onChange={e => set("budget_job", Number(e.target.value))}/>
            <small>Fewer clips are made if a job would cost more.</small></label>
          <label className="field"><span>Per day <small>(USD)</small></span><input type="number" step={0.05} min={0.01} value={s.budget_day} onChange={e => set("budget_day", Number(e.target.value))}/></label>
          <label className="field"><span>Total <small>(USD, since billing started)</small></span><input type="number" step={0.5} min={0.01} value={s.budget_total} onChange={e => set("budget_total", Number(e.target.value))}/>
            <small>Keep this below your prepaid balance.</small></label>
        </div>
      </section></div></details>
      <details className="settings-group">
        <summary><span><b>Optional services & maintenance</b><small>Backup providers, trends, publishing, storage, and alerts</small></span><i>Optional</i></summary>
        <div className="settings-group-body"><section className="card card-pad form-card">
        <div><h2>Rendering, music & captions</h2><p>How clips are drawn, licensed background music ducked under speech, and a check for subtitles already in the video.</p></div>
        <div className="fields">
          <label className="field wide"><span>Watch YouTube channels <small>(new uploads and finished streams)</small></span>
            <Toggle checked={!!s.watch_enabled} onChange={v => set("watch_enabled", v)} label="Watch channels"/>
            <textarea rows={3} placeholder={"@streamer\nhttps://www.youtube.com/@another\nUCxxxxxxxxxxxxxxxxxxxxxx"} value={(s.watch_channels ?? []).join("\n")}
              onChange={e => set("watch_channels", e.target.value.split("\n").map(x => x.trim()).filter(Boolean).slice(0, 30))}/>
            <small>Checked every 30 min. You get a Telegram message with a link that opens a pre-filled New job. Nothing is downloaded or clipped until you start the job.</small></label>
          <label className="field"><span>Read the transcript first</span><Toggle checked={s.transcript_scoring ?? true} onChange={v => set("transcript_scoring", v)} label="Transcript scoring"/>
            <small>One cheap text call (~$0.005) scores every moment 0–100 before any video is judged.</small></label>
          <label className="field"><span>Cut dead air</span><Toggle checked={s.dead_air_cut ?? true} onChange={v => set("dead_air_cut", v)} label="Cut dead air"/>
            <small>Removes stretches inside a clip where nobody talks and nothing happens.</small></label>
          <label className="field"><span>Captions <small>(default for new jobs)</small></span><select value={s.captions ?? "overlay"} onChange={e => set("captions", e.target.value)}>
            <option value="overlay">On the video</option><option value="bar">In a strip between streamer and game</option><option value="off">No captions (faster)</option></select>
            <small>The strip keeps text off the gameplay and the webcam (webcam-on-top layout). Off skips caption timing, so clips finish sooner.</small></label>
          <label className="field"><span>Render engine</span><select value={s.render_engine ?? "fast"} onChange={e => set("render_engine", e.target.value)}>
            <option value="fast">Fast (ffmpeg, about 10 s per clip)</option><option value="remotion">Remotion (Chrome, about 4 min per clip)</option></select>
            <small>Both draw the same layouts, zooms and captions. If Fast ever fails, that clip is redone with Remotion automatically.</small></label>
          <label className="field"><span>Background music</span><Toggle checked={s.music_enabled} onChange={v => set("music_enabled", v)} label="Background music"/>
            <span className="chips"><Key on={data?.integrations.music_freesound} name="Freesound"/><Key on={data?.integrations.music_jamendo} name="Jamendo"/><Key on name="Openverse (no key needed)"/></span></label>
          <label className="field"><span>Detect burned-in captions</span><Toggle checked={s.detect_burned_captions} onChange={v => set("detect_burned_captions", v)} label="Detect burned-in captions"/>
            <small>On-device text recognition, ~15 s per video.</small></label>
          <label className="field"><span>Music level <small>(dB)</small></span><input type="number" step={1} min={-40} max={-10} value={s.music_gain_db} onChange={e => set("music_gain_db", Number(e.target.value))}/></label>
          <label className="field"><span>Extra ducking under speech <small>(dB)</small></span><input type="number" step={1} min={-30} max={0} value={s.music_duck_db} onChange={e => set("music_duck_db", Number(e.target.value))}/></label>
        </div>
      </section>
      <section className="card card-pad form-card">
        <div><h2>AI extras</h2><p>Backup AI when Gemini is over quota, trend hints for hooks, and an optional safety check.</p></div>
        <div className="fields">
          <label className="field"><span>Backup AI (OpenRouter, then Groq)</span><Toggle checked={s.fallback_enabled} onChange={v => set("fallback_enabled", v)} label="Backup AI"/>
            <span className="chips"><Key on={data?.integrations.llm_openrouter} name="OpenRouter"/><Key on={data?.integrations.llm_groq} name="Groq"/></span>
            <small>Only used when Gemini fails with a quota or server error. Counts toward your spending limits.</small></label>
          <label className="field"><span>Trend hints</span><Toggle checked={s.trends_enabled} onChange={v => set("trends_enabled", v)} label="Trend hints"/>
            <select value={s.trends_provider} onChange={e => set("trends_provider", e.target.value)} aria-label="Trend provider">
              <option value="auto">Auto (ShortsIntel, then TrendsMCP)</option><option value="shortsintel">ShortsIntel</option><option value="trendsmcp">TrendsMCP</option></select>
            <span className="chips"><Key on={data?.integrations.trends_shortsintel} name="ShortsIntel"/><Key on={data?.integrations.trends_trendsmcp} name="TrendsMCP"/></span></label>
          <label className="field wide"><span>Safety check of finished clips (Gemini)</span><Toggle checked={s.moderation_enabled} onChange={v => set("moderation_enabled", v)} label="Safety check"/>
            <small>Flags nudity, graphic gore, hate, self-harm or drug promotion; normal game violence passes. A flagged clip fails QC. About $0.003 per clip.</small></label>
        </div>
      </section>
      <section className="card card-pad form-card">
        <div><h2>Publishing</h2><p>Every post needs your Approve, then “Send as draft” (held, not live). Publish stays locked until you turn it on here.</p></div>
        <div className="fields">
          <label className="field"><span>Allow publishing</span><Toggle checked={s.publishing_enabled} onChange={v => set("publishing_enabled", v)} label="Allow publishing"/>
            <small>Even when on, nothing posts automatically: you click Publish per post.</small></label>
          <label className="field"><span>Provider</span><select value={s.publish_provider} onChange={e => set("publish_provider", e.target.value)}>
            <option value="ayrshare">Ayrshare</option><option value="postlake">PostLake (not integrated yet)</option><option value="publora">Publora (not integrated yet)</option></select>
            <span className="chips"><Key on={data?.integrations.publish_ayrshare} name="Ayrshare"/></span>
            <small>Ayrshare: video posts need its paid plan.</small></label>
        </div>
      </section></div></details>
      <section className="card card-pad form-card">
        <div><h2>Storage & alerts</h2><p>Old job files are cleaned up daily at 04:00.</p></div>
        <div className="fields">
          <label className="field"><span>Keep job files for <small>(days)</small></span><input type="number" min={1} max={90} value={s.retention_days} onChange={e => set("retention_days", Number(e.target.value))}/></label>
          <label className="field"><span>Telegram messages</span><span style={{ display: "flex", alignItems: "center", gap: ".6rem", minHeight: 42 }}>
            <span className="switch"><input type="checkbox" checked={s.telegram_notify} onChange={e => set("telegram_notify", e.target.checked)} aria-label="Telegram messages"/><i/></span>
            <span className="muted" style={{ fontWeight: 400 }}>{s.telegram_notify ? "On" : "Off"}</span></span>
            <small>Saved now; the n8n workflows start reading it in the next workflow update.</small></label>
        </div>
      </section>
      <div className="form-actions"><button className="btn btn-primary btn-lg" disabled={busy}><Check/>{busy ? "Saving…" : "Save settings"}</button></div>
    </form>}
    <section className="card card-pad section">
      <div className="card-title"><div><h2>System</h2><p>Everything runs on this Mac.</p></div></div>
      <div className="health">{health.data ? Object.entries(health.data.health).map(([k, h]) => (
        <div key={k}><span className={`dot ${h.ok ? "" : "bad"}`}/><span style={{ minWidth: 0 }}><b style={{ textTransform: "capitalize" }}>{k}</b><span>{h.detail}</span></span></div>)) : null}</div>
    </section>
    {msg && <div className="toast" role="status">{msg}</div>}
  </>;
}
