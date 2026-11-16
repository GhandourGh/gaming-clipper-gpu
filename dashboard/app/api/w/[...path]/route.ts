import { passResponse, workerFetch } from "@/lib/worker";

// Server-side pass-through to the worker API. Only these paths/methods are reachable from the browser;
// the worker token is added here and never sent to the page.
const ID = "[0-9a-f-]{36}";
const ALLOW: Array<[string, RegExp]> = [
  ["GET", /^dashboard\/(overview|clips|costs|settings|posts|accounts|jobs)$/],
  ["GET", new RegExp(`^dashboard\\/jobs\\/${ID}$`)],
  ["PUT", /^dashboard\/settings$/],
  ["DELETE", new RegExp(`^dashboard\\/jobs\\/${ID}$`)],
  ["POST", /^dashboard\/posts$/],
  ["PUT", new RegExp(`^dashboard\\/posts\\/${ID}$`)],
  ["DELETE", new RegExp(`^dashboard\\/posts\\/${ID}$`)],
  ["POST", new RegExp(`^dashboard\\/posts\\/${ID}\\/approve$`)],
  ["POST", new RegExp(`^jobs\\/${ID}\\/clips\\/\\d{1,2}\\/(review|regenerate)$`)],
  ["GET", new RegExp(`^jobs\\/${ID}\\/clips\\/\\d{1,2}\\/text$`)],
  ["POST", new RegExp(`^jobs\\/${ID}\\/clips\\/\\d{1,2}\\/text$`)],
  ["POST", new RegExp(`^jobs\\/${ID}\\/(cancel|retry)$`)],
  ["GET", /^dashboard\/gpu$/],
  ["POST", /^admin\/(stop-pod|update|env)$/],
  ["GET", /^admin\/logs$/],
  ["GET", /^dashboard\/live$/],
  ["POST", /^dashboard\/live(\/check)?$/],
  ["PUT", /^dashboard\/live\/[a-z0-9-]{1,40}$/],
  ["DELETE", /^dashboard\/live\/[a-z0-9-]{1,40}$/],
  ["GET", new RegExp(`^tools\\/guide\\/${ID}$`)],
  ["POST", new RegExp(`^tools\\/guide\\/${ID}\\/clips\\/\\d{1,2}\\/export$`)],
];

async function handle(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const joined = path.map(decodeURIComponent).join("/");
  if (!ALLOW.some(([method, re]) => method === request.method && re.test(joined))) {
    return Response.json({ error: "Not allowed" }, { status: 404 });
  }
  const search = new URL(request.url).search;
  const init: RequestInit = { method: request.method };
  if (request.method === "POST" || request.method === "PUT") {
    init.body = await request.text();
    init.headers = { "content-type": "application/json" };
  }
  try {
    return passResponse(await workerFetch(`/${joined}${search}`, init));
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Worker unavailable" }, { status: 503 });
  }
}

export { handle as GET, handle as POST, handle as PUT, handle as DELETE };
