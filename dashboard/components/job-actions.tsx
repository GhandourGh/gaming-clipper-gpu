"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { Refresh, Trash } from "./icons";

/** Delete a job completely, or start it over from the original video. Both ask first. */
export function deleteJob(id: string, title: string): Promise<boolean> {
  if (!window.confirm(`Delete "${title}"?\n\nThis permanently removes the job, its clips, analysis and files. It cannot be undone.`)) return Promise.resolve(false);
  const source = window.confirm("Also delete the uploaded video from the inbox folder?\n\nOK = delete the video too, Cancel = keep the video file.");
  return api(`dashboard/jobs/${id}${source ? "?source=true" : ""}`, { method: "DELETE" }).then(() => true);
}

export function JobActions({ id, title, running, onChange }: { id: string; title: string; running: boolean; onChange?: () => void }) {
  const router = useRouter();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function startOver() {
    if (!window.confirm(`Start "${title}" over?\n\nThe clips, edits and analysis are discarded and the whole pipeline runs again on the same video.`)) return;
    setBusy("restart"); setError("");
    try {
      const r = await fetch(`/api/restart/${id}`, { method: "POST" });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.error || body.detail || `Request failed (${r.status})`);
      onChange?.(); router.refresh();
    }
    catch (e) { setError(e instanceof Error ? e.message : "Could not restart the job"); }
    finally { setBusy(""); }
  }
  async function call(path: string, what: string, ask?: string) {
    if (ask && !window.confirm(ask)) return;
    setBusy(what); setError("");
    try { await api(path, { method: "POST" }); onChange?.(); router.refresh(); }
    catch (e) { setError(e instanceof Error ? e.message : `Could not ${what} the job`); }
    finally { setBusy(""); }
  }
  async function remove() {
    setBusy("delete"); setError("");
    try { if (await deleteJob(id, title)) router.push("/jobs"); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not delete the job"); }
    finally { setBusy(""); }
  }

  return <div style={{ display: "flex", gap: "var(--space-sm)", flexWrap: "wrap", alignItems: "center" }}>
    {running && <button className="btn" onClick={() => call(`jobs/${id}/cancel`, "cancel", `Cancel "${title}"? Steps already finished are kept.`)} disabled={!!busy}><Trash size={16}/>{busy === "cancel" ? "Cancelling…" : "Cancel"}</button>}
    {!running && <button className="btn" onClick={() => call(`jobs/${id}/retry`, "retry")} disabled={!!busy} title="Retry failed steps; finished work is reused"><Refresh size={16}/>{busy === "retry" ? "Retrying…" : "Retry failed"}</button>}
    <button className="btn" onClick={startOver} disabled={!!busy || running} title={running ? "Wait for the job to finish" : "Run the whole pipeline again on this video"}><Refresh size={16}/>{busy === "restart" ? "Restarting…" : "Start over"}</button>
    <button className="btn" onClick={remove} disabled={!!busy} title="Delete this job and its files"><Trash size={16}/>{busy === "delete" ? "Deleting…" : "Delete"}</button>
    {error && <span className="notice error" role="alert" style={{ margin: 0 }}>{error}</span>}
  </div>;
}
