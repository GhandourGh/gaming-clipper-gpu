// Streams clip videos, thumbnails and zip downloads from the worker (supports Range for video seeking).
const worker = process.env.CLIPPER_WORKER_URL || "http://127.0.0.1:8787";
const ID = "[0-9a-f-]{36}";
const ALLOW = [new RegExp(`^(files|thumbs)\\/${ID}\\/[\\w.-]{1,80}$`), new RegExp(`^jobs\\/${ID}\\/(zip|source)$`)];

export async function GET(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const joined = path.map(decodeURIComponent).join("/");
  if (!ALLOW.some(re => re.test(joined)) || joined.includes("..")) return new Response("Not found", { status: 404 });
  const headers = new Headers();
  const range = request.headers.get("range");
  if (range) headers.set("range", range);
  const upstream = await fetch(`${worker}/${joined}`, { headers, cache: "no-store" });
  const out = new Headers();
  for (const name of ["content-type", "content-length", "content-range", "accept-ranges", "content-disposition", "last-modified", "etag"]) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  out.set("cache-control", "private, max-age=60");
  return new Response(upstream.body, { status: upstream.status, headers: out });
}
