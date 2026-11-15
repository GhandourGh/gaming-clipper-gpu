import { NextRequest, NextResponse } from "next/server";
import { cookieName, sessionSignature } from "@/lib/session";

export async function proxy(request: NextRequest) {
  const expected = await sessionSignature();
  if (!expected) return new NextResponse("Dashboard is not configured", { status: 503 });
  const supplied = request.cookies.get(cookieName)?.value || "";
  if (request.nextUrl.pathname.startsWith("/api/") && supplied !== expected) {
    return Response.json({ error: "Dashboard session required" }, { status: 401 });
  }
  const response = NextResponse.next();
  if (supplied !== expected) response.cookies.set(cookieName, expected, { httpOnly: true, sameSite: "strict", secure: false, path: "/" });
  return response;
}

// /api/upload is excluded: the proxy buffers at most 10 MB of a request body (videos are bigger). That route
// streams the file to the worker and does the same session + origin check itself (lib/session.ts).
export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico|api/upload).*)"] };
