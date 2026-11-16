import { passResponse, workerFetch } from "@/lib/worker";

// Owner-triggered publishing step, sent straight to the worker, which re-checks every rule
// (approved first; publishing enabled in Settings).
export async function POST(request: Request) {
  const { post_id, action } = await request.json().catch(() => ({}));
  if (!/^[0-9a-f-]{36}$/.test(String(post_id)) || !["send", "publish"].includes(action)) {
    return Response.json({ error: "invalid request" }, { status: 400 });
  }
  try {
    return passResponse(await workerFetch(`/dashboard/posts/${post_id}/action`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ action }) }));
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Worker unavailable" }, { status: 503 });
  }
}
