"""Opus 5.5 prompts for the Remotion renderer (docs/REMOTION_CONTRACT.md §3, part A).

pick_cards: one call that assigns a shotcraft card to every scene (plus one shared look).
scene_module: one call per scene that writes src/scenes/<id>.tsx from the scene and its card.
"""
from __future__ import annotations

import json
import re

from .. import llm
from . import catalog

# Allowed per-position defaults when the picker fails or names an unknown card.
DEFAULT_CARDS = {"first": "opening/orbit-ring-title-open", "last": "outro/logo-shrink-wordmark-lockup",
                 "middle": "typography/blur-slide", "data": "data/chart-live-moves"}
LIB_MODULES = {"Polish", "Look", "Motion", "Brand", "TitleCard"}
DEFAULT_LOOK = {"background": "#101828", "ink": "#f4f1ea", "dim": "#9aa4b2", "accent": "#ffd166",
                "accent2": "#7fb0c8"}

PICK_SYSTEM = """You are the motion director of a short narrated explainer video. The video is
rendered with Remotion; every scene is animated after ONE shot card from the video-shotcraft
library (a motion recipe + a reference Remotion implementation).
You get the storyboard (scene id, title, narration, visual description, length, assets) and the
card catalog, one card per line: `<card id> [UI-mock]? | one-line summary | 适用 | 时长`.
Pick the card whose motion best tells each scene's content:
- the first / title scene -> an `opening/*` or a title `typography/*` card;
- a closing scene / summary / end card -> an `outro/*` card or a list/recap card;
- numbers, comparisons, growth, proportions -> `data/*`; steps, lists, processes -> `ui-entrance/*`
  list/stack cards or `rhythm/*`; one key sentence -> `typography/*`;
- cards marked [UI-mock] are built around fake product screenshots / app UI: avoid them unless
  the scene has an illustration or a clip that can stand in for the screenshot;
- transitions/* cards are cuts between two shots: use them only if the scene itself is a turn;
- vary the cards: do not use the same card twice in a row, use any card at most twice.
Also choose ONE look for the whole film (colours as #rrggbb) that fits the topic; if the film has a
light paper background image, use dark ink.
Return ONLY JSON, no prose, no code fence:
{"look": {"background": "#..", "ink": "#..", "dim": "#..", "accent": "#..", "accent2": "#.."},
 "cards": {"<scene id>": "<card id>", ...}, "why": {"<scene id>": "<few words>", ...}}"""

SCENE_SYSTEM = """You write ONE Remotion scene component (TypeScript + React, a .tsx module) for a
narrated explainer video. You get the scene (narration, visual description, length, frame size,
assets), the film's shared look, and ONE video-shotcraft shot card: its motion recipe (markdown,
Chinese) and its reference Remotion demo source. Re-create the card's motion language (phases,
easing, stagger, camera path, energy) with THIS scene's content. Do not copy the demo's product
copy, logos or fake UI: tell the scene's content (the narration and visual description).

The module exports exactly these three names:
  export const DEFAULTS = { ... };   // every editable value: all on-screen copy (Chinese as in
                                     // the scene), colours, font sizes, positions, toggles; lists
                                     // as one multi-line string (one item per line, split on "\\n")
  export const schema: PropField[] = [ ... ];  // one field per DEFAULTS key worth editing,
                                     // labels in Chinese; import type { PropField } from "../types"
  export const Scene: React.FC<Partial<typeof DEFAULTS> & { duration?: number }> = (p) => {
    const o = { ...DEFAULTS, ...p };
    const dur = p.duration ?? <scene frames>;
    ...
  };
PropField = {type:"text"|"textarea",key,label,default?} | {type:"number"|"slider",key,label,
default?,min?,max?,step?,unit?} | {type:"color",key,label,default?} | {type:"select",key,label,
default?,options:{value,label}[]} | {type:"boolean",key,label,default?}.

Hard rules:
1. Pure function of useCurrentFrame() (frame 0 = scene start). No Date, no Math.random (use the
   deterministic `rand(seed)` from ../lib/Motion or remotion's random("seed")), no timers, no
   requestAnimationFrame, no state/effects. Every interpolate() gets
   {extrapolateLeft: "clamp", extrapolateRight: "clamp"} and a strictly increasing input range
   (guard ranges computed from dur with Math.max so they stay increasing for short scenes).
2. Rhythm is constant code (entrance lengths, stagger, easing, camera path); only context values
   go into DEFAULTS/schema.
3. Use `dur` for the exit and to spread the scene's beats over its whole length; never use a fixed
   frame past dur. The demo's absolute frame numbers are for its own length: rescale its beats so
   the climax lands well inside dur. Something must move during the whole scene (slow drift,
   camera push, breathing glow) but every text must be fully visible by 0.6*dur and stay until a
   short exit fade in the last ~12 frames.
4. Keep the bottom 15% of the frame free of text and key visuals (captions go there). Lay out for
   the given width x height (both 1920x1080 and 1080x1920 occur: read useVideoConfig() width and
   height for layout, never for timing). Text must fit: wrap long Chinese lines (maxWidth +
   wordBreak), sizes relative to the frame (titles >= 5.5% of height in landscape, >= 3.2% in
   portrait; labels >= 2.8% / 2%).
5. Fonts: fontFamily '"Noto Sans CJK SC", "Noto Sans SC", sans-serif' for all text (the only
   installed CJK font). Use the shared look colours.
6. Assets only via staticFile(): images with <Img src={staticFile("img/<id>.png")} />, clips with
   <OffthreadVideo muted src={staticFile("clips/<id>.mp4")} />, the film background with
   staticFile("background.png"). Only use the assets listed for this scene. No network, no URLs.
7. Imports allowed: "react", "remotion", "../types", and "../lib/Polish", "../lib/Look",
   "../lib/Motion", "../lib/Brand" (unchanged copies of the demo's ../../_fixtures/* files, same
   exports). Nothing else exists: no ../../_fixtures/Fixtures, PageCam2D, _textures, assets/lib,
   @remotion/* packages, no other npm packages. Do not use useT() from Motion (it reads the whole
   composition length); compute progress from frame and dur. Do not use the Shotcraft logo/brand.
8. Draw the scene's own opaque background (the look's background colour, or the background
   image / illustration / clip). Keep it light enough to render fast: no huge blur filters on
   full-frame layers, at most ~150 animated elements.
9. Valid TypeScript (tsc, strict off). Keep it compact: under ~260 lines.
Return ONLY the module source, starting with `import`, no prose, no code fence."""


def _strip_fence(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*\n", "", t)
    t = re.sub(r"\n```\s*$", "", t)
    return t


def parse_json(text: str) -> dict:
    t = _strip_fence(text)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        raise ValueError("model output has no JSON object")
    return json.loads(t[i: j + 1])


def _brief(s: dict, frames: int, images: dict, clips: dict) -> dict:
    b = {k: s.get(k) for k in ("id", "title", "narration", "visual")}
    b["seconds"] = round(frames / 30, 1)
    if s["id"] in images:
        b["illustration"] = (s.get("illustration") or {}).get("place", "background")
    if s["id"] in clips:
        b["clip"] = True
    return b


def default_card(i: int, n: int, s: dict, known: set) -> str:
    text = f"{s.get('title', '')}{s.get('visual', '')}"
    key = ("first" if i == 0 else "last" if i == n - 1 else
           "data" if re.search(r"\d+\s*(%|％|倍|万|亿|年|ms|秒)", text) else "middle")
    cid = DEFAULT_CARDS[key]
    return cid if cid in known else sorted(known)[0]


def pick_cards(scenes: list, frames: list, w: int, h: int, images: dict, clips: dict,
               has_background: bool, cards: list, log=None) -> tuple[dict, dict]:
    """-> (look, {scene id: card id}); unknown or missing picks get a positional default."""
    known = {c.id for c in cards}
    board = [_brief(s, frames[i], images, clips) for i, s in enumerate(scenes)]
    user = (f"Frame {w}x{h} ({'portrait 9:16' if h > w else 'landscape 16:9'}), 30 fps."
            f"{' The film has a light paper background image under every scene.' if has_background else ''}\n"
            f"Storyboard:\n{json.dumps(board, ensure_ascii=False, indent=1)}\n\nCard catalog:\n"
            + "\n".join(c.line() for c in cards))
    look, picks = dict(DEFAULT_LOOK), {}
    try:
        out = parse_json(llm.ask(PICK_SYSTEM, user, max_tokens=3000))
        look.update({k: v for k, v in (out.get("look") or {}).items()
                     if k in DEFAULT_LOOK and re.fullmatch(r"#[0-9a-fA-F]{6}", str(v))})
        picks = out.get("cards") or {}
    except Exception as e:  # noqa: BLE001 - positional defaults below
        if log:
            log(f"remotion: card picking failed ({e}); using default cards")
    res = {}
    for i, s in enumerate(scenes):
        cid = str(picks.get(s["id"], "")).strip()
        if cid not in known:
            if cid and log:
                log(f"remotion: unknown card {cid!r} for {s['id']}; using a default")
            cid = default_card(i, len(scenes), s, known)
        res[s["id"]] = cid
    return look, res


def scene_prompt(s: dict, idx: int, total: int, frames: int, w: int, h: int, card,
                 look: dict, assets: list, context: str = "", error: str = "",
                 previous: str = "") -> str:
    brief = {k: s.get(k) for k in ("id", "title", "narration", "visual")}
    ill = (s.get("illustration") or {}).get("place")
    parts = [
        f"Frame {w}x{h} ({'portrait 9:16' if h > w else 'landscape 16:9'}), 30 fps. Scene {idx + 1} "
        f"of {total}; it lasts {frames} frames ({frames / 30:.1f} s): use `p.duration ?? {frames}`.",
        f"Scene:\n{json.dumps(brief, ensure_ascii=False, indent=1)}",
        f"Shared look: {json.dumps(look)}",
        ("Assets for this scene: " + ", ".join(assets)) if assets else
        "Assets for this scene: none (draw everything with shapes, text and SVG).",
    ]
    if ill:
        parts.append(f"The illustration img/{s['id']}.png is meant for placement '{ill}' "
                     "(background = full-frame under a dark scrim; left/right = a large framed "
                     "panel on that side, text on the other side; in portrait put it top).")
    if context:
        parts.append(context)
    parts.append(f"Shot card {card.id}: recipe (references/shots/{card.id}.md):\n"
                 + card.md.read_text(encoding="utf-8")[:9000])
    parts.append(f"Reference demo source (demos/{card.id}/):\n" + catalog.demo_source(card))
    if error:
        parts.append("Your previous module for this scene FAILED. Fix it; keep what worked.\n"
                     f"Error:\n{error[-4000:]}\n\nPrevious module:\n{previous[:20000]}")
    return "\n\n".join(parts)


def extract_tsx(text: str) -> str:
    t = _strip_fence(text)
    i = t.find("import")
    if i < 0:
        raise ValueError("model output has no import line")
    return t[i:].rstrip() + "\n"


_FORBIDDEN = [(r"\bDate\b", "Date"), (r"Math\.random", "Math.random"),
              (r"requestAnimationFrame|setTimeout|setInterval", "timers"),
              (r"\buse(State|Effect|LayoutEffect)\b", "React state/effects"),
              (r"\bfetch\(|https?://", "network/URLs"), (r"\buseT\(", "useT()")]


def check_tsx(src: str) -> list[str]:
    """Static checks of contract §3 rules that tsc does not catch. [] = fine."""
    bad = []
    for name in ("DEFAULTS", "schema", "Scene"):
        if not re.search(rf"export\s+const\s+{name}\b", src):
            bad.append(f"missing `export const {name}`")
    for mod in re.findall(r"""(?:from|import)\s+['"]([^'"]+)['"]""", src):
        if mod in ("react", "remotion", "../types"):
            continue
        m = re.fullmatch(r"\.\./lib/(\w+)", mod)
        if not m or m.group(1) not in LIB_MODULES:
            bad.append(f"import of {mod!r} is not allowed (only react, remotion, ../types, "
                       f"../lib/{{{','.join(sorted(LIB_MODULES - {'TitleCard'}))}}})")
    code = re.sub(r"//[^\n]*", "", src)
    for pat, what in _FORBIDDEN:
        if re.search(pat, code):
            bad.append(f"uses {what} (forbidden: the scene must be a pure function of the frame)")
    return bad


def scene_module(s: dict, idx: int, total: int, frames: int, w: int, h: int, card, look: dict,
                 assets: list, context: str = "", error: str = "", previous: str = "") -> str:
    user = scene_prompt(s, idx, total, frames, w, h, card, look, assets, context, error, previous)
    return extract_tsx(llm.ask(SCENE_SYSTEM, user, max_tokens=16000))
