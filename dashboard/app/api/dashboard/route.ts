import { passResponse, workerFetch } from "@/lib/worker";

export async function GET() {
  try {
    return passResponse(await workerFetch("/dashboard/jobs"));
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Dashboard unavailable" }, { status: 503 });
  }
}
