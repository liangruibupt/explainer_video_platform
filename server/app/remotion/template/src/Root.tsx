import React from "react";
import { Composition } from "remotion";
import { Main } from "./Main";
import tl from "./data/timeline.json";

// FIXED file (docs/REMOTION_CONTRACT.md §2). Total length = sum of scene durations.
const total = tl.scenes.reduce((n, s) => Math.max(n, s.from + s.duration), 0) || 1;

export const Root: React.FC = () => (
  <Composition id="Main" component={Main} durationInFrames={total}
    fps={tl.fps} width={tl.width} height={tl.height} />
);
