// Set CLIPPER_WORKER_URL to the GPU worker when the dashboard is used as a
// remote-only client. The Mac remains responsible for the UI and never runs
// the processing pipeline in that mode.
const workerUrl = process.env.CLIPPER_WORKER_URL || "http://127.0.0.1:8787";
const token = process.env.CLIPPER_TOKEN || "";

export async function workerFetch(path: string, init: RequestInit = {}) {
  if (!token) throw new Error("CLIPPER_TOKEN is not configured for the dashboard");
  const headers = new Headers(init.headers);
  headers.set("X-Clipper-Token", token);
  return fetch(`${workerUrl}${path}`, { ...init, headers, cache: "no-store" });
}

export async function passResponse(response: Response) {
  const body = await response.text();
  return new Response(body, {
    status: response.status,
    headers: { "content-type": response.headers.get("content-type") || "application/json" },
  });
}
