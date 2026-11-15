// Dashboard session check shared by proxy.ts and routes that bypass the proxy (large uploads).
export const cookieName = "clipper_dashboard_session";
const context = "gaming-clipper-dashboard-v1";

export async function sessionSignature() {
  const secret = process.env.CLIPPER_TOKEN || "";
  if (!secret) return "";
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const bytes = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(context)));
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function cookie(request: Request, name: string) {
  const raw = request.headers.get("cookie") || "";
  return raw.split(";").map(p => p.trim()).find(p => p.startsWith(`${name}=`))?.slice(name.length + 1) || "";
}

/** True when the request carries the dashboard session cookie and comes from the dashboard's own origin. */
export async function validSession(request: Request) {
  const expected = await sessionSignature();
  if (!expected || cookie(request, cookieName) !== expected) return false;
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  return origin !== null && host !== null && new URL(origin).host === host;  // browsers send Origin on every POST
}
