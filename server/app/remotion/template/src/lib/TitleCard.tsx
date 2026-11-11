import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame, Easing } from "remotion";
import type { PropField } from "../types";

// FIXED fallback scene (docs/REMOTION_CONTRACT.md §3): used when a generated scene fails
// type-check or its still render. Follows the scene rules: pure function of the frame,
// clamped tweens, editable props with defaults, timing kept constant.
export const DEFAULTS = {
  title: "标题",
  subtitle: "",
  color: "#f4f1ea",
  accent: "#ffd166",
  background: "#101828",
  titleSize: 96,
};
export const schema: PropField[] = [
  { type: "text", key: "title", label: "标题", default: DEFAULTS.title },
  { type: "textarea", key: "subtitle", label: "副标题", default: DEFAULTS.subtitle },
  { type: "color", key: "color", label: "文字颜色", default: DEFAULTS.color },
  { type: "color", key: "accent", label: "强调色", default: DEFAULTS.accent },
  { type: "color", key: "background", label: "背景色", default: DEFAULTS.background },
  { type: "number", key: "titleSize", label: "标题字号", default: DEFAULTS.titleSize,
    min: 32, max: 200, step: 2, unit: "px" },
];

const IN = 18; // entrance length (frames): fixed rhythm, not exposed
const OUT = 12;

export const Scene: React.FC<Partial<typeof DEFAULTS> & { duration?: number }> = (p) => {
  const o = { ...DEFAULTS, ...p };
  const dur = p.duration ?? 150;
  const f = useCurrentFrame();
  const k = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
  const enter = interpolate(f, [0, IN], [0, 1], { ...k, easing: Easing.out(Easing.cubic) });
  const exit = interpolate(f, [dur - OUT, dur], [1, 0], k);
  const bar = interpolate(f, [6, IN + 10], [0, 1], { ...k, easing: Easing.out(Easing.quad) });
  return (
    <AbsoluteFill style={{ background: o.background, justifyContent: "center",
      alignItems: "center", opacity: exit, fontFamily: "Noto Sans CJK SC, sans-serif" }}>
      <div style={{ color: o.color, fontSize: o.titleSize, fontWeight: 800,
        transform: `translateY(${(1 - enter) * 40}px)`, opacity: enter, textAlign: "center",
        padding: "0 8%" }}>{o.title}</div>
      <div style={{ width: 240 * bar, height: 8, background: o.accent, margin: "32px 0",
        borderRadius: 4 }} />
      {o.subtitle && (
        <div style={{ color: o.color, opacity: 0.8 * enter, fontSize: o.titleSize * 0.4,
          textAlign: "center", padding: "0 12%", whiteSpace: "pre-line" }}>{o.subtitle}</div>
      )}
    </AbsoluteFill>
  );
};
