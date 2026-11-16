import { cookieName } from "@/lib/session";

export async function POST() {
  return new Response(JSON.stringify({ ok: true }), { headers: { "content-type": "application/json", "set-cookie": `${cookieName}=; Path=/; Max-Age=0` } });
}
