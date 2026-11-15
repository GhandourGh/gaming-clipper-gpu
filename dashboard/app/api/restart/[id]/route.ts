import { passResponse, workerFetch } from "@/lib/worker";

// "Start over": the worker wipes the job's results, then the same n8n flow that normal jobs use is started again
// (n8n drives every stage after analysis, so a worker-only restart would stall).
export async function POST(_: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!/^[0-9a-f-]{36}$/.test(id)) return Response.json({ error: "Bad job id" }, { status: 400 });
  try {
    const reset = await workerFetch(`/dashboard/jobs/${id}/restart`, { method: "POST" });
    if (!reset.ok) return passResponse(reset);
    const job = await reset.json();
    const payload = {
      source_type: ["youtube", "twitch"].includes(job.source_type) ? job.source_type : "file", url: job.url || undefined, filename: job.filename || undefined,
      title: job.title, style: job.style, clips: job.clips, min_seconds: job.min_seconds, max_seconds: job.max_seconds, rights_confirmed: true,
    };
    const token = process.env.CLIPPER_TOKEN || "";
    const n8n = process.env.CLIPPER_N8N_URL || "http://127.0.0.1:5678/webhook";
    const started = await fetch(`${n8n}/gaming-clipper`, { method: "POST", headers: { "content-type": "application/json", "X-Clipper-Token": token }, body: JSON.stringify(payload) });
    if (!started.ok) return Response.json({ error: "The job was reset but the pipeline could not be started. Check that n8n is running." }, { status: 502 });
    return Response.json({ job_id: id, status: "created" });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Could not restart the job" }, { status: 503 });
  }
}
