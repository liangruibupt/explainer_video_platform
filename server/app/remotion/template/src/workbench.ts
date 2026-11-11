import { Main, TL, CAPTIONS, AUDIO } from "./Main";
import { SCENES } from "./scenes";
import { Captions, CAPTION_DEFAULTS, CAPTION_SCHEMA } from "./lib/Captions";

// FIXED file (docs/REMOTION_CONTRACT.md §4): the shotcraft Motion Workbench manifest
// (references/workbench.md §2). Same data as Main.tsx, so import is lossless (npm run parity).
// The voice track (narration + ducked music) is listed under bgm; the studio re-imposes it
// on every saved version, so it is locked even though the editor lets you drag it.
export const WORKBENCH = {
  name: TL.scenes[0]?.label ?? "explainer",
  fps: TL.fps, width: TL.width, height: TL.height,
  total: TL.scenes.reduce((n, s) => Math.max(n, s.from + s.duration), 0),
  background: TL.background,
  revision: TL.revision,
  shots: TL.scenes.filter((s) => SCENES[s.id]).map((s) => ({
    id: s.id, label: s.label, from: s.from, duration: s.duration,
    component: SCENES[s.id].Scene,
    props: { ...SCENES[s.id].DEFAULTS },
    schema: SCENES[s.id].schema,
    durationProp: "duration",
    cardId: `scene-${s.id}`, cardName: s.label,
  })),
  captions: CAPTIONS.map((c) => ({
    id: c.id, label: c.text.slice(0, 12), from: c.from, duration: c.duration,
    component: Captions,
    props: { ...CAPTION_DEFAULTS, text: c.text, chars: c.chars },
    schema: CAPTION_SCHEMA,
    durationProp: "duration",
    cardId: "caption", cardName: "字幕",
  })),
  sfx: AUDIO.sfx.map((x) => ({ from: x.from, duration: x.duration, src: x.src,
    volume: x.volume, label: x.label ?? x.id })),
  bgm: AUDIO.voice ? [{ from: AUDIO.voice.from, duration: AUDIO.voice.duration,
    src: AUDIO.voice.src, volume: AUDIO.voice.volume, label: "配音（已锁定）" }] : [],
  order: ["captions"] as ("transitions" | "captions" | "overlays")[],
  original: Main,
};
