import React from "react";
import { Composition } from "remotion";
import { z } from "zod";
import { Clip } from "./Clip";
import studioProps from "./studio-props.json"; // written by `./dev.sh edit`: the clip being edited ({} otherwise)
import type { ClipProps } from "./types";

const box = z.object({ x: z.number(), y: z.number(), w: z.number(), h: z.number() });
// Fields shown as editable controls in Remotion Studio's side panel (anything else in props.json is kept untouched).
export const clipSchema = z.looseObject({
  captionScale: z.number().min(0.4).max(2.5).step(0.05).optional(),
  captionOffsetY: z.number().min(-900).max(900).optional(),
  zoomScale: z.number().min(0).max(3).step(0.1).optional(),
  hook: z.object({ text: z.string(), duration: z.number(), enabled: z.boolean().optional() }),
  captions: z.object({
    enabled: z.boolean(), style: z.enum(["pop", "karaoke"]), position: z.enum(["middle", "lower"]),
    emphasis: z.array(z.string()), words: z.array(z.object({ w: z.string(), s: z.number(), e: z.number() })),
  }),
  overlays: z.array(z.object({
    kind: z.enum(["emoji", "text", "image"]), start: z.number(), end: z.number(), value: z.string(), file: z.string().nullable(),
    position: z.enum(["top", "center", "upper_left", "upper_right"]), animation: z.enum(["pop", "slide_up", "shake"]),
  })),
  zooms: z.array(z.object({
    start: z.number(), end: z.number(), target: z.enum(["gameplay", "facecam", "frame"]), scale: z.number(),
    fx: z.number(), fy: z.number(), style: z.enum(["punch", "smooth"]),
  })),
  logos: z.array(z.object({ file: z.string(), x: z.number(), y: z.number(), w: z.number(),
    start: z.number().optional(), end: z.number().optional(), opacity: z.number().optional() })).optional(),
  template: z.string().optional(),
  layout: z.looseObject({ mode: z.enum(["split", "bubble", "full", "fit"]), facecamBox: box.nullable(), gameplayFocusX: z.number(), facecamShare: z.number() }),
});

const defaults: ClipProps = {
  fps: 30, duration: 5, src: "base.mp4", srcW: 1280, srcH: 720,
  layout: { mode: "fit", facecamBox: null, gameplayFocusX: 0.5, facecamShare: 0.36 },
  zooms: [], captions: { enabled: true, style: "pop", position: "middle", emphasis: [], words: [] },
  overlays: [], sfx: [], transitions: [], hook: { text: "Hook", duration: 2.5, enabled: true }, music: null,
  captionScale: 1, captionOffsetY: 0, zoomScale: 1, logos: [],
};

export const Root: React.FC = () => (
  <Composition
    id="Clip"
    schema={clipSchema}
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    component={Clip as unknown as React.FC<any>}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={150}
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    defaultProps={{ ...defaults, ...(studioProps as object) } as any}
    calculateMetadata={({ props }) => {
      const p = props as unknown as ClipProps;
      return { fps: p.fps, durationInFrames: Math.max(1, Math.round(p.duration * p.fps)) };
    }}
  />
);
