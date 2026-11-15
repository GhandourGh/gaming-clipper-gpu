"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, label, media, statusTone, useData } from "@/lib/api";
import type { ClipRow } from "./clips";
import { Calendar, Check, Lock, Plus, Trash } from "./icons";

export type Account = { platform: Platform; label: string; title_max: number; caption_max: number; needs: string[]; connected: boolean };
type Platform = "youtube" | "instagram" | "tiktok";
type Post = { id: string; job_id: string; idx: number; platform: Platform; title: string; caption: string; hashtags: string; status: string;
  scheduled_at: string | null; created_at: string; clip: ClipRow | null; provider?: string | null; external_id?: string | null; error?: string | null };
type Gate = { provider: string; provider_configured: boolean; publishing_enabled: boolean };
const STATUS_LABEL: Record<string, string> = { draft: "Draft", scheduled: "Planned", approved: "Approved", sent: "Held at provider", published: "Published" };

async function runAction(id: string, action: "send" | "publish") {
  const r = await fetch("/api/publish", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ post_id: id, action }) });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `Failed (${r.status})`);
  return body;
}
const SHORT: Record<Platform, string> = { youtube: "YT", instagram: "IG", tiktok: "TT" };
export const PlatformIcon = ({ p }: { p: Platform }) => <span className={`pf pf-${p}`} aria-hidden="true">{SHORT[p]}</span>;

function localInput(iso: string | null) {
  if (!iso) return "";
  const d = new Date(iso); d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

function PostEditor({ post, account, gate, onChange, toast }: { post: Post; account?: Account; gate?: Gate; onChange: () => void; toast: (m: string) => void }) {
  const [title, setTitle] = useState(post.title || "");
  const [caption, setCaption] = useState(post.caption || "");
  const [tags, setTags] = useState(post.hashtags || "");
  const [when, setWhen] = useState(localInput(post.scheduled_at));
  const [busy, setBusy] = useState(false);
  const max = account?.caption_max || 2200;
  const total = caption.length + (tags ? tags.length + 2 : 0);
  const save = async () => {
    setBusy(true);
    try {
      await api(`dashboard/posts/${post.id}`, { method: "PUT", body: JSON.stringify({ title, caption, hashtags: tags,
        scheduled_at: when ? new Date(when).toISOString() : null, status: when ? "scheduled" : "draft" }) });
      toast("Saved"); onChange();
    } catch (e) { toast(e instanceof Error ? e.message : "Could not save"); } finally { setBusy(false); }
  };
  const locked = post.status === "sent" || post.status === "published";
  const step = async (name: string, fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try { await fn(); toast(ok); onChange(); } catch (e) { toast(e instanceof Error ? e.message : `${name} failed`); } finally { setBusy(false); }
  };
  const approve = () => step("Approve", async () => { await save(); await api(`dashboard/posts/${post.id}/approve`, { method: "POST" }); }, "Approved");
  const send = () => step("Send", () => runAction(post.id, "send"), `Held as a draft at ${gate?.provider}`);
  const publish = () => { if (confirm(`Publish this post to ${account?.label || post.platform} now? It goes live.`)) step("Publish", () => runAction(post.id, "publish"), "Published"); };
  const remove = async () => { if (!confirm("Delete this draft? The clip itself is kept.")) return; await api(`dashboard/posts/${post.id}`, { method: "DELETE" }); onChange(); };

  return <div className="post">
    <span className="thumb">{post.clip?.thumbnail ? <img src={media(post.clip.thumbnail)} alt=""/> : null}</span>
    <div style={{ display: "grid", gap: ".6rem", minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
        <PlatformIcon p={post.platform}/><b>{account?.label || post.platform}</b>
        <span className={`badge ${statusTone(post.status === "sent" ? "scheduled" : post.status === "approved" ? "rendered" : post.status)}`}>{STATUS_LABEL[post.status] || label(post.status)}</span>
        {post.clip && <Link href={`/jobs/${post.job_id}`} className="muted" style={{ fontSize: "var(--text-xs)" }}>{post.clip.title}</Link>}
      </div>
      {post.platform === "youtube" && <label className="field"><span>Title <span className={`counter ${title.length > 100 ? "over" : ""}`}>{title.length}/100</span></span>
        <input value={title} maxLength={100} disabled={locked} onChange={e => setTitle(e.target.value)}/></label>}
      <label className="field"><span>Caption <span className={`counter ${total > max ? "over" : ""}`}>{total}/{max}</span></span>
        <textarea value={caption} disabled={locked} onChange={e => setCaption(e.target.value)}/></label>
      <div className="fields">
        <label className="field"><span>Hashtags</span><input value={tags} disabled={locked} onChange={e => setTags(e.target.value)} placeholder="#gta6 #gaming"/></label>
        <label className="field"><span>Planned for <small>(reminder only)</small></span><input type="datetime-local" value={when} onChange={e => setWhen(e.target.value)}/></label>
      </div>
    </div>
    <div style={{ display: "grid", gap: ".45rem", alignContent: "start" }}>
      {!locked && <button className="btn btn-sm" onClick={async () => { await save(); }} disabled={busy}><Check size={16}/>Save</button>}
      {(post.status === "draft" || post.status === "scheduled") && <button className="btn btn-sm btn-success" onClick={approve} disabled={busy}>Approve</button>}
      {post.status === "approved" && <button className="btn btn-sm btn-primary" onClick={send} disabled={busy || !gate?.provider_configured}
        title={gate?.provider_configured ? "Uploads the clip and creates a held post (not live)" : `${gate?.provider} is not configured`}>Send as draft</button>}
      {post.status === "sent" && <button className="btn btn-sm btn-primary" onClick={publish} disabled={busy || !gate?.publishing_enabled}
        title={gate?.publishing_enabled ? "Makes the held post live" : "Publishing is locked: enable it in Settings"}>{gate?.publishing_enabled ? null : <Lock size={14}/>}Publish</button>}
      {!locked && <button className="btn btn-sm btn-ghost" onClick={remove} aria-label="Delete draft"><Trash size={16}/></button>}
      {post.error && <span className="muted" style={{ fontSize: "var(--text-xs)", color: "var(--color-danger)", maxWidth: 160 }}>{post.error}</span>}
    </div>
  </div>;
}

export function Publish() {
  const params = useSearchParams();
  const router = useRouter();
  const { data, error, reload } = useData<{ posts: Post[]; accounts: Account[] } & Gate>("dashboard/posts");
  const library = useData<{ clips: ClipRow[] }>("dashboard/clips");
  const [tab, setTab] = useState("draft");
  const [msg, setMsg] = useState("");
  const [pick, setPick] = useState<{ job: string; idx: number } | null>(null);
  const [platforms, setPlatforms] = useState<Platform[]>(["youtube", "instagram", "tiktok"]);
  useEffect(() => { const j = params.get("job"), i = params.get("idx"); if (j && i) setPick({ job: j, idx: Number(i) }); }, [params]);
  useEffect(() => { if (!msg) return; const t = window.setTimeout(() => setMsg(""), 2500); return () => window.clearTimeout(t); }, [msg]);

  const create = async () => {
    if (!pick) return;
    try {
      for (const p of platforms) await api("dashboard/posts", { method: "POST", body: JSON.stringify({ job_id: pick.job, idx: pick.idx, platform: p }) });
      setPick(null); setTab("draft"); router.replace("/publish"); setMsg(`${platforms.length} draft${platforms.length > 1 ? "s" : ""} created`); reload();
    } catch (e) { setMsg(e instanceof Error ? e.message : "Could not create drafts"); }
  };
  const TABS: Record<string, string[]> = { draft: ["draft", "scheduled"], approved: ["approved"], sent: ["sent"], published: ["published"] };
  const posts = (data?.posts || []).filter(p => TABS[tab].includes(p.status));
  const acc = (p: Platform) => data?.accounts.find(a => a.platform === p);
  const connected = data?.accounts.filter(a => a.connected).length ?? 0;

  return <>
    <header className="page-head"><div><p className="eyebrow">Publishing</p><h1>Posts</h1><p>Prepare titles, captions and hashtags for each platform. Nothing is ever posted without your click.</p></div></header>
    {error && <div className="notice error">{error}</div>}
    {data && <div className={`notice ${data.provider_configured && data.publishing_enabled ? "info" : "warn"}`}>
      <b>How posting works:</b> Approve a draft → <b>Send as draft</b> uploads it to {data.provider} and holds it (not live) → <b>Publish</b> makes it live.
      {!data.provider_configured && <> {data.provider} is not configured yet (its API key goes in .env).</>}
      {!data.publishing_enabled && <> Publishing is locked; you can turn it on in <Link href="/settings" style={{ textDecoration: "underline" }}>Settings</Link>.</>}
      {connected === 0 && null}</div>}

    <section className="card card-pad" style={{ marginBottom: "var(--space-md)" }}>
      <div className="card-title"><div><h2>New post</h2><p>Pick a clip, choose platforms, then edit each draft below.</p></div></div>
      <div className="fields">
        <label className="field"><span>Clip</span>
          <select value={pick ? `${pick.job}:${pick.idx}` : ""} onChange={e => { const [job, idx] = e.target.value.split(":"); setPick(job ? { job, idx: Number(idx) } : null); }}>
            <option value="">Choose a clip</option>
            {pick && !library.data?.clips.some(c => c.job_id === pick.job && c.idx === pick.idx) && <option value={`${pick.job}:${pick.idx}`}>Selected clip</option>}
            {[...(library.data?.clips || [])].sort((a, b) => Number(b.review === "approved") - Number(a.review === "approved")).map(c => <option key={`${c.job_id}:${c.idx}`} value={`${c.job_id}:${c.idx}`}>{c.review === "approved" ? "✓ " : ""}{c.title} · {c.job_title}</option>)}
          </select>
          <small>Approved clips are listed first.</small></label>
        <div className="field"><span>Platforms</span>
          <div className="segmented">{(["youtube", "instagram", "tiktok"] as Platform[]).map(p => (
            <label key={p}><input type="checkbox" checked={platforms.includes(p)} onChange={e => setPlatforms(s => e.target.checked ? [...s, p] : s.filter(x => x !== p))}/>
              <span style={{ gap: ".45rem" }}><PlatformIcon p={p}/>{acc(p)?.label || p}</span></label>
          ))}</div></div>
      </div>
      <div className="form-actions" style={{ marginTop: "var(--space-sm)" }}><button className="btn btn-primary" onClick={create} disabled={!pick || !platforms.length}><Plus/>Create drafts</button></div>
    </section>

    <div className="toolbar"><div className="chips" role="group" aria-label="Post status">
      {[["draft", "Drafts"], ["approved", "Approved"], ["sent", "Held at provider"], ["published", "Published"]].map(([id, l]) => (
        <button key={id} className="chip" aria-pressed={tab === id} onClick={() => setTab(id)}>{l}<span className="count">{(data?.posts || []).filter(p => TABS[id].includes(p.status)).length}</span></button>))}
    </div></div>
    <div className="card">{!data ? <div className="skeleton" style={{ height: 200, margin: "1rem" }}/> : posts.length ? posts.map(p => <PostEditor key={p.id} post={p} account={acc(p.platform)} gate={data} onChange={reload} toast={setMsg}/>) :
      <div className="empty"><Calendar size={28}/><b>{tab === "draft" ? "No drafts" : tab === "scheduled" ? "Nothing planned" : "Nothing published yet"}</b><span>Create a post from an approved clip above.</span></div>}</div>
    {msg && <div className="toast" role="status">{msg}</div>}
  </>;
}

export function Accounts() {
  const { data, error } = useData<{ accounts: Account[] }>("dashboard/accounts");
  return <>
    <header className="page-head"><div><p className="eyebrow">Accounts</p><h1>Connected platforms</h1><p>Connect each platform once to unlock publishing from the Posts page. Posting always needs your click.</p></div></header>
    {error && <div className="notice error">{error}</div>}
    <div className="grid-3">{(data?.accounts || []).map(a => (
      <section key={a.platform} className="card card-pad account">
        <div style={{ display: "flex", alignItems: "center", gap: ".7rem" }}><PlatformIcon p={a.platform}/><h2>{a.label}</h2>
          <span className={`badge ${a.connected ? "ok" : ""}`} style={{ marginLeft: "auto" }}>{a.connected ? "Connected" : "Not connected"}</span></div>
        <div><p className="muted" style={{ fontSize: "var(--text-sm)", marginBottom: ".5rem" }}>What you need first:</p><ul>{a.needs.map(n => <li key={n}>{n}</li>)}</ul></div>
        <p className="muted" style={{ fontSize: "var(--text-sm)" }}>{a.connected
          ? <>Posts go through <b>{(a as Account & { via?: string }).via}</b>. Connect the {a.label.split(" ")[0]} account inside your {(a as Account & { via?: string }).via} dashboard.</>
          : <>Publishing goes through a multi-network service (Ayrshare by default). Add its API key to .env, then connect {a.label.split(" ")[0]} in that service.</>}</p>
      </section>
    ))}</div>
  </>;
}
