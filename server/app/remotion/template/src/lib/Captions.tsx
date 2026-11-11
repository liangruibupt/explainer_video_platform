import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import type { PropField } from "../types";

// Part B (docs/REMOTION_CONTRACT.md §5). Same look as the ASS burn-in captions.py writes for
// the html renderer: Noto Sans CJK SC bold, ASS size = 4.5 % of the frame height, white text,
// each character turns gold when it is spoken (karaoke \k), black outline of size/16 px,
// bottom-centred with a bottom margin of 6 % of the height, 60 px side margins.
//
// libass scales a font so that (winAscent + winDescent) == the ASS size; for Noto Sans CJK
// that is 1.448 em, so the CSS em is the ASS size / 1.448 and the line box is the ASS size.
const EM_PER_ASS = 1 / 1.448;

export const CAPTION_DEFAULTS = {
  text: "",
  /** Per-character [start, end] frames relative to the line start (captions.json). */
  chars: [] as [number, number][],
  /** Size as % of the frame height (captions.py: 4.5). */
  size: 4.5,
  color: "#ffffff",
  highlight: "#ffd700",
  outline: "#000000",
  /** Bottom margin as % of the frame height (captions.py: 6). */
  bottom: 6,
};
export const CAPTION_SCHEMA: PropField[] = [
  { type: "textarea", key: "text", label: "文案", default: "" },
  { type: "slider", key: "size", label: "字号（画高 %）", default: 4.5, min: 2, max: 10,
    step: 0.1, unit: "%" },
  { type: "color", key: "color", label: "文字颜色", default: "#ffffff" },
  { type: "color", key: "highlight", label: "高亮颜色", default: "#ffd700" },
  { type: "color", key: "outline", label: "描边颜色", default: "#000000" },
  { type: "slider", key: "bottom", label: "底边距（画高 %）", default: 6, min: 0, max: 40,
    step: 0.5, unit: "%" },
];

/** Frame (relative to the line) at which character i turns to the highlight colour. When the
 * text was edited so its length no longer matches `chars`, spread the highlight evenly over
 * the line's spoken span instead. */
const onsets = (n: number, chars: [number, number][], duration: number): number[] => {
  if (chars.length === n) return chars.map((c) => c[0]);
  const a = chars.length ? chars[0][0] : 0;
  const b = chars.length ? chars[chars.length - 1][1] : Math.max(1, duration - 8);
  return Array.from({ length: n }, (_, i) => a + ((b - a) * i) / Math.max(n, 1));
};

export const Captions: React.FC<Partial<typeof CAPTION_DEFAULTS> & { duration?: number }> = (p) => {
  const o = { ...CAPTION_DEFAULTS, ...p };
  const f = useCurrentFrame();
  const { height } = useVideoConfig();
  const glyphs = [...(o.text ?? "")];
  const at = onsets(glyphs.length, o.chars ?? [], p.duration ?? 9999);
  const ass = Math.round((height * o.size) / 100);
  const stroke = Math.max(2, Math.floor(ass / 16));
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center",
      paddingBottom: Math.round((height * o.bottom) / 100), paddingLeft: 60, paddingRight: 60 }}>
      <div style={{
        fontFamily: "'Noto Sans CJK SC', 'Noto Sans SC', sans-serif", fontWeight: 700,
        fontSize: ass * EM_PER_ASS, lineHeight: `${ass}px`, textAlign: "center",
        whiteSpace: "pre-wrap", wordBreak: "break-all",
        WebkitTextStroke: `${stroke * 2}px ${o.outline}`, paintOrder: "stroke fill",
        strokeLinejoin: "round",
      }}>
        {glyphs.map((ch, i) => (
          <span key={i} style={{ color: f >= at[i] ? o.highlight : o.color }}>{ch}</span>
        ))}
      </div>
    </AbsoluteFill>
  );
};
