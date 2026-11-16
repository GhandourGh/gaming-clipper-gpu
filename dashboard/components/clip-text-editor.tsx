"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Texts = { editable: boolean; template: string | null; hook_text: string; header_text: string; show_header: boolean; caption_size: string; caption_shift: number };

/** Change the text on a finished clip (hook card, header, caption size and position) and re-render it in about 30 seconds:
 *  the plan is not redone, so only what you type changes. Only for the GTA VI layouts. */
export function ClipTextEditor({ jobId, idx, onChange }: { jobId: string; idx: number; onChange?: () => void }) {
  const [t, setT] = useState<Texts | null>(null);
  const [form, setForm] = useState<Texts | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let live = true;
    api<Texts>(`jobs/${jobId}/clips/${idx}/text`).then(r => { if (live) { setT(r); setForm(r); } }).catch(() => { if (live) setT(null); });
    return () => { live = false; };
  }, [jobId, idx]);

  if (!t || !t.editable || !form) return null;
  const set = <K extends keyof Texts>(k: K, v: Texts[K]) => setForm({ ...form, [k]: v });
  const apply = async () => {
    const body: Record<string, string | number | boolean> = {};
    if (form.hook_text !== t.hook_text) body.hook_text = form.hook_text;
    if (form.header_text !== t.header_text) body.header_text = form.header_text;
    if (form.show_header !== t.show_header) body.show_header = form.show_header;
    if (form.caption_size !== t.caption_size) body.caption_size = form.caption_size;
    if (form.caption_shift !== t.caption_shift) body.caption_shift = form.caption_shift;
    if (!Object.keys(body).length) { setNote("Nothing changed."); return; }
    setBusy(true); setNote("");
    try {
      await api(`jobs/${jobId}/clips/${idx}/text`, { method: "POST", body: JSON.stringify(body) });
      setT(form); setNote("Re-rendering this clip with your text (about 30 s). The page updates on its own; the previous cut is kept as a version.");
      onChange?.();
    } catch (e) { setNote(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  };

  return <div className="field" style={{ marginTop: ".25rem" }}>
    <span>Edit the text on this clip</span>
    {!open ? <button className="btn" onClick={() => setOpen(true)}>Edit hook, header and captions</button> : <div style={{ display: "grid", gap: ".6rem" }}>
      <label className="field"><span>Hook card text</span>
        <input value={form.hook_text} maxLength={80} placeholder="Empty: no card" onChange={e => set("hook_text", e.target.value)}/>
        <small>Shown in the white card above the gameplay (widescreen layout). One emoji at the end works.</small></label>
      <label className="field" style={{ display: "flex", gap: ".5rem", alignItems: "center" }}><input type="checkbox" checked={form.show_header} onChange={e => set("show_header", e.target.checked)}/><span>Show the header at the top (name + GTA VI logo)</span></label>
      <label className="field"><span>Header text</span>
        <input value={form.header_text} maxLength={40} placeholder="Empty: only the logo" onChange={e => set("header_text", e.target.value)}/></label>
      <label className="field"><span>Caption size</span>
        <select value={form.caption_size} onChange={e => set("caption_size", e.target.value)}>
          <option value="small">Small</option><option value="medium">Medium</option><option value="large">Large</option></select></label>
      <label className="field"><span>Caption position: {form.caption_shift > 0 ? `${form.caption_shift} px lower` : form.caption_shift < 0 ? `${-form.caption_shift} px higher` : "default"}</span>
        <input type="range" min={-200} max={200} step={10} value={form.caption_shift} onChange={e => set("caption_shift", Number(e.target.value))}/></label>
      <div style={{ display: "flex", gap: ".5rem" }}>
        <button className="btn btn-primary" disabled={busy} onClick={apply}>{busy ? "Applying…" : "Apply and re-render"}</button>
        <button className="btn btn-ghost" disabled={busy} onClick={() => { setForm(t); setOpen(false); setNote(""); }}>Cancel</button>
      </div>
    </div>}
    {note && <p className="notice info" style={{ margin: 0 }}>{note}</p>}
  </div>;
}
