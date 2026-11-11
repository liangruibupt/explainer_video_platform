import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import type { PropField } from "../types";

// SCAFFOLD STUB, owned by part B (docs/REMOTION_CONTRACT.md §5). B replaces the body with the
// word-by-word highlight matching captions.py's ASS style; the exports' names stay.
export const CAPTION_DEFAULTS = {
  text: "",
  chars: [] as [number, number][],
  fontSize: 54,
  color: "#ffffff",
  highlight: "#ffd700",
};
export const CAPTION_SCHEMA: PropField[] = [
  { type: "textarea", key: "text", label: "文案", default: "" },
  { type: "number", key: "fontSize", label: "字号", default: 54, min: 24, max: 120, step: 2,
    unit: "px" },
  { type: "color", key: "color", label: "文字颜色", default: "#ffffff" },
  { type: "color", key: "highlight", label: "高亮颜色", default: "#ffd700" },
];

export const Captions: React.FC<Partial<typeof CAPTION_DEFAULTS> & { duration?: number }> = (p) => {
  const o = { ...CAPTION_DEFAULTS, ...p };
  const f = useCurrentFrame();
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: "6%" }}>
      <div style={{ fontSize: o.fontSize, fontWeight: 700, fontFamily: "Noto Sans CJK SC, sans-serif" }}>
        {[...o.text].map((ch, i) => {
          const on = o.chars[i] ? f >= o.chars[i][0] : false;
          return <span key={i} style={{ color: on ? o.highlight : o.color }}>{ch}</span>;
        })}
      </div>
    </AbsoluteFill>
  );
};
