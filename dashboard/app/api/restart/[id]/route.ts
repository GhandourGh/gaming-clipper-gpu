import { passResponse, workerFetch } from "@/lib/worker";

// "Start over": the worker wipes the job's results and queues the whole pipeline again (it drives every stage itself).
export async function POST(_: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!/^[0-9a-f-]{36}$/.test(id)) return Response.json({ error: "Bad job id" }, { status: 400 });
  try {
    return passResponse(await workerFetch(`/dashboard/jobs/${id}/restart`, { method: "POST" }));
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Could not restart the job" }, { status: 503 });
  }
}
