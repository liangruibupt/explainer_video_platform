import type React from "react";

// FIXED file (docs/REMOTION_CONTRACT.md §3). PropField mirrors the shotcraft workbench
// schema types (workbench/src/cards/types.ts) so the manifest is structurally compatible.
export type PropField =
  | { type: "text" | "textarea"; key: string; label: string; default?: string }
  | { type: "number" | "slider"; key: string; label: string; default?: number;
      min?: number; max?: number; step?: number; unit?: string }
  | { type: "color"; key: string; label: string; default?: string }
  | { type: "select"; key: string; label: string; default?: string;
      options: { value: string; label: string }[] }
  | { type: "boolean"; key: string; label: string; default?: boolean };

/** One generated scene module, src/scenes/sNN.tsx. */
export type SceneModule = {
  Scene: React.ComponentType<any>;
  DEFAULTS: Record<string, unknown>;
  schema: PropField[];
};

export type TimelineScene = {
  id: string; label: string; from: number; duration: number;
  /** shotcraft card used for this scene, "<category>/<card>", or "fallback/title-card". */
  card: string;
};
export type Timeline = {
  fps: number; width: number; height: number; background: string;
  /** Bumped by the pipeline on every regeneration; the workbench uses it as revision. */
  revision: string;
  scenes: TimelineScene[];
};
export type CaptionLine = {
  id: string; scene: string; from: number; duration: number; text: string;
  /** Per-character [start, end] in frames RELATIVE to the line's `from`; same length as text. */
  chars: [number, number][];
};
export type AudioTrack = { id: string; from: number; duration: number; src: string;
  volume: number; label?: string };
export type AudioData = { voice: AudioTrack | null; sfx: AudioTrack[] };
