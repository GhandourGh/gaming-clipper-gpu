"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");
  const response = await fetch(`/api/w/${path.replace(/^\//, "")}`, { ...init, headers, cache: "no-store" });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.map((d: { msg?: string }) => d.msg).join("; ") : body.detail;
    throw new Error(body.error || detail || `Request failed (${response.status})`);
  }
  return body as T;
}

/** Media from the worker (videos, thumbnails, zip) through the dashboard's own server. */
export const media = (path?: string | null) => (path ? `/api/media${path}` : undefined);

/** Fetch with optional polling (paused while the tab is hidden). Keeps the last good data while refreshing. */
export function useData<T>(path: string | null, every = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const alive = useRef(true);
  const load = useCallback(async () => {
    if (!path) return;
    try { const body = await api<T>(path); if (alive.current) { setData(body); setError(""); } }
    catch (reason) { if (alive.current) setError(reason instanceof Error ? reason.message : "Could not load"); }
  }, [path]);
  useEffect(() => {
    alive.current = true;
    load();
    const timer = every ? window.setInterval(() => { if (document.visibilityState === "visible") load(); }, every) : 0;
    return () => { alive.current = false; if (timer) window.clearInterval(timer); };
  }, [load, every]);
  return { data, error, reload: load, setData };
}

export function duration(seconds?: number | null) {
  const total = Math.max(0, Math.round(seconds || 0));
  if (total >= 3600) return `${Math.floor(total / 3600)}h ${Math.floor((total % 3600) / 60)}m`;
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
export function ago(value?: string | null) {
  if (!value) return "";
  const s = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
export const usd = (v?: number | null, digits = 3) => `$${Number(v || 0).toFixed(digits)}`;
export const label = (s?: string | null) => (s || "").replaceAll("_", " ");

export function statusTone(status?: string | null): "ok" | "bad" | "warn" | "info" | "accent" | "" {
  if (!status) return "";
  if (["done", "ready_for_review", "approved", "published", "rendered"].includes(status)) return "ok";
  if (["failed", "qc_failed", "rejected", "expired"].includes(status)) return "bad";
  if (["no_highlights", "draft"].includes(status)) return "warn";
  if (["scheduled"].includes(status)) return "info";
  return "accent";
}
export const ACTIVE = new Set(["created", "ingested", "analyzed", "ranking", "selected", "rendering", "planned", "assets_ready"]);
