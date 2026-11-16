// Dashboard sign-in. The session cookie is an HMAC of the worker token, so it can only be minted by someone who knows
// the access key (DASHBOARD_PASSWORD, or the worker token when no separate password is set). Shared by proxy.ts and the
// routes that bypass the proxy (large uploads).
export const cookieName = "clipper_dashboard_session";
const context = "gaming-clipper-dashboard-v2";

export async function sessionSignature() {
  const secret = process.env.CLIPPER_TOKEN || "";
  if (!secret) return "";
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const bytes = new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(context)));
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

/** Constant-time check of the access key typed on the sign-in page. */
export async function accessKeyOk(supplied: string) {
  const expected = process.env.DASHBOARD_PASSWORD || process.env.CLIPPER_TOKEN || "";
  if (!expected || !supplied) return false;
  const h = async (s: string) => new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)));
  const [a, b] = await Promise.all([h(supplied), h(expected)]);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
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
  const host = request.headers.get("x-forwarded-host") || request.headers.get("host");
  return origin !== null && host !== null && new URL(origin).host === host;  // browsers send Origin on every POST
}
