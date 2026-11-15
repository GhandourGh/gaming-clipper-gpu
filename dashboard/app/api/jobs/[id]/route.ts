import { passResponse, workerFetch } from "@/lib/worker";

export async function GET(_: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  try {
    return passResponse(await workerFetch(`/dashboard/jobs/${encodeURIComponent(id)}`));
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Job unavailable" }, { status: 503 });
  }
}
