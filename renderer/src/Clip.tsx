import React from "react";
import { AbsoluteFill, Audio, Easing, Img, OffthreadVideo, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { loadFont as montserrat } from "@remotion/google-fonts/Montserrat";
import { loadFont as luckiestGuy } from "@remotion/google-fonts/LuckiestGuy";
import { loadFont as bebasNeue } from "@remotion/google-fonts/BebasNeue";
import { loadFont as anton } from "@remotion/google-fonts/Anton";
import { loadFont as inter } from "@remotion/google-fonts/Inter";
import type { Box, ClipProps, Word } from "./types";

// One display font per editing style (all Google Fonts, SIL Open Font License)
const FONT = {
  montserrat: montserrat("normal", { weights: ["800", "900"], subsets: ["latin"] }).fontFamily,
  luckiest: luckiestGuy("normal", { weights: ["400"], subsets: ["latin"] }).fontFamily,
  bebas: bebasNeue("normal", { weights: ["400"], subsets: ["latin"] }).fontFamily,
  anton: anton("normal", { weights: ["400"], subsets: ["latin"] }).fontFamily,
  inter: inter("normal", { weights: ["800"], subsets: ["latin"] }).fontFamily,
};
const W = 1080, H = 1920;
const SAFE = { side: 80, top: 230, bottom: 420 }; // keep text clear of platform UI
type Theme = { size: number; accent: string; hot: string; stroke: number; upper: boolean; hookBg: string; hookFg: string; lowerY: number; font: string; weight: number };
const THEMES: Record<string, Theme> = {
  viral:     { size: 92, accent: "#7CFF4F", hot: "#FF3B3B", stroke: 14, upper: true,  hookBg: "#fff",    hookFg: "#111", lowerY: 0.7,  font: FONT.montserrat, weight: 900 },
  funny:     { size: 92, accent: "#7CFF4F", hot: "#FF3B3B", stroke: 14, upper: true,  hookBg: "#FFE600", hookFg: "#111", lowerY: 0.7,  font: FONT.luckiest, weight: 400 },
  fast:      { size: 112, accent: "#7CFF4F", hot: "#FF3B3B", stroke: 14, upper: true,  hookBg: "#111",    hookFg: "#fff", lowerY: 0.7,  font: FONT.anton, weight: 400 },
  cinematic: { size: 84, accent: "#7CFF4F", hot: "#FF3B3B", stroke: 0,  upper: true,  hookBg: "rgba(0,0,0,0.55)", hookFg: "#fff", lowerY: 0.73, font: FONT.bebas, weight: 400 },
  minimal:   { size: 70, accent: "#7CFF4F", hot: "#FF3B3B", stroke: 10, upper: false, hookBg: "#fff",    hookFg: "#111", lowerY: 0.72, font: FONT.inter, weight: 800 },
};
const theme = (p: ClipProps) => THEMES[p.style ?? "viral"] ?? THEMES.viral;
const db = (g: number) => Math.pow(10, g / 20);

/** Zoom factor for a target at time t (seconds). Punch = snap in, hold, release. */
const zoomAt = (zooms: ClipProps["zooms"], targets: string[], t: number, zoomMul = 1) => {
  for (const z of zooms) {
    if (!targets.includes(z.target) || t < z.start || t > z.end) continue;
    const d = z.end - z.start;
    const inD = z.style === "punch" ? Math.min(0.14, d / 3) : d * 0.7;
    const outD = z.style === "punch" ? Math.min(0.25, d / 3) : Math.min(0.4, d * 0.3);
    const k = interpolate(t, [z.start, z.start + inD, z.end - outD, z.end], [0, 1, 1, 0], {
      extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.inOut(Easing.cubic) });
    return { s: 1 + (z.scale - 1) * k * zoomMul, fx: z.fx, fy: z.fy };
  }
  return { s: 1, fx: 0.5, fy: 0.5 };
};

/** A window onto the source video covering a w×h region.
 *  box  = normalised source rect to fill the window with (e.g. the facecam);
 *  null = "cover" the window with the whole frame, centred on focusX. */
const Region: React.FC<{ p: ClipProps; w: number; h: number; box: Box | null; focusX?: number; muted: boolean; zoom: { s: number; fx: number; fy: number }; style?: React.CSSProperties }> =
  ({ p, w, h, box: b, focusX = 0.5, muted, zoom, style }) => {
    // area = the real game picture (letterbox bars inside the source removed); only for whole-frame regions
    const a: Box = (!b && p.layout.gameplayArea) || { x: 0, y: 0, w: 1, h: 1 };
    const box: Box = b ?? { x: a.x + focusX * a.w, y: a.y + a.h / 2, w: 0, h: 0 };
    const k = b ? Math.max(w / (b.w * p.srcW), h / (b.h * p.srcH)) : Math.max(w / (a.w * p.srcW), h / (a.h * p.srcH));
    const vw = p.srcW * k, vh = p.srcH * k;
    const left = Math.min(-a.x * vw, Math.max(w - (a.x + a.w) * vw, w / 2 - (box.x + box.w / 2) * vw));
    const top = Math.min(-a.y * vh, Math.max(h - (a.y + a.h) * vh, h / 2 - (box.y + box.h / 2) * vh));
    return (
      <div style={{ position: "absolute", width: w, height: h, overflow: "hidden", ...style }}>
        <div style={{ width: w, height: h, transform: `scale(${zoom.s})`, transformOrigin: `${zoom.fx * 100}% ${zoom.fy * 100}%` }}>
          <OffthreadVideo src={staticFile(p.src)} muted={muted} style={{ position: "absolute", left, top, width: vw, height: vh, maxWidth: "none" }} />
        </div>
      </div>
    );
  };

const groupWords = (words: Word[]) => {
  const groups: Word[][] = [];
  let cur: Word[] = [];
  for (const w of words) {
    const chars = cur.reduce((n, x) => n + x.w.length + 1, 0);
    const prev = cur[cur.length - 1];
    if (cur.length && (cur.length >= 3 || chars + w.w.length > 16 || w.s - prev.e > 0.45 || /[.!?]$/.test(prev.w))) {
      groups.push(cur);
      cur = [];
    }
    cur.push(w);
  }
  if (cur.length) groups.push(cur);
  return groups;
};

const Captions: React.FC<{ p: ClipProps; y: number }> = ({ p, y }) => {
  const th = theme(p);
  const ACCENT = th.accent, HOT = th.hot;
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const groups = React.useMemo(() => groupWords(p.captions.words), [p.captions.words]);
  const emph = React.useMemo(() => new Set(p.captions.emphasis.map((e) => e.toLowerCase().replace(/[^\p{L}\p{N}']/gu, ""))), [p.captions.emphasis]);
  const gi = groups.findIndex((g, i) => t >= g[0].s - 0.05 && t < Math.min(g[g.length - 1].e + 0.35, groups[i + 1]?.[0].s ?? Infinity));
  if (gi < 0) return null;
  const g = groups[gi];
  const pop = spring({ frame: frame - Math.round((g[0].s - 0.05) * fps), fps, config: { damping: 14, stiffness: 220, mass: 0.6 } });
  return (
    <div style={{ position: "absolute", left: SAFE.side, right: SAFE.side, top: y, display: "flex", justifyContent: "center", flexWrap: "wrap", gap: `0 ${Math.round(th.size * 0.48)}px`,
      transform: `translateY(-50%) scale(${(p.captions.style === "pop" ? 0.82 + 0.18 * pop : 1) * (p.captionScale ?? 1)})`, fontFamily: th.font, fontWeight: th.weight, fontSize: th.size * (p.layout.captionBar && p.layout.mode === "split" ? 0.8 : 1), lineHeight: 1.08,
      textTransform: th.upper ? "uppercase" : "none", textAlign: "center", letterSpacing: -1 }}>
      {g.map((w, i) => {
        const active = t >= w.s && t < w.e + 0.08;
        const spoken = t >= w.s;
        const isEmph = emph.has(w.w.toLowerCase().replace(/[^\p{L}\p{N}']/gu, ""));
        const color = isEmph ? HOT : active ? ACCENT : p.captions.style === "karaoke" && !spoken ? "rgba(255,255,255,0.55)" : "#fff";
        return (
          <span key={i} style={{ color, display: "inline-block", transform: `scale(${active ? 1.1 : 1}) rotate(${isEmph && active ? -2 : 0}deg)`,
            WebkitTextStroke: th.stroke ? `${th.stroke}px #000` : undefined, paintOrder: "stroke fill",
            textShadow: th.stroke ? "0 8px 0 rgba(0,0,0,0.55)" : "0 4px 18px rgba(0,0,0,0.9), 0 0 4px rgba(0,0,0,0.9)" }}>{w.w}</span>
        );
      })}
    </div>
  );
};

const Hook: React.FC<{ p: ClipProps }> = ({ p }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (p.hook.enabled === false || !p.hook.text) return null;
  const end = p.hook.duration * fps;
  if (frame > end) return null;
  const s = spring({ frame, fps, config: { damping: 12, stiffness: 180 } });
  const out = interpolate(frame, [end - 6, end], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", top: SAFE.top, left: SAFE.side, right: SAFE.side, display: "flex", justifyContent: "center", opacity: out, transform: `scale(${0.6 + 0.4 * s})` }}>
      <div style={{ background: theme(p).hookBg, color: theme(p).hookFg, fontFamily: theme(p).font, fontWeight: theme(p).weight, fontSize: 64, lineHeight: 1.12, padding: "22px 36px", borderRadius: 28,
        textAlign: "center", boxShadow: "0 12px 0 rgba(0,0,0,0.45)", maxWidth: W - 2 * SAFE.side }}>{p.hook.text}</div>
    </div>
  );
};

const POS: Record<string, React.CSSProperties> = {
  top: { top: SAFE.top + 170, left: 0, right: 0, justifyContent: "center" },
  center: { top: H * 0.4, left: 0, right: 0, justifyContent: "center" },
  upper_left: { top: SAFE.top + 190, left: SAFE.side, justifyContent: "flex-start" },
  upper_right: { top: SAFE.top + 190, right: SAFE.side, justifyContent: "flex-end" },
};

const Overlay: React.FC<{ o: ClipProps["overlays"][number]; th: Theme }> = ({ o, th }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const s = spring({ frame, fps, config: { damping: 9, stiffness: 200, mass: 0.5 } });
  const out = interpolate(frame, [durationInFrames - 5, durationInFrames], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const tf = o.animation === "pop" ? `scale(${s})`
    : o.animation === "slide_up" ? `translateY(${(1 - s) * 140}px)`
    : `scale(${Math.min(1, s * 1.2)}) translateX(${Math.sin(frame * 2.4) * 14 * Math.max(0, 1 - frame / (fps * 0.6))}px) rotate(${Math.sin(frame * 1.9) * 5 * Math.max(0, 1 - frame / (fps * 0.6))}deg)`;
  return (
    <div style={{ position: "absolute", display: "flex", ...POS[o.position], opacity: out }}>
      <div style={{ transform: tf }}>
        {o.kind === "text" || !o.file ? (
          <div style={{ fontFamily: th.font, fontWeight: th.weight, fontSize: 72, color: "#fff", background: "#FF3B3B", padding: "10px 30px", borderRadius: 20, transform: "rotate(-3deg)",
            boxShadow: "0 10px 0 rgba(0,0,0,0.45)", maxWidth: W - 2 * SAFE.side, textAlign: "center" }}>{o.value}</div>
        ) : <Img src={staticFile(o.file)} style={{ width: o.kind === "emoji" ? 300 : 460, height: "auto", filter: "drop-shadow(0 10px 18px rgba(0,0,0,0.5))" }} />}
      </div>
    </div>
  );
};

/** Background music, ducked under speech: full duck while a word is spoken, easing back over ~0.3 s. */
const duckAmount = (t: number, words: Word[]) => {
  let amt = 0;
  for (const w of words) {
    const d = t < w.s ? w.s - t : t > w.e ? t - w.e : 0;
    if (d < 0.3) amt = Math.max(amt, 1 - d / 0.3);
    if (w.s > t + 0.3) break;
  }
  return amt;
};
const Music: React.FC<{ p: ClipProps }> = ({ p }) => {
  const { fps } = useVideoConfig();
  const m = p.music!;
  const words = p.captions.words;
  const total = p.duration;
  return <Audio src={staticFile(m.file)} loop volume={f => {
    const t = f / fps;
    const fade = Math.min(1, t / 0.8, Math.max(0, (total - t) / 1.2));
    return db(m.gainDb + (m.duckDb ?? -10) * duckAmount(t, words)) * fade;
  }}/>;
};

/** Streamer webcam in a corner bubble. A facecam zoom grows the bubble toward the centre (the reaction), then it settles back. */
export const bubbleGeom = (p: ClipProps, zs: number) => {
  const size = (p.layout.bubbleSize ?? 0.34) * W;
  const b = p.layout.facecamBox!;
  const round = (p.layout.bubbleShape ?? "circle") === "circle";
  const aspect = round ? 1 : Math.min(1.4, Math.max(0.8, (b.w * p.srcW) / (b.h * p.srcH)));
  const grow = 1 + (zs - 1) * 1.6; // zoom 1.4 => bubble ~1.64x bigger
  const w = Math.min(W * 0.72, size * grow), h = w / aspect;
  const corner = p.layout.bubbleCorner ?? "top_right";
  const left = corner.endsWith("left"), mid = corner.startsWith("mid");
  const k = Math.min(1, (grow - 1) / 0.64); // how far the bubble travels toward the centre while it grows
  const x0 = left ? 48 : W - 48 - w, y0 = mid ? H * 0.42 - h / 2 : p.template === "gta6_gameplay" ? 72 : SAFE.top + 190;
  const x = x0 + ((W - w) / 2 - x0) * 0.5 * k;
  return { x, y: y0, w, h, radius: round ? w / 2 : 40 };
};
const Bubble: React.FC<{ p: ClipProps; box: Box; zf: { s: number; fx: number; fy: number } }> = ({ p, box, zf }) => {
  const g = bubbleGeom(p, zf.s);
  const gameplayFocus = p.template === "gta6_gameplay";
  return (
    <div style={{ position: "absolute", left: g.x, top: g.y, width: g.w, height: g.h, borderRadius: g.radius, overflow: "hidden",
      border: gameplayFocus ? "4px solid rgba(255,255,255,0.86)" : "7px solid #fff", boxShadow: gameplayFocus ? "0 14px 34px rgba(0,0,0,0.42)" : "0 18px 40px rgba(0,0,0,0.55)" }}>
      <Region p={p} w={g.w} h={g.h} box={box} muted zoom={{ s: 1 + (zf.s - 1) * 0.35, fx: zf.fx, fy: zf.fy }} style={{ left: 0, top: 0 }} />
    </div>
  );
};

export const Clip: React.FC<ClipProps> = (p) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const { mode, facecamBox, gameplayFocusX: gx, facecamShare } = p.layout;
  const zg = zoomAt(p.zooms, mode === "split" || mode === "bubble" ? ["gameplay", "frame"] : ["gameplay", "frame", "facecam"], t, p.zoomScale ?? 1);
  const zf = zoomAt(p.zooms, ["facecam"], t, p.zoomScale ?? 1);

  // global impact transitions
  let gScale = 1, gx2 = 0, gy2 = 0, flash = 0;
  for (const tr of p.transitions) {
    const dt = t - tr.at;
    if (dt < 0 || dt > 0.45) continue;
    if (tr.kind === "flash") flash = Math.max(flash, interpolate(dt, [0, 0.25], [0.85, 0], { extrapolateRight: "clamp" }));
    if (tr.kind === "zoom_punch") gScale = interpolate(dt, [0, 0.3], [1.14, 1], { extrapolateRight: "clamp", easing: Easing.out(Easing.cubic) });
    if (tr.kind === "shake") { const a = 22 * (1 - dt / 0.45); gx2 = Math.sin(dt * 70) * a; gy2 = Math.cos(dt * 83) * a; }
  }

  const camH = Math.round(H * facecamShare);
  const barH = mode === "split" && p.layout.captionBar ? 200 : 0; // captions in their own strip: they cover neither cam nor game
  const fitH = p.layout.fitH ?? Math.round(W * 1.12);
  const fitTop = p.layout.fitTop ?? 300; // default card spans 300..1510: clear of the hook area and the bottom platform UI
  const th = theme(p);
  const captionY = p.captionY ? p.captionY : barH ? camH + barH / 2 : mode === "split" ? (p.captions.position === "lower" ? H * th.lowerY : camH + 40)
    : mode === "fit" ? fitTop + fitH - 140
    : p.captions.position === "lower" ? H * th.lowerY : H * 0.58;

  return (
    <AbsoluteFill style={{ background: "#000" }}>
      <AbsoluteFill style={{ transform: `translate(${gx2}px, ${gy2}px) scale(${gScale})` }}>
        {mode === "split" && facecamBox ? (
          <>
            <Region p={p} w={W} h={H - camH - barH} box={null} focusX={gx} muted={false} zoom={zg} style={{ top: camH + barH }} />
            <Region p={p} w={W} h={camH} box={facecamBox} muted zoom={zf} style={{ top: 0, borderBottom: barH ? undefined : "8px solid #000" }} />
            {barH > 0 && <div style={{ position: "absolute", top: camH, left: 0, width: W, height: barH, background: "#0B0B0F" }} />}
          </>
        ) : mode === "bubble" && facecamBox ? (
          <>
            <Region p={p} w={W} h={H} box={null} focusX={gx} muted={false} zoom={zg} />
            <Bubble p={p} box={facecamBox} zf={zf} />
          </>
        ) : mode === "fit" ? (
          <>
            {p.bgSrc ? ( // pre-blurred by ffmpeg: a live CSS blur on every 1080x1920 frame is the slowest part of a render
              <OffthreadVideo src={staticFile(p.bgSrc)} muted style={{ position: "absolute", left: 0, top: 0, width: W, height: H, objectFit: "cover" }} />
            ) : (
              <Region p={p} w={W} h={H} box={null} focusX={gx} muted zoom={{ s: 1.15, fx: 0.5, fy: 0.5 }} style={{ filter: "blur(38px) brightness(0.45) saturate(1.3)" }} />
            )}
            <Region p={p} w={W} h={fitH} box={null} focusX={gx} muted={false} zoom={zg} style={{ top: fitTop, borderRadius: 36, boxShadow: "0 30px 80px rgba(0,0,0,0.6)" }} />
            {p.layout.cam && facecamBox && ( // webcam card (GTA 6 template): only the webcam, white ring, top centre
              <div style={{ position: "absolute", left: p.layout.cam.x, top: p.layout.cam.y, width: p.layout.cam.w, height: p.layout.cam.h,
                borderRadius: p.layout.cam.radius, overflow: "hidden", border: "7px solid #fff", boxSizing: "border-box", boxShadow: "0 14px 34px rgba(0,0,0,0.42)" }}>
                <Region p={p} w={p.layout.cam.w} h={p.layout.cam.h} box={facecamBox} muted zoom={zf} style={{ left: 0, top: 0 }} />
              </div>
            )}
          </>
        ) : (
          <Region p={p} w={W} h={H} box={null} focusX={gx} muted={false} zoom={zg} />
        )}
      </AbsoluteFill>
      {p.overlays.map((o, i) => (
        <Sequence key={i} from={Math.round(o.start * fps)} durationInFrames={Math.max(6, Math.round((o.end - o.start) * fps))} layout="none">
          <Overlay o={o} th={theme(p)} />
        </Sequence>
      ))}
      {p.captions.enabled && p.captions.words.length > 0 && <Captions p={p} y={captionY + (p.captionOffsetY ?? 0)} />}
      {(p.logos ?? []).map((l, i) => (
        <Sequence key={"logo" + i} from={Math.round((l.start ?? 0) * fps)} durationInFrames={Math.max(1, Math.round(((l.end ?? p.duration) - (l.start ?? 0)) * fps))} layout="none">
          <Img src={staticFile(l.file)} style={{ position: "absolute", left: l.x * W - l.w / 2, top: l.y * H, width: l.w, transform: "translateY(-50%)", opacity: l.opacity ?? 1 }} />
        </Sequence>
      ))}
      {p.template !== "gta6_gameplay" && <Hook p={p} />}
      {flash > 0 && <AbsoluteFill style={{ background: "#fff", opacity: flash }} />}
      {p.sfx.map((s, i) => (
        <Sequence key={i} from={Math.max(0, Math.round(s.at * fps))} layout="none">
          <Audio src={staticFile(s.file)} volume={db(s.gainDb)} />
        </Sequence>
      ))}
      {p.music && <Music p={p}/>}
    </AbsoluteFill>
  );
};
