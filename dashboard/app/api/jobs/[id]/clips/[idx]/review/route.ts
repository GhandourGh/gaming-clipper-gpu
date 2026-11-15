import { passResponse, workerFetch } from "@/lib/worker";

export async function POST(request: Request, context: { params: Promise<{ id: string; idx: string }> }) {
  const { id, idx } = await context.params;
  const body = await request.text();
  return passResponse(await workerFetch(`/jobs/${encodeURIComponent(id)}/clips/${encodeURIComponent(idx)}/review`, {
    method: "POST", headers: { "content-type": "application/json" }, body,
  }));
}
