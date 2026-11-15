import { passResponse, workerFetch } from "@/lib/worker";

export async function POST(request: Request, context: { params: Promise<{ id: string; idx: string }> }) {
  const { id, idx } = await context.params;
  const { style } = await request.json();
  return passResponse(await workerFetch(`/jobs/${encodeURIComponent(id)}/clips/${encodeURIComponent(idx)}/regenerate?style=${encodeURIComponent(style)}`, { method: "POST" }));
}
