import React from "react";
import { Composition } from "remotion";
import { Bench } from "./Bench";

// Greybox render benchmark: one engine-like set (~60 meshes), camera dolly + orbit,
// piston/crank animation. Props pick shadows and mesh count.
export const Root: React.FC = () => (
  <>
    {[
      ["Bench1080", 1920, 1080],
      ["Bench720", 1280, 720],
    ].map(([id, w, h]) => (
      <Composition
        key={id as string}
        id={id as string}
        component={Bench}
        durationInFrames={150}
        fps={30}
        width={w as number}
        height={h as number}
        defaultProps={{ shadows: true, copies: 1 }}
      />
    ))}
  </>
);
