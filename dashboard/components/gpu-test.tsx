/Users/macos/.zshenv:.:1: no such file or directory: /Users/macos/.cargo/env
"use client";

import { FormEvent, useState } from "react";
import { api, duration, useData } from "@/lib/api";

type Check = { ok: boolean | string; [k: string]: unknown };
type Gpu = {
  ready: boolean;
  checks: Record<string, Check>;
  gpu: { present: boolean; name?: string; util_pct?: number; mem_used_mb?: number; mem_total_mb?: number; encoder_sessions?: number; temp_c?: number; driver?: string; detail?: string };
  runtime: { pod_id?: string; uptime_s: number; idle_s: number; idle_stop_min: number; stop_in_s?: number | null; queue: number; busy: boolean;
    queue_stats?: { workers: Record<string, number>; running: Record<string, number>; queued: Record<string, number> };
    gpu_usd_per_hour?: number | null; session_cost_usd?: number | null; git_rev?: string | null;
    replacement_gpu_ceiling_usd?: number };
};

const LABEL: Record<string, string> = { postgres: "Database", ffmpeg: "FFmpeg + NVENC", whisper: "Whisper (speech)", llm: "Gemini AI", telegram: "Telegram", render: "Renderer", disk: "Volume disk" };

function detail(k: string, c: Check): string {
  if (k === "ffmpeg") return c.nvenc_test_encode ? `h264_nvenc test encode OK (${c.nvenc_test_seconds}s) · libass ${c.libass ? "yes" : "no"}` : String(c.nvenc_error || c.version || "");
  if (k === "whisper") {
    const loaded = Object.entries((c.loaded as Record<string, string>) || {}).map(([m, d]) => `${m}: ${d}`).join(", ");
    return `${c.cuda_devices ?? 0} CUDA device(s)${loaded ? " · " + loaded : " · loads on first job"}`;
  }
  if (k === "llm") return c.gemini_key ? `Gemini key set${c.openrouter_key ? " · OpenRouter backup" : ""}${c.groq_key ? " · Groq backup" : ""}` : "GEMINI_API_KEY missing on the Pod";
  if (k === "telegram") return c.enabled ? "Notifications on" : "Off (no bot token/chat id, or turned off in Settings)";
  if (k === "render") return `${c.engine}${c.cpu_fallback_allowed ? " · CPU fallback allowed" : " · no CPU fallback"}`;
  if (k === "disk") return `${c.used_pct}% used · ${c.free_gb} GB free of ${c.total_gb} GB`;
  return c.detail ? String(c.detail) : "";
}

export function GpuTest() {
  const { data, error, reload } = useData<Gpu>("dashboard/gpu", 10000);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  async function act(path: string, what: string, method: "GET" | "POST", ask?: string) {
    if (ask && !window.confirm(ask)) return;
    setBusy(what); setMsg("");
    try { await api(path, { method }); setMsg(`${what}: done`); reload(); }
    catch (e) { setMsg(e instanceof Error ? e.message : `${what} failed`); }
    finally { setBusy(""); }
  }
  async function saveKeys(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const values: Record<string, string> = {};
    new FormData(form).forEach((v, k) => { if (String(v).trim()) values[k] = String(v).trim(); });
    if (!Object.keys(values).length) return;
    setBusy("Keys"); setMsg("");
    try { const r = await api<{ stored: string[] }>("admin/env", { method: "POST", body: JSON.stringify({ values }) }); setMsg(`Saved on the Pod: ${r.stored.join(", ")}`); form.reset(); reload(); }
    catch (err) { setMsg(err instanceof Error ? err.message : "Could not save"); }
    finally { setBusy(""); }
  }
  const g = data?.gpu, rt = data?.runtime;
  return <>
    <header className="page-head"><div><p className="eyebrow">GPU worker</p><h1>{g?.present ? g.name : "GPU"}</h1>
      <p>Everything runs here: download, Whisper, moment finding, editing, NVENC rendering, quality checks and storage. This page reads the Pod live.</p></div>
      {data && <span className={`badge ${data.ready ? "ok" : "bad"}`}>{data.ready ? "Ready" : "Not ready"}</span>}</header>
    {(error || msg) && <div className={`notice ${error ? "error" : "info"}`}>{error || msg}</div>}
    {!data ? <div className="skeleton" style={{ height: 300 }}/> : <>
      <section className="card card-pad">
        <div className="live-stats">
          <div><b>GPU load</b><strong className="num">{g?.util_pct ?? "—"}%</strong></div>
          <div><b>VRAM</b><strong className="num">{g?.mem_used_mb != null ? `${Math.round(g.mem_used_mb / 1024)} / ${Math.round((g.mem_total_mb || 0) / 1024)} GB` : "—"}</strong></div>
          <div><b>Encoder sessions</b><strong className="num">{g?.encoder_sessions ?? "—"}</strong></div>
          <div><b>Temperature</b><strong className="num">{g?.temp_c != null ? `${g.temp_c}°C` : "—"}</strong></div>
          <div><b>Jobs</b><strong>{rt?.busy ? `working (${rt.queue} queued)` : "idle"}</strong></div>
          <div><b>Parallel clips</b><strong className="num">{rt?.queue_stats?.workers?.gpu ?? "—"}</strong></div>
          <div><b>Uptime</b><strong className="num">{rt ? duration(rt.uptime_s) : "—"}</strong></div>
          <div><b>Auto-stop</b><strong className="num">{rt?.idle_stop_min ? (rt.busy ? "paused while working" : `in ${duration(rt.stop_in_s || 0)}`) : "off"}</strong></div>
          <div><b>This session</b><strong className="num">{rt?.session_cost_usd != null ? `$${rt.session_cost_usd.toFixed(2)}` : "rate not set"}</strong></div>
        </div>
        <p className="muted" style={{ marginTop: ".8rem", fontSize: "var(--text-sm)" }}>Pod {rt?.pod_id || "—"} · driver {g?.driver || "—"} · code {rt?.git_rev || "—"}.
          {rt?.idle_stop_min ? ` The Pod stops itself after ${rt.idle_stop_min} minutes with no active jobs. An open dashboard tab no longer keeps the GPU billing. Start it again from the Runpod console.`
            : " Auto-stop is off on this host."}</p>
        {rt?.replacement_gpu_ceiling_usd != null && <p className="muted" style={{ marginTop: ".35rem", fontSize: "var(--text-sm)" }}>
          Replacement GPU ceiling: ${rt.replacement_gpu_ceiling_usd.toFixed(2)}/hour.
        </p>}
      </section>
      <section className="card section">
        <div className="card-pad" style={{ paddingBottom: 0 }}><div className="card-title"><div><h2>Checks</h2><p>Proven on this Pod, not assumed: FFmpeg encodes a real test frame with NVENC.</p></div></div></div>
        <div className="rows">{Object.entries(data.checks).map(([k, c]) => (
          <div className="row" key={k} style={{ gridTemplateColumns: "minmax(0,1fr) auto" }}>
            <span><div className="row-title">{LABEL[k] || k}</div><p className="muted" style={{ fontSize: "var(--text-sm)" }}>{detail(k, c)}</p></span>
            <span className={`badge ${c.ok ? "ok" : "bad"}`}>{c.ok ? "OK" : "Problem"}</span></div>))}</div>
      </section>
      <form className="card card-pad section" onSubmit={saveKeys} style={{ display: "grid", gap: ".7rem" }}>
        <div><h2>Keys on the Pod</h2><p className="muted">Stored only in <code>/workspace/clipper.env</code> on the GPU Pod and used right away. Values are never shown again; leave a field empty to keep it.</p></div>
        <input name="GEMINI_API_KEY" type="password" autoComplete="off" placeholder={`Gemini API key ${data.checks.llm?.gemini_key ? "(set)" : "(required)"}`}/>
        <input name="TELEGRAM_BOT_TOKEN" type="password" autoComplete="off" placeholder="Telegram bot token (optional)"/>
        <input name="TELEGRAM_CHAT_ID" autoComplete="off" placeholder="Telegram chat id (optional)"/>
        <input name="OPENROUTER_API_KEY" type="password" autoComplete="off" placeholder="OpenRouter key: backup AI (optional)"/>
        <input name="GPU_USD_PER_HOUR" inputMode="decimal" autoComplete="off" placeholder="GPU price per hour, e.g. 0.27 (for the cost display)"/>
        <div><button className="btn btn-primary" disabled={!!busy}>{busy === "Keys" ? "Saving…" : "Save keys"}</button></div>
      </form>
      <div className="section" style={{ display: "flex", gap: ".6rem", flexWrap: "wrap" }}>
        <button className="btn" disabled={!!busy} onClick={() => act("dashboard/gpu?refresh=1", "Re-check", "GET")}>{busy === "Re-check" ? "Checking…" : "Re-run checks"}</button>
        <button className="btn" disabled={!!busy || rt?.busy} onClick={() => act("admin/update", "Update", "POST", "Pull the latest code from GitHub and restart the worker and dashboard? Takes about a minute.")}>Update code</button>
        <button className="btn" disabled={!!busy || rt?.busy} onClick={() => act("admin/stop-pod", "Stop", "POST", "Stop the GPU Pod now? GPU billing ends; the dashboard goes offline until you start the Pod again in the Runpod console.")}>Stop Pod now</button>
      </div>
    </>}
  </>;
}
