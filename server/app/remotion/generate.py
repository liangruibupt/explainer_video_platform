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


# ---- layout review (vision) ---------------------------------------------------------------

LAYOUT_KINDS = ("clipped", "overlap", "empty", "covered_title", "too_small", "other")

LAYOUT_SYSTEM = """You are the layout checker of a narrated explainer video. You get several stills
of ONE scene in time order (the scene frame of each is given): the first one or two are taken
during the entrance, the others once the layout should have settled. You also get the scene's
narration, visual description and the real frame size (the stills are downscaled to a long
side of 960 px). Find CONCRETE, VISIBLE layout defects only.

Go through EVERY still and check each of these explicitly; checkers before you missed them:
1. Frame edges (kind clipped): does any text, label, panel, card or meaningful shape touch or
   cross the left, right, top or bottom edge of the frame, so part of it is cut off? This counts
   in every still, also while a camera moves, zooms or tilts the scene, and also for a panel
   whose border runs off the frame. Check the first and last characters of every text line near
   an edge. Also text cut by its own panel or container border.
2. Labels over content (kind overlap): any text drawn on top of data points, dots, molecules,
   lines, curves, arrows, bars or another label so that some glyphs are crossed or hidden;
   colliding axis tick labels; two tick sets or duplicate labels on one axis.
3. Covered heading (kind covered_title): any box, panel, card or shape over the scene title /
   heading, even partly, even while it is still moving in. This counts in the entrance stills.
4. Empty areas (kind empty): a panel, card or chart area that is mostly empty (a frame with
   nothing or almost nothing inside), or a third or more of the frame unused while the rest is
   crowded, in a SETTLED still. In an entrance still only report a panel that is drawn at full
   size but empty.
5. Text height (kind too_small): measure the glyph height of every text that must be read
   (labels, axis ticks, list lines, notes, annotations, legends). Too small = under 2.2% of
   frame height in landscape 16:9 (24 px of 1080; 12 px in a 960x540 still) or under 1.8% in
   portrait 9:16 (35 px of 1920; 17 px in a 540x960 still). Name the elements.
6. Caption band: the bottom 15% of the frame is reserved for burned-in captions. Subtitle text
   there is expected; any OTHER text or key visual inside that band is a defect (kind other,
   where = "bottom caption band").
7. other: any other clear readability defect (text over a busy background with no backing, text
   running off its panel, elements colliding).

Do NOT judge style, taste, colours, wording, motion design or what you would have drawn. In
entrance stills, an element that is half transparent, or that is visibly sliding in from
outside the frame, is not a defect by itself (but rule 3 still applies). A purely decorative
background shape (glow, texture, faint ring) that bleeds off an edge is fine. Report a defect
only when you can see it; when the stills look fine, say so.
Return ONLY JSON, no prose, no code fence:
{"ok": true|false, "issues": [{"kind": "clipped|overlap|empty|covered_title|too_small|other",
 "where": "which still (scene frame), which element and where (e.g. 'f173, left edge: the title
 什么是瑞利散射 loses its first character')",
 "fix": "a concrete layout change (move/shrink/wrap/reposition, with sizes relative to the frame)"}]}
"ok" is true exactly when "issues" is empty."""


def layout_user(s: dict, frames: int, w: int, h: int, at: list,
                previous: list | None = None) -> str:
    brief = {k: s.get(k) for k in ("id", "title", "narration", "visual")}
    n_early = sum(1 for f in at if f < frames * 0.3)
    text = (f"Frame {w}x{h} ({'portrait 9:16' if h > w else 'landscape 16:9'}). The scene lasts "
            f"{frames} frames; the stills are at scene frames {', '.join(map(str, at))} "
            f"(the first {n_early} during the entrance).\n"
            f"Scene:\n{json.dumps(brief, ensure_ascii=False, indent=1)}")
    if previous:
        text += ("\n\nThis is a REWRITE. The previous version had these defects; for each one, look "
                 "at the same place in every still and report it again if any trace remains, "
                 "then check everything else as usual:\n"
                 + "\n".join(f"- [{x['kind']}] {x['where']}" for x in previous))
    return text


def parse_review(text: str) -> dict:
    """Validate the reviewer's JSON -> {"ok", "issues"}; raises ValueError when unusable."""
    out = parse_json(text)
    issues = out.get("issues")
    if not isinstance(out.get("ok"), bool) or not isinstance(issues, list):
        raise ValueError("review JSON needs ok (bool) and issues (list)")
    clean = []
    for it in issues:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind", "other")).strip()
        clean.append({"kind": kind if kind in LAYOUT_KINDS else "other",
                      "where": str(it.get("where", ""))[:300],
                      "fix": str(it.get("fix", ""))[:400]})
    if not out["ok"] and not clean:
        raise ValueError("review says not ok but lists no issues")
    return {"ok": not clean, "issues": clean}


def review_layout(s: dict, frames: int, w: int, h: int, stills: list,
                  previous: list | None = None) -> dict:
    """stills = [(scene frame, image bytes)]. ONE vision call. previous = issues of the version
    before a rewrite (confirmation pass). Returns {"ok", "issues"} and, when the reply could not
    be parsed, "error" (a first review then counts as ok; a confirmation never counts as fixed)."""
    at = [f for f, _ in stills]
    text = llm.ask_images(LAYOUT_SYSTEM, layout_user(s, frames, w, h, at, previous),
                          [b for _, b in stills], max_tokens=2500)
    try:
        return parse_review(text)
    except Exception as e:  # noqa: BLE001 - an unreadable review never blocks the scene
        return {"ok": True, "issues": [], "error": f"unparsable review ({e}): {text[:300]}"}


def issues_text(issues: list, at: list) -> str:
    """The layout issue list as the error text of a regeneration (scene_prompt's error path)."""
    lines = [f"Layout review of stills at scene frames {', '.join(map(str, at))} found these "
             "visible layout defects. Fix every one (move, resize, wrap or reposition elements; "
             "keep the motion, the content and everything that already looks right):"]
    lines += [f"- [{i['kind']}] {i['where']} -> fix: {i['fix']}" for i in issues]
    return "\n".join(lines)
