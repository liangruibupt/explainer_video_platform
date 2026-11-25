// Easing and path interpolation for the shot compiler (docs/PREVIS_CONTRACT.md section 7).
import type { Ease, V3 } from "../core/types";

/** Eased progress for u in [0,1]. `hold` stays at 0 until the key frame itself. */
export function ease(kind: Ease | undefined, u: number): number {
  const x = Math.min(1, Math.max(0, u));
  switch (kind ?? "inOut") {
    case "linear":
      return x;
    case "in":
      return x * x;
    case "out":
      return 1 - (1 - x) * (1 - x);
    case "inOut":
      return x < 0.5 ? 2 * x * x : 1 - 2 * (1 - x) * (1 - x);
    case "inOutCubic":
      return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
    case "hold":
      return x >= 1 ? 1 : 0;
    default:
      return x;
  }
}

export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
export const lerp3 = (a: V3, b: V3, t: number): V3 => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const len = (a: V3) => Math.hypot(a[0], a[1], a[2]);

/**
 * Centripetal Catmull-Rom (alpha 0.5) between p1 and p2 at u in [0,1], neighbours p0 / p3
 * (Barry-Goldman pyramid). Missing neighbours are mirrored (p0 = 2 p1 - p2), which makes the
 * end segments start/finish along the chord.
 */
export function catmull(p0: V3 | null, p1: V3, p2: V3, p3: V3 | null, u: number): V3 {
  const a = p0 ?? sub([2 * p1[0], 2 * p1[1], 2 * p1[2]], p2);
  const d = p3 ?? sub([2 * p2[0], 2 * p2[1], 2 * p2[2]], p1);
  const EPS = 1e-6;
  const k = (x: V3, y: V3) => Math.max(EPS, Math.sqrt(len(sub(y, x))));
  const t0 = 0;
  const t1 = t0 + k(a, p1);
  const t2 = t1 + k(p1, p2);
  const t3 = t2 + k(p2, d);
  if (len(sub(p2, p1)) < EPS) return [...p1] as V3;
  const t = lerp(t1, t2, u);
  const mix = (x: V3, y: V3, ta: number, tb: number): V3 => lerp3(x, y, (t - ta) / (tb - ta));
  const A1 = mix(a, p1, t0, t1);
  const A2 = mix(p1, p2, t1, t2);
  const A3 = mix(p2, d, t2, t3);
  const B1 = mix(A1, A2, t0, t2);
  const B2 = mix(A2, A3, t1, t3);
  return mix(B1, B2, t1, t2);
}

/** Vertical field of view (degrees) of a full-frame lens on a 24 mm high frame. */
export const lensToVfov = (mm: number) => (2 * Math.atan(12 / mm) * 180) / Math.PI;
