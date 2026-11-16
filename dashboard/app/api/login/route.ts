import { accessKeyOk, cookieName, sessionSignature } from "@/lib/session";

const tries = new Map<string, { n: number; t: number }>();  // simple brute-force brake per client address

export async function POST(request: Request) {
  const ip = (request.headers.get("x-forwarded-for") || "local").split(",")[0].trim();
  const now = Date.now();
  const t = tries.get(ip);
  if (t && t.n >= 8 && now - t.t < 10 * 60_000) return Response.json({ error: "Too many attempts. Wait 10 minutes." }, { status: 429 });
  const { key } = await request.json().catch(() => ({ key: "" }));
  if (!(await accessKeyOk(String(key || "")))) {
    tries.set(ip, { n: (t && now - t.t < 10 * 60_000 ? t.n : 0) + 1, t: now });
    return Response.json({ error: "Wrong access key." }, { status: 401 });
  }
  tries.delete(ip);
  const secure = (request.headers.get("x-forwarded-proto") || new URL(request.url).protocol.replace(":", "")) === "https";
  const value = await sessionSignature();
  return new Response(JSON.stringify({ ok: true }), { headers: { "content-type": "application/json",
    "set-cookie": `${cookieName}=${value}; Path=/; HttpOnly; SameSite=Lax; Max-Age=${60 * 60 * 24 * 30}${secure ? "; Secure" : ""}` } });
}
