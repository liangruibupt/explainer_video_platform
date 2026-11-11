import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile } from "remotion";
import tlJson from "./data/timeline.json";
import capsJson from "./data/captions.json";
import audioJson from "./data/audio.json";
import { SCENES } from "./scenes";
import { Captions, CAPTION_DEFAULTS } from "./lib/Captions";
import type { AudioData, CaptionLine, Timeline } from "./types";

// FIXED file (docs/REMOTION_CONTRACT.md §2). Everything here is driven by the three data
// files; src/workbench.ts imports the same data, so editor import == this composition.
export const TL = tlJson as Timeline;
export const CAPTIONS = capsJson as CaptionLine[];
export const AUDIO = audioJson as AudioData;

export const Main: React.FC = () => (
  <AbsoluteFill style={{ background: TL.background }}>
    {TL.scenes.map((s) => {
      const m = SCENES[s.id];
      if (!m) return null;
      return (
        <Sequence key={s.id} name={s.label} from={s.from} durationInFrames={s.duration}>
          <m.Scene {...m.DEFAULTS} duration={s.duration} />
        </Sequence>
      );
    })}
    {CAPTIONS.map((c) => (
      <Sequence key={c.id} name={`cap ${c.id}`} from={c.from} durationInFrames={c.duration}>
        <Captions {...CAPTION_DEFAULTS} text={c.text} chars={c.chars} duration={c.duration} />
      </Sequence>
    ))}
    {AUDIO.voice && (
      <Sequence from={AUDIO.voice.from} durationInFrames={AUDIO.voice.duration} name="voice">
        <Audio src={staticFile(AUDIO.voice.src)} volume={AUDIO.voice.volume} />
      </Sequence>
    )}
    {AUDIO.sfx.map((x) => (
      <Sequence key={x.id} from={x.from} durationInFrames={x.duration} name={`sfx ${x.id}`}>
        <Audio src={staticFile(x.src)} volume={x.volume} />
      </Sequence>
    ))}
  </AbsoluteFill>
);
