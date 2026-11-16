// Upload a big video in small pieces. Runpod's web address cuts any single request after ~100 s / ~100 MB, so a
// 1.5 GB file is sent as 16 MB pieces, each its own short request. A piece that fails is retried; a piece that already
// reached the Pod is skipped, so after a dropped connection (or a reload) the same file resumes instead of restarting.
// Pure logic: the actual sending is injected (browserSender below uses XMLHttpRequest for per-piece progress).

export const CHUNK_BYTES = 16 * 1024 * 1024;
const ATTEMPTS = 5;

export type Req = { action: "chunk" | "status" | "finish"; id: string; total: number; name: string; index?: number; body?: Blob };
export type Res = { status: number; json: { have?: number[]; filename?: string; error?: string; detail?: string } };
export type Sender = (req: Req, onBytes?: (n: number) => void) => Promise<Res>;
export type Progress = { loaded: number; total: number; phase: "uploading" | "saving" };
type FileLike = Blob & { name: string; lastModified: number };

/** Same file + same piece size => same id, which is what lets a retry find the pieces already on the Pod. */
export function uploadId(f: { name: string; size: number; lastModified: number }, chunk = CHUNK_BYTES): string {
  let h = 2166136261;
  for (const c of `${f.name}|${f.size}|${f.lastModified}`) h = Math.imul(h ^ c.codePointAt(0)!, 16777619) >>> 0;
  return `u${h.toString(16)}-${f.size}-${chunk}`;
}

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
const fail = (res: Res) => new Error(res.json.error || res.json.detail ||
  (res.status === 401 ? "Your dashboard session expired: reload the page and sign in again." : `Upload failed (${res.status})`));

async function withRetry(send: () => Promise<Res>, wait: (ms: number) => Promise<unknown>): Promise<Res> {
  let last: unknown = new Error("Upload failed");
  for (let a = 0; a < ATTEMPTS; a++) {
    try {
      const res = await send();
      if (res.status >= 200 && res.status < 300) return res;
      if (res.status === 401 || (res.status >= 400 && res.status < 500 && res.status !== 408 && res.status !== 409 && res.status !== 429)) throw Object.assign(fail(res), { fatal: true });
      last = fail(res);
      if (res.status === 409) return res;  // the caller decides (missing pieces)
    } catch (e) {
      if ((e as { fatal?: boolean }).fatal) throw e;
      last = e;
    }
    await wait(Math.min(10_000, 1000 * 1.7 ** a));  // the connection blipped: back off, then try this piece again
  }
  throw last instanceof Error ? last : new Error("Upload failed: the connection to the dashboard was lost. Check that it is still running, then try again.");
}

export async function uploadChunked(file: FileLike, send: Sender, onProgress: (p: Progress) => void,
                                    opts: { chunk?: number; wait?: (ms: number) => Promise<unknown> } = {}): Promise<string> {
  const chunk = opts.chunk ?? CHUNK_BYTES, wait = opts.wait ?? sleep;
  const total = Math.max(1, Math.ceil(file.size / chunk));
  const id = uploadId(file, chunk), name = file.name;
  const size = (i: number) => Math.min(chunk, file.size - i * chunk);

  for (let round = 0; round < 3; round++) {
    let have = new Set<number>();
    try { have = new Set((await send({ action: "status", id, total, name })).json.have ?? []); } catch { /* no status: send everything */ }
    let done = [...have].reduce((n, i) => n + (i < total ? size(i) : 0), 0);
    onProgress({ loaded: done, total: file.size, phase: "uploading" });
    for (let i = 0; i < total; i++) {
      if (have.has(i)) continue;
      const body = file.slice(i * chunk, i * chunk + size(i));
      let seen = 0;
      await withRetry(() => { seen = 0; return send({ action: "chunk", id, total, name, index: i, body }, n => { seen = n; onProgress({ loaded: done + n, total: file.size, phase: "uploading" }); }); }, wait);
      done += size(i);
      onProgress({ loaded: done, total: file.size, phase: "uploading" });
    }
    onProgress({ loaded: file.size, total: file.size, phase: "saving" });  // every piece is there; the Pod is stitching them together
    const res = await withRetry(() => send({ action: "finish", id, total, name }), wait);
    if (res.status >= 200 && res.status < 300 && res.json.filename) return res.json.filename;
    // 409: some piece went missing on the Pod (it was cleaned up or restarted): the next round re-checks and re-sends them
  }
  throw new Error("Upload failed: the Pod lost parts of the file. Try again.");
}

/** Browser sender: one XMLHttpRequest per piece (fetch cannot report upload progress). */
export const browserSender: Sender = (req, onBytes) => new Promise((resolve, reject) => {
  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload-chunk");
  xhr.setRequestHeader("x-action", req.action);
  xhr.setRequestHeader("x-upload-id", req.id);
  xhr.setRequestHeader("x-chunk-total", String(req.total));
  xhr.setRequestHeader("x-filename", encodeURIComponent(req.name));
  if (req.index !== undefined) xhr.setRequestHeader("x-chunk-index", String(req.index));
  if (onBytes) xhr.upload.onprogress = e => { if (e.lengthComputable) onBytes(e.loaded); };
  xhr.onload = () => { let json = {}; try { json = JSON.parse(xhr.responseText); } catch { /* not JSON */ } resolve({ status: xhr.status, json }); };
  xhr.onerror = () => reject(new Error("Upload failed: the connection to the dashboard was lost. Check that it is still running, then try again."));
  xhr.ontimeout = () => reject(new Error("Upload timed out."));
  xhr.send(req.body ?? null);
});
