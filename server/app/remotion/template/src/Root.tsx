import React from "react";
import { Composition } from "remotion";
import { Main } from "./Main";
import { SCENES } from "./scenes";
import tl from "./data/timeline.json";

// FIXED file (docs/REMOTION_CONTRACT.md §2). Total length = sum of scene durations.
const total = tl.scenes.reduce((n, s) => Math.max(n, s.from + s.duration), 0) || 1;

// 3D previs (docs/PREVIS_CONTRACT.md §9): one Shot-<id> composition per previs scene (a module
// exporting PREVIS = true), only that scene, no captions or audio -> jobs/<id>/shots/<id>.mp4.
const previs = tl.scenes.filter((s) => (SCENES[s.id] as { PREVIS?: boolean } | undefined)?.PREVIS);

export const Root: React.FC = () => (
  <>
    <Composition id="Main" component={Main} durationInFrames={total}
      fps={tl.fps} width={tl.width} height={tl.height} />
    {previs.map((s) => (
      <Composition key={s.id} id={`Shot-${s.id.replace(/[^A-Za-z0-9-]/g, "-")}`}
        component={SCENES[s.id].Scene} durationInFrames={s.duration} fps={tl.fps}
        width={tl.width} height={tl.height} defaultProps={{ ...SCENES[s.id].DEFAULTS, duration: s.duration }} />
    ))}
  </>
);
