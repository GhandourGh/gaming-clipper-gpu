"use client";

import { FormEvent, Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";

function LoginForm() {
  const params = useSearchParams();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true); setError("");
    const key = String(new FormData(e.currentTarget).get("key") || "");
    const r = await fetch("/api/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ key }) });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) { setBusy(false); setError(body.error || "Could not sign in"); return; }
    const next = params.get("next");
    window.location.href = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
  }
  return <form onSubmit={submit} className="card" style={{ maxWidth: 420, margin: "12vh auto", padding: "1.5rem", display: "grid", gap: "1rem" }}>
    <div><p className="eyebrow">Clipper</p><h1 style={{ margin: 0 }}>Sign in</h1>
      <p className="muted">Enter the dashboard access key (DASHBOARD_PASSWORD, or CLIPPER_TOKEN from your .env).</p></div>
    <input name="key" type="password" autoComplete="current-password" placeholder="Access key" required autoFocus/>
    {error && <div className="notice error" role="alert">{error}</div>}
    <button className="btn btn-primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
  </form>;
}

export default function Page() {
  return <Suspense><LoginForm/></Suspense>;
}
