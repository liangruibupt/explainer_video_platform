// HTML labels and dimension lines over the canvas (not in WebGL), Noto Sans CJK SC, kept out of
// the bottom 18 % caption zone. Positions come from projecting the compiler's world points with
// the same camera as the canvas.
import React from "react";
import * as THREE from "three";
import { applyCamera, toScreen } from "./camera";
import { row, type ROverlay } from "./types";

const FONT = "'Noto Sans CJK SC', 'Noto Sans SC', sans-serif";
export const CAPTION_ZONE = 0.18;

const fadeIn = (f: number, from: number, to: number, last: number) => {
  const a = Math.min(1, (f - from + 1) / 8);
  const b = to >= last ? 1 : Math.min(1, (to - f + 1) / 6);
  return Math.max(0, Math.min(a, b));
};

export const Overlays: React.FC<{
  overlays: ROverlay[];
  cam: number[];
  frame: number;
  last: number;
  width: number;
  height: number;
  texts: Record<number, string>;
  ink: string;
}> = ({ overlays, cam, frame, last, width: W, height: H, texts, ink }) => {
  const c = applyCamera(new THREE.PerspectiveCamera(), cam, W / H);
  const maxY = H * (1 - CAPTION_ZONE);
  const fs = Math.round(H * 0.03);
  const out: React.ReactNode[] = [];
  overlays.forEach((o, i) => {
    if (frame < o.from || frame > o.to) return;
    const op = fadeIn(frame, o.from, o.to, last);
    const text = texts[i] ?? o.text;
    if (o.kind === "label") {
      const p = toScreen(c, o.track ? row(o.track, frame - o.from) : o.point, W, H);
      if (!p) return;
      const [x, y] = p;
      const boxW = fs * (text.length * 1.05 + 1.4);
      const boxH = fs * 1.7;
      let bx = o.side === "left" ? x - W * 0.07 - boxW : o.side === "top" ? x - boxW / 2 : x + W * 0.07;
      let by = o.side === "top" ? y - H * 0.11 - boxH : y - H * 0.07 - boxH / 2;
      bx = Math.max(W * 0.03, Math.min(W * 0.97 - boxW, bx));
      by = Math.max(H * 0.04, Math.min(maxY - boxH - H * 0.01, by));
      const ax = o.side === "left" ? bx + boxW : o.side === "top" ? bx + boxW / 2 : bx;
      const ay = o.side === "top" ? by + boxH : by + boxH / 2;
      out.push(
        <g key={i} opacity={op}>
          <line x1={x} y1={y} x2={ax} y2={ay} stroke={ink} strokeWidth={Math.max(2, H / 450)} />
          <circle cx={x} cy={y} r={H / 170} fill={ink} stroke="#ffffff" strokeWidth={H / 540} />
          <rect x={bx} y={by} width={boxW} height={boxH} rx={boxH * 0.22} fill="#ffffff" fillOpacity={0.92} stroke={ink} strokeWidth={Math.max(1.5, H / 720)} />
          <text x={bx + boxW / 2} y={by + boxH * 0.68} fontSize={fs} fontFamily={FONT} fontWeight={700} fill="#1f1d1a" textAnchor="middle">
            {text}
          </text>
        </g>,
      );
    } else {
      const a = toScreen(c, o.a, W, H);
      const b = toScreen(c, o.b, W, H);
      if (!a || !b) return;
      const dx = b[0] - a[0];
      const dy = b[1] - a[1];
      const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len;
      const ny = dx / len;
      const t = H * 0.012;
      const ar = H * 0.012;
      const ux = dx / len;
      const uy = dy / len;
      const head = (px: number, py: number, s: number) =>
        `${px},${py} ${px - s * ux * ar + nx * ar * 0.5},${py - s * uy * ar + ny * ar * 0.5} ${px - s * ux * ar - nx * ar * 0.5},${py - s * uy * ar - ny * ar * 0.5}`;
      // text on the side of the line that points up (or right), out of the caption zone
      let side = ny < 0 || (Math.abs(ny) < 1e-6 && nx > 0) ? 1 : -1;
      let mx = (a[0] + b[0]) / 2 + side * nx * H * 0.04;
      let my = (a[1] + b[1]) / 2 + side * ny * H * 0.04;
      if (my > maxY - fs) {
        side = -side;
        mx = (a[0] + b[0]) / 2 + side * nx * H * 0.04;
        my = Math.min(maxY - fs, (a[1] + b[1]) / 2 + side * ny * H * 0.04);
      }
      const dfs = Math.round(H * 0.026);
      const tw = dfs * (text.length * 0.62 + 1);
      out.push(
        <g key={i} opacity={op} stroke={ink} strokeWidth={Math.max(2, H / 500)}>
          <line x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} />
          <line x1={a[0] - nx * t} y1={a[1] - ny * t} x2={a[0] + nx * t} y2={a[1] + ny * t} />
          <line x1={b[0] - nx * t} y1={b[1] - ny * t} x2={b[0] + nx * t} y2={b[1] + ny * t} />
          <polygon points={head(a[0], a[1], -1)} fill={ink} stroke="none" />
          <polygon points={head(b[0], b[1], 1)} fill={ink} stroke="none" />
          {text && (
            <>
              <rect x={mx - tw / 2} y={my - dfs * 0.85} width={tw} height={dfs * 1.3} rx={dfs * 0.25} fill="#ffffff" fillOpacity={0.9} stroke="none" />
              <text x={mx} y={my + dfs * 0.12} fontSize={dfs} fontFamily={FONT} fontWeight={700} fill="#1f1d1a" stroke="none" textAnchor="middle">
                {text}
              </text>
            </>
          )}
        </g>,
      );
    }
  });
  return (
    <svg width={W} height={H} style={{ position: "absolute", left: 0, top: 0, pointerEvents: "none" }}>
      {out}
    </svg>
  );
};
