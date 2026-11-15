import { validSession } from "@/lib/session";

// Streams a video (raw request body) to the worker's /upload as multipart, without holding it in memory.
// Not behind proxy.ts (its 10 MB body buffer would cut videos off), so it checks the session itself.
const worker = process.env.CLIPPER_WORKER_URL || "http://127.0.0.1:8787";
const VIDEO = /\.(mp4|mov|mkv|webm|m4v|avi)$/i;

export async function POST(request: Request) {
  if (!(await validSession(request))) return Response.json({ error: "Dashboard session required" }, { status: 401 });
  const name = decodeURIComponent(request.headers.get("x-filename") || "").split(/[\\/]/).pop()?.replace(/[^\w.\- ()]/g, "_") || "";
  if (!VIDEO.test(name)) return Response.json({ error: "Choose a video file (MP4, MOV, MKV, WebM)." }, { status: 400 });
  if (!request.body) return Response.json({ error: "The upload was empty." }, { status: 400 });
  const token = process.env.CLIPPER_TOKEN || "";
  const boundary = `----clipper${crypto.randomUUID().replaceAll("-", "")}`;
  const enc = new TextEncoder();
  const head = enc.encode(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${name.replaceAll('"', "")}"\r\nContent-Type: application/octet-stream\r\n\r\n`);
  const tail = enc.encode(`\r\n--${boundary}--\r\n`);
  const reader = request.body.getReader();
  const body = new ReadableStream<Uint8Array>({
    start(c) { c.enqueue(head); },
    async pull(c) {
      const { done, value } = await reader.read();
      if (done) { c.enqueue(tail); c.close(); } else c.enqueue(value);
    },
    cancel(reason) { reader.cancel(reason); },
  });
  try {
    const response = await fetch(`${worker}/upload`, {
      method: "POST", body, headers: { "content-type": `multipart/form-data; boundary=${boundary}`, "X-Clipper-Token": token },
      // @ts-expect-error -- Node fetch needs duplex for streamed request bodies
      duplex: "half",
    });
    const text = await response.text();
    return new Response(text, { status: response.status, headers: { "content-type": response.headers.get("content-type") || "application/json" } });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Upload failed" }, { status: 502 });
  }
}
