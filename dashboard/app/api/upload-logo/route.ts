import { validSession } from "@/lib/session";

const worker = process.env.CLIPPER_WORKER_URL || "http://127.0.0.1:8787";
const IMAGE = /\.(png|webp|jpe?g)$/i;

export async function POST(request: Request) {
  if (!(await validSession(request))) return Response.json({ error: "Dashboard session required" }, { status: 401 });
  const name = decodeURIComponent(request.headers.get("x-filename") || "").split(/[\\/]/).pop()?.replace(/[^\w.\- ()]/g, "_") || "";
  if (!IMAGE.test(name)) return Response.json({ error: "Choose a PNG, WebP, or JPG logo." }, { status: 400 });
  const size = Number(request.headers.get("content-length") || 0);
  if (size > 8 * 1024 * 1024) return Response.json({ error: "Logo must be smaller than 8 MB." }, { status: 413 });
  if (!request.body) return Response.json({ error: "The upload was empty." }, { status: 400 });
  const token = process.env.CLIPPER_TOKEN || "";
  const form = new FormData();
  form.set("file", new File([await request.blob()], name, { type: request.headers.get("content-type") || "application/octet-stream" }));
  try {
    const response = await fetch(`${worker}/upload-logo`, { method: "POST", body: form, headers: { "X-Clipper-Token": token } });
    return new Response(await response.text(), { status: response.status, headers: { "content-type": response.headers.get("content-type") || "application/json" } });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Logo upload failed" }, { status: 502 });
  }
}
