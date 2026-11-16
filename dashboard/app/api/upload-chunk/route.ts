import { validSession } from "@/lib/session";

// One piece of a big video (see lib/chunked-upload.ts). Runpod's web address cuts any single request after ~100 s or
// ~100 MB, so the browser sends a 1+ GB file as 16 MB pieces. This route is not behind proxy.ts (its 10 MB body buffer would
// cut a piece off; the path starts with api/upload, which the proxy matcher skips), so it checks the session itself.
// x-action: "chunk" (body = the piece), "status" (which pieces the Pod already has), "finish" (stitch them together).
const worker = process.env.CLIPPER_WORKER_URL || "http://127.0.0.1:8787";
const ID = /^[A-Za-z0-9_.-]{6,100}$/;

export async function POST(request: Request) {
  if (!(await validSession(request))) return Response.json({ error: "Dashboard session required" }, { status: 401 });
  const h = request.headers;
  const action = h.get("x-action") || "chunk";
  const id = h.get("x-upload-id") || "";
  const total = Number(h.get("x-chunk-total"));
  const name = decodeURIComponent(h.get("x-filename") || "").split(/[\\/]/).pop()?.replace(/[^\w.\- ()]/g, "_") || "";
  if (!ID.test(id) || !Number.isInteger(total) || total < 1 || total > 4000 || !/\.(mp4|mov|mkv|webm|m4v)$/i.test(name)) {
    return Response.json({ error: "Choose a video file (MP4, MOV, MKV, WebM)." }, { status: 400 });
  }
  const headers: Record<string, string> = { "X-Clipper-Token": process.env.CLIPPER_TOKEN || "" };
  const q = new URLSearchParams({ upload_id: id, total: String(total), name });
  try {
    let response: Response;
    if (action === "status") {
      response = await fetch(`${worker}/upload/status?${q}`, { headers });
    } else if (action === "finish") {
      response = await fetch(`${worker}/upload/finish?${q}`, { method: "POST", headers });
    } else {
      const index = Number(h.get("x-chunk-index"));
      if (!Number.isInteger(index) || index < 0 || index >= total) return Response.json({ error: "bad piece number" }, { status: 400 });
      q.set("index", String(index));
      response = await fetch(`${worker}/upload/chunk?${q}`, { method: "POST", headers, body: await request.arrayBuffer() });
    }
    return new Response(await response.text(), { status: response.status, headers: { "content-type": response.headers.get("content-type") || "application/json" } });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Upload failed" }, { status: 502 });
  }
}
