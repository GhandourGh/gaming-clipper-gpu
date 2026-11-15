// Render props produced by the worker (worker/clipper/render.py). Times are OUTPUT seconds.
export type Box = { x: number; y: number; w: number; h: number };
export type Word = { w: string; s: number; e: number };
export type Style = "viral" | "funny" | "cinematic" | "fast" | "minimal";
export type ClipProps = {
  template?: string;
  bgSrc?: string | null;
  style?: Style;
  fps: number;
  duration: number;
  src: string; // file inside the public dir
  srcW: number;
  srcH: number;
  layout: { mode: "split" | "bubble" | "full" | "fit"; facecamBox: Box | null; gameplayFocusX: number; facecamShare: number;
    bubbleCorner?: "top_left" | "top_right" | "mid_left" | "mid_right"; bubbleShape?: "circle" | "rounded"; bubbleSize?: number;
    gameplayArea?: Box | null; captionBar?: boolean };
  zooms: { start: number; end: number; target: "gameplay" | "facecam" | "frame"; scale: number; fx: number; fy: number; style: "punch" | "smooth" }[];
  captions: { enabled: boolean; style: "pop" | "karaoke"; position: "middle" | "lower"; emphasis: string[]; words: Word[] };
  overlays: { kind: "emoji" | "text" | "image"; start: number; end: number; value: string; file: string | null; position: "top" | "center" | "upper_left" | "upper_right"; animation: "pop" | "slide_up" | "shake" }[];
  sfx: { at: number; file: string; gainDb: number }[];
  transitions: { at: number; kind: "flash" | "zoom_punch" | "shake" }[];
  hook: { text: string; duration: number; enabled?: boolean };
  music: { file: string; gainDb: number; duckDb?: number } | null;
  // --- editor extras (all optional; edit them in Remotion Studio's props panel) ---
  captionScale?: number;   // 1 = default size, 1.3 = 30% bigger
  captionOffsetY?: number; // pixels, negative = up
  logos?: { file: string; x: number; y: number; w: number; start?: number; end?: number; opacity?: number }[]; // file inside public/ (e.g. logos/me.png); x,y = centre as 0-1 of the screen; w = width in px
  zoomScale?: number;      // multiplies every zoom's strength: 0 = no zooms, 1 = as planned, 1.5 = stronger
};
