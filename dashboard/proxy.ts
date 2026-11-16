import { NextRequest, NextResponse } from "next/server";
import { cookieName, sessionSignature } from "@/lib/session";

// Every page and API route needs the session cookie set by /login. Pages redirect to /login, APIs answer 401.
export async function proxy(request: NextRequest) {
  const expected = await sessionSignature();
  if (!expected) return new NextResponse("Dashboard is not configured (CLIPPER_TOKEN missing)", { status: 503 });
  const path = request.nextUrl.pathname;
  if (path === "/login" || path === "/api/login" || path === "/api/setup") return NextResponse.next();
  if ((request.cookies.get(cookieName)?.value || "") === expected) return NextResponse.next();
  if (path.startsWith("/api/")) return NextResponse.json({ error: "Sign in to the dashboard first." }, { status: 401 });
  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = path === "/" ? "" : `?next=${encodeURIComponent(path + request.nextUrl.search)}`;
  return NextResponse.redirect(url);
}

// /api/upload is excluded: the proxy buffers at most 10 MB of a request body (videos are bigger). That route
// streams the file to the worker and does the same session + origin check itself (lib/session.ts).
export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico|api/upload).*)"] };
