// 3D previs: renders one ResolvedShot (docs/PREVIS_CONTRACT.md section 9). FIXED template file,
// copied into every previs job. Reads only the ResolvedShot: per frame it takes row `frame` of
// the camera / object / part arrays. No time or pose maths here (the shot compiler did it).
//
// Props (the scene module's DEFAULTS, editable in the motion editor):
//   shadows, grid, background, clay, accent1..accent4, glass, ink (overlay colour),
//   label_<i> / dim_<i>: text of overlay i (index into data.overlays).
import React, { useMemo } from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { ThreeCanvas } from "@remotion/three";
import * as THREE from "three";
import { useGlbs } from "./loader";
import { PALETTE, type Palette } from "./materials";
import { Model } from "./Model";
import { Overlays } from "./Overlays";
import { CameraDriver, Ground, Lights } from "./Stage";
import { row, type ResolvedShot } from "./types";
import type { PropField } from "../../types";

export type PrevisProps = {
  data: ResolvedShot;
  shadows?: boolean;
  grid?: boolean;
  background?: string;
  ink?: string;
  duration?: number;
} & Partial<Palette> &
  Record<string, unknown>;

/** Default props of a previs scene module (what the generated DEFAULTS hold). */
export function previsDefaults(d: ResolvedShot): Record<string, unknown> {
  const out: Record<string, unknown> = {
    shadows: d.style.shadows,
    grid: d.set.ground.kind === "grid",
    background: d.set.background,
    ink: "#2b2924",
    ...PALETTE,
  };
  d.overlays.forEach((o, i) => (out[`${o.kind === "label" ? "label" : "dim"}_${i}`] = o.text));
  return out;
}

/** Motion-editor schema of a previs scene module. */
export function previsSchema(d: ResolvedShot): PropField[] {
  const f: PropField[] = [];
  d.overlays.forEach((o, i) =>
    f.push({ type: "text", key: `${o.kind === "label" ? "label" : "dim"}_${i}`, label: o.kind === "label" ? `标注 ${i + 1}` : `尺寸 ${i + 1}`, default: o.text }),
  );
  f.push(
    { type: "boolean", key: "shadows", label: "阴影", default: d.style.shadows },
    { type: "boolean", key: "grid", label: "网格地面", default: d.set.ground.kind === "grid" },
    { type: "color", key: "background", label: "背景", default: d.set.background },
    { type: "color", key: "ink", label: "标注颜色", default: "#2b2924" },
  );
  for (const [k, v] of Object.entries(PALETTE)) f.push({ type: "color", key: k, label: `调色板 ${k}`, default: v });
  return f;
}

export const PrevisShot: React.FC<PrevisProps> = (props) => {
  const d = props.data;
  const { width, height } = useVideoConfig();
  const frame = Math.max(0, Math.min(d.frames - 1, useCurrentFrame()));
  const glbs = useGlbs(d.objects.map((o) => o.glb));
  const shadows = props.shadows ?? d.style.shadows;
  const grid = props.grid ?? d.set.ground.kind === "grid";
  const bg = props.background ?? d.set.background;
  const pal = useMemo<Palette>(
    () => ({
      clay: (props.clay as string) ?? PALETTE.clay,
      accent1: (props.accent1 as string) ?? PALETTE.accent1,
      accent2: (props.accent2 as string) ?? PALETTE.accent2,
      accent3: (props.accent3 as string) ?? PALETTE.accent3,
      accent4: (props.accent4 as string) ?? PALETTE.accent4,
      glass: (props.glass as string) ?? PALETTE.glass,
    }),
    [props.clay, props.accent1, props.accent2, props.accent3, props.accent4, props.glass],
  );
  // scene box for the light / shadow camera: the loaded models at their first-frame placement
  const { center, r } = useMemo(() => {
    const box = new THREE.Box3();
    for (const o of d.objects) {
      const g = glbs?.[o.glb];
      if (!g) continue;
      const b = new THREE.Box3().setFromObject(g.scene);
      const x = row(o.xform, 0);
      box.union(b.translate(new THREE.Vector3(x[0], x[1], x[2])));
    }
    if (box.isEmpty()) return { center: new THREE.Vector3(0, 0.3, 0), r: 1 };
    const s = box.getBoundingSphere(new THREE.Sphere());
    return { center: s.center, r: Math.max(0.2, s.radius) };
  }, [glbs, d.objects]);
  const texts: Record<number, string> = {};
  d.overlays.forEach((o, i) => {
    const v = props[`${o.kind === "label" ? "label" : "dim"}_${i}`];
    if (typeof v === "string") texts[i] = v;
  });
  const cam = row(d.camera, frame);
  const fade =
    d.style.fade_in > 0 && frame < d.style.fade_in
      ? 1 - frame / d.style.fade_in
      : d.style.fade_out > 0 && frame > d.frames - 1 - d.style.fade_out
        ? (frame - (d.frames - 1 - d.style.fade_out)) / d.style.fade_out
        : 0;
  return (
    <AbsoluteFill style={{ background: bg }}>
      {glbs && (
        <ThreeCanvas
          width={width}
          height={height}
          shadows={shadows}
          gl={{ stencil: true, antialias: true }}
          onCreated={(s) => {
            s.gl.localClippingEnabled = true;
          }}
          style={{ background: bg }}
        >
          <color attach="background" args={[bg]} />
          <CameraDriver row={cam} />
          <Lights preset={d.set.lights} center={center} r={r} shadows={shadows} />
          <Ground kind={d.set.ground.kind} size={d.set.ground.size} color={d.set.ground.color} grid={grid} shadows={shadows} />
          {d.objects.map((o, i) => (
            <Model
              key={o.id}
              obj={o}
              gltf={glbs[o.glb]}
              frame={frame}
              sections={d.sections}
              pal={pal}
              look={d.style.look}
              shadows={shadows}
              orderBase={i * 1000}
            />
          ))}
        </ThreeCanvas>
      )}
      <Overlays overlays={d.overlays} cam={cam} frame={frame} last={d.frames - 1} width={width} height={height} texts={texts} ink={(props.ink as string) ?? "#2b2924"} />
      {fade > 0 && <AbsoluteFill style={{ background: bg, opacity: Math.min(1, fade) }} />}
    </AbsoluteFill>
  );
};
