// Owner-triggered publishing step: forwards {post_id, action} to the n8n "Publish" workflow (which also sends a
// Telegram confirmation). The worker re-checks every rule (approved first; publishing enabled in Settings).
export async function POST(request: Request) {
  const { post_id, action } = await request.json().catch(() => ({}));
  if (!/^[0-9a-f-]{36}$/.test(String(post_id)) || !["send", "publish"].includes(action)) {
    return Response.json({ error: "invalid request" }, { status: 400 });
  }
  const n8n = process.env.CLIPPER_N8N_URL || "http://127.0.0.1:5678/webhook";
  try {
    const r = await fetch(`${n8n}/gaming-clipper-publish`, {
      method: "POST", headers: { "content-type": "application/json", "X-Clipper-Token": process.env.CLIPPER_TOKEN || "" },
      body: JSON.stringify({ post_id, action }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok || body.detail) return Response.json({ error: body.detail || body.message || `Publish workflow returned ${r.status}` }, { status: r.ok ? 409 : r.status });
    return Response.json(body);
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "n8n not reachable" }, { status: 503 });
  }
}
