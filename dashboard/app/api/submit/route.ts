import { passResponse, workerFetch } from "@/lib/worker";

export async function POST(request: Request) {
  try {
    const form = await request.formData();
    const file = form.get("file");
    let filename = String(form.get("inbox") || "");
    if (file instanceof File && file.size) {
      const upload = new FormData();
      upload.set("file", file);
      const result = await workerFetch("/upload", { method: "POST", body: upload });
      if (!result.ok) return passResponse(result);
      filename = (await result.json()).filename;
    }
    const url = String(form.get("url") || "").trim();
    const payload = {
      source_type: url ? (/^https?:\/\/(?:www\.|m\.|go\.)?twitch\.tv\//i.test(url) ? "twitch" : "youtube") : "file",
      ...(url ? { url } : { filename }),
      title: String(form.get("title") || "") || null,
      style: "viral",
      clips: Number(form.get("clips")) || null,
      min_seconds: Number(form.get("min_seconds")) || null,
      max_seconds: Number(form.get("max_seconds")) || null,
      rights_confirmed: form.get("rights") === "yes",
      fresh: form.get("fresh") === "1",
      captions: ["overlay", "bar", "off"].includes(String(form.get("captions"))) ? String(form.get("captions")) : null,
      engine: ["stories", "moments"].includes(String(form.get("engine"))) ? String(form.get("engine")) : "stories",
      template: ["auto", "full", "split_50", "split_20", "gta6_gameplay", "gta_cam", "gta_hook", "gta_mix"].includes(String(form.get("template"))) ? String(form.get("template")) : "auto",
    };
    if (!payload.rights_confirmed) return Response.json({ error: "Confirm that you have the rights to edit this video." }, { status: 400 });
    if (!url && !filename) return Response.json({ error: "Add a YouTube or Twitch VOD URL, upload a file, or choose an inbox file." }, { status: 400 });
    const g = (k: string) => String(form.get(k) || "");
    const options = {  // mode + editing options: the worker attaches them to the job created right after
      mode: g("mode"), content_type: g("content_type"), edit: g("edit"), silence: g("silence"), zoom: g("zoom"), sfx: g("sfx"),
      caption_look: g("caption_look"), instructions: g("instructions") || null,
      clips: payload.clips, min_seconds: payload.min_seconds, max_seconds: payload.max_seconds,
      captions: payload.captions, template: payload.template,
      clip_strategy: g("clip_strategy") || "single",
      facecam_size: g("facecam_size") || null, facecam_position: g("facecam_position") || null,
      game_logo: g("game_logo") || null, gameplay_crop: g("gameplay_crop") || null,
      header_text: form.has("header_text") ? g("header_text") : null, hook_text: form.has("hook_text") ? g("hook_text") : null, frame: g("frame") || null,
      mix_layouts: String(form.get("template")) === "gta_mix" ? form.getAll("mix_layouts").map(String) : null,
    };
    const pending = await workerFetch("/jobs/pending-options", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ source: url || filename, options }) });
    if (!pending.ok) return Response.json({ error: "Could not save the editing options for this job." }, { status: 502 });
    // The worker owns the whole pipeline: creating the job starts ingest -> analysis -> plan -> render -> QC -> review.
    return passResponse(await workerFetch("/jobs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) }));
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Could not create job" }, { status: 500 });
  }
}
