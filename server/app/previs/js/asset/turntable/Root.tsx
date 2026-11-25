import React from "react";
import { Composition } from "remotion";
import { FPS, FRAMES, Turntable, type TurntableProps } from "./Turntable";

// One composition: 4 s, 360 degrees, 720p. Props come from --props (turntable.ts).
export const Root: React.FC = () => (
  <Composition
    id="Turntable"
    component={Turntable}
    durationInFrames={FRAMES}
    fps={FPS}
    width={1280}
    height={720}
    defaultProps={{ glb: "model.glb", bbox: { min: [-0.5, 0, -0.5], max: [0.5, 1, 0.5] }, rig: null } as TurntableProps}
  />
);
