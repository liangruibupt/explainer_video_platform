import React from "react";
import { Composition, registerRoot } from "remotion";
import { Preview } from "./Preview";

const Root: React.FC = () =>
  React.createElement(Composition, {
    id: "Preview",
    component: Preview,
    durationInFrames: 1,
    fps: 30,
    width: 640,
    height: 640,
    defaultProps: { glb: "model.glb", az: 35, clay: true },
  });

registerRoot(Root);
