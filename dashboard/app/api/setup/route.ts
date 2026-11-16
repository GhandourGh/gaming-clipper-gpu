import { spawn } from "node:child_process";
import { existsSync, readFileSync, writeFileSync, chmodSync, unlinkSync } from "node:fs";

// First-run setup of a brand-new Pod, usable exactly once: the caller proves it can read the Pod's Runpod console log
// (where pod-run.sh prints a one-time code), then stores the worker token / dashboard password / API keys in the Pod's
// env file. Afterwards the services restart with the new settings and this route answers 410.
const STATE = process.env.CLIPPER_STATE || "/workspace/clipper-runtime";
const ENV_FILE = process.env.CLIPPER_ENV_FILE || "/workspace/clipper.env";
const ROOT = process.env.CLIPPER_ROOT || "/workspace/gaming-clipper-host";
const KEYS = new Set(["CLIPPER_TOKEN", "DASHBOARD_PASSWORD", "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
  "OPENROUTER_API_KEY", "GROQ_API_KEY", "GPU_USD_PER_HOUR", "IDLE_STOP_MIN"]);
let failures = 0;

function same(a: string, b: string) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

export async function POST(request: Request) {
  const codeFile = `${STATE}/setup-code`;
  if (existsSync(`${STATE}/setup-done`) || !existsSync(codeFile)) return Response.json({ error: "Setup was already done." }, { status: 410 });
  const { code, values } = await request.json().catch(() => ({}));
  if (!same(String(code || ""), readFileSync(codeFile, "utf8").trim())) {
    if (++failures >= 5) unlinkSync(codeFile);  // brute force: burn the code; a Pod restart prints a new one
    return Response.json({ error: "Wrong setup code." }, { status: 401 });
  }
  const entries = Object.entries((values || {}) as Record<string, unknown>).map(([k, v]) => [k, String(v ?? "").trim()] as const);
  const bad = entries.filter(([k, v]) => !KEYS.has(k) || !v || /[\r\n]/.test(v)).map(([k]) => k);
  if (bad.length || !entries.length) return Response.json({ error: `Not allowed or empty: ${bad.join(", ") || "no values"}` }, { status: 400 });
  const lines = existsSync(ENV_FILE) ? readFileSync(ENV_FILE, "utf8").split("\n").filter(Boolean) : [];
  const names = new Set(entries.map(([k]) => k));
  const keep = lines.filter(l => !names.has(l.split("=", 1)[0].trim()));
  writeFileSync(ENV_FILE, [...keep, ...entries.map(([k, v]) => `${k}=${v}`)].join("\n") + "\n");
  chmodSync(ENV_FILE, 0o600);
  writeFileSync(`${STATE}/setup-done`, new Date().toISOString());
  unlinkSync(codeFile);
  // restart worker + dashboard with the new env (detached: this process is one of the services being restarted)
  spawn("bash", ["-c", `sleep 1; bash ${ROOT}/scripts/pod-run.sh >> ${STATE}/update.log 2>&1`], { detached: true, stdio: "ignore" }).unref();
  return Response.json({ stored: [...names].sort(), restarting: true });
}
