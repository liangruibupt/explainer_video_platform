"""Opus 5.5 on Bedrock: writes the storyboard and the frame-drawing code."""
import json
import re

import boto3
from botocore.config import Config

MODEL_ID = "us.anthropic.claude-opus-5-5"
REGION = "us-east-1"
_client = None


def _bedrock():
    # Long generations (a whole HTML scene file) stream for several minutes.
    # Streaming keeps bytes flowing, and the read timeout only guards against a stall.
    global _client
    if _client is None:
        _client = boto3.client("bedrock-runtime", region_name=REGION, config=Config(
            read_timeout=300, connect_timeout=20, retries={"max_attempts": 2, "mode": "standard"}))
    return _client


def ask(system: str, user: str, max_tokens: int = 8000) -> str:
    r = _bedrock().converse_stream(
        modelId=MODEL_ID,
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": user}]}],
        inferenceConfig={"maxTokens": max_tokens},
    )
    parts, stop = [], None
    for ev in r["stream"]:
        if "contentBlockDelta" in ev:
            parts.append(ev["contentBlockDelta"]["delta"].get("text", ""))
        elif "messageStop" in ev:
            stop = ev["messageStop"].get("stopReason")
    text = "".join(parts)
    if stop == "max_tokens":
        raise ValueError(f"model output was cut off at {max_tokens} tokens")
    return text


STORYBOARD_SYSTEM = """You write storyboards for short narrated explainer videos: principle
explanations and slide-style walkthroughs, never drama or live-action scenes.
Return ONLY a JSON array, no prose, no code fence. Each item:
{"id": "snake_case_id", "title": "short on-screen heading",
 "narration": "one or two spoken sentences",
 "visual": "what is drawn: diagrams, labels, arrows, charts, step-by-step build-up",
 "sfx": [{"at": FRAME_IN_SCENE, "kind": "whoosh|pop|rise|thud|hiss"}]}
Rules: 5-8 scenes (5-6 when the target is 60 seconds or less); first scene is a title card,
last is a short recap.
Length budget: the video is exactly as long as its narration plus a short pause per scene,
so the narration must fit the target length. Total narration across all scenes must stay
within: Chinese about target_seconds x 3.8 characters (45 s ~ 170, 60 s ~ 230, 90 s ~ 340);
English about target_seconds x 2.2 words. Split this budget across the scenes; the title card
and the recap get the least. Before answering, count the total. If it is over budget, cut
words or merge scenes. Never exceed the budget; being 10% short is fine.
One idea per scene. Narration in the requested language; ids in English.
"sfx" is optional, at most 2 per scene, "at" between 10 and 60.
Check facts; do not invent numbers you are unsure of."""


ILLUSTRATION_NOTE = """

AI illustrations are available for up to {n} scenes. Add an "illustration" field only to
the scenes where a picture explains better than a diagram (a place, an object, a historical
scene, a mood), never to the title card's text or to scenes that are charts or flows:
"illustration": {{"prompt": "detailed image prompt in Chinese or English: subject, style,
composition, lighting; always end with 画面中没有任何文字", "place": "background|left|right"}}
"background" fills the whole frame behind the scene; "left"/"right" is a picture panel on
that half while the diagram and text use the other half."""

IMG_NOTE = """This scene has an AI illustration: T.img['{sid}'] (an HTMLImageElement, already
loaded). Placement: {place}.
- background: draw it with ctx.drawImage over the whole canvas first, then a soft dark or
  light veil (globalAlpha 0.25-0.45) where text sits, so text stays readable. Animate it gently
  (slow zoom from 1.0 to 1.06 across the scene, centred), never cropping more than 6%.
- left/right: draw it inside a rounded panel filling that half (keep the bottom 16% free),
  cover-fit and clipped to the panel, with the same slow zoom; put text and diagrams on the
  other half.
Never draw text on top of the picture without a backing panel."""

CLIP_SCENE_NOTE = """This scene has an AI video clip as its moving background: T.clipFrame (an
HTMLImageElement, already loaded, exactly W x H) is the clip's frame for the current f; the
page shell swaps it every frame. Draw it FIRST, every frame: ctx.drawImage(T.clipFrame, 0, 0,
W, H) (guard with if (T.clipFrame)). Do not zoom or crop it: the footage already moves. Then
draw a soft dark veil (a vertical gradient or rounded panel, rgba(0,0,0,0.35-0.55)) where the
text sits, so text stays readable over the footage, and put the title, labels and a light
diagram on top. Keep most of the footage visible; never cover the whole frame opaquely."""


CLIP_NOTE = """

AI video clips (5 s of real-looking footage, played as a moving background) are available for
up to {n} scenes. Add a "clip" field only to scenes where real footage helps (a place, a
machine, nature, a landscape), never to the title card, a chart, a flow or a diagram scene,
and never to a scene that already has an "illustration":
"clip": {{"prompt": "English description of one live-action shot: subject, setting, camera
movement, light, time of day; no text, no people talking", "place": "background"}}"""

MUSIC_NOTE = """

The video also gets AI background music. Return an object instead of a bare array:
{"scenes": [...the scenes as above...], "music_style": "..."}
"music_style" is ONE line of English style text for a calm instrumental bed that fits the
topic (genre, instruments, tempo, mood), and it must end with exactly
"ambient, no drums, instrumental, no vocals"."""

MUSIC_TAIL = "ambient, no drums, instrumental, no vocals"


def parse_storyboard(text: str) -> tuple:
    """(scenes list, music_style str). Accepts {"scenes": [...], "music_style": ...} or a bare
    JSON array (the older format)."""
    t = re.sub(r"^```[a-zA-Z]*\n", "", text.strip())
    t = re.sub(r"\n```\s*$", "", t)
    i, j = t.find("{"), t.find("[")
    if i >= 0 and (j < 0 or i < j):
        obj = json.loads(t[i: t.rfind("}") + 1])
        return obj.get("scenes") or [], str(obj.get("music_style") or "")
    return json.loads(_extract(text, "[")), ""


def _music_style(style: str) -> str:
    style = re.sub(r"\s+", " ", style).strip().rstrip(".")[:400]
    if not style:
        return ""
    if not style.lower().endswith(MUSIC_TAIL):
        style = f"{style}, {MUSIC_TAIL}"
    return style


def storyboard(topic: str, language: str, seconds: int, brief: str = "",
               illustrations: int = 0, clips: int = 0, music_ai: bool = False,
               meta: dict | None = None) -> list:
    """Returns the scenes. meta (a dict), when given, receives "music_style"."""
    user = (f"Topic: {topic}\nNarration language: {language}\n"
            f"Target total length: about {seconds} seconds.")
    if brief:
        user += ("\n\nBrief from the user (follow it; it overrides the default scene count "
                 "and must be used for facts):\n" + brief)
    if illustrations:
        user += ILLUSTRATION_NOTE.format(n=illustrations)
    if clips:
        user += CLIP_NOTE.format(n=clips)
    if music_ai:
        user += MUSIC_NOTE
    text = ask(STORYBOARD_SYSTEM, user, max_tokens=4000)
    scenes, style = parse_storyboard(text)
    if meta is not None:
        meta["music_style"] = _music_style(style)
    out = []
    for i, s in enumerate(scenes):
        if not s.get("narration"):
            continue
        sid = re.sub(r"[^a-z0-9_]", "_", str(s.get("id") or f"scene_{i}").lower())[:40]
        out.append({"id": sid or f"scene_{i}", "title": str(s.get("title", ""))[:80],
                    "narration": str(s["narration"]), "visual": str(s.get("visual", "")),
                    "sfx": [e for e in s.get("sfx", []) if isinstance(e, dict)
                            and e.get("kind") in ("whoosh", "pop", "rise", "thud", "hiss")][:2]})
        ill = s.get("illustration")
        if illustrations and isinstance(ill, dict) and str(ill.get("prompt", "")).strip():
            out[-1]["illustration"] = {
                "prompt": str(ill["prompt"])[:600],
                "place": ill.get("place") if ill.get("place") in ("background", "left", "right")
                else "background"}
        clip = s.get("clip")
        if (clips and isinstance(clip, dict) and str(clip.get("prompt", "")).strip()
                and "illustration" not in out[-1] and i > 0):
            out[-1]["clip"] = {"prompt": str(clip["prompt"])[:600], "place": "background"}
    if illustrations:  # never more than asked for, first ones win
        seen = 0
        for s in out:
            if "illustration" in s:
                seen += 1
                if seen > illustrations:
                    del s["illustration"]
    if clips:
        seen = 0
        for s in out:
            if "clip" in s:
                seen += 1
                if seen > clips:
                    del s["clip"]
    if not out:
        raise ValueError("storyboard came back empty")
    return out


SCENE_SYSTEM = """You write ONE JavaScript function that draws one scene of a narrated
explainer video on a Canvas 2D context, frame by frame. A headless browser calls it for
every frame of the scene and screenshots the result.
Signature (exactly this name and parameters):
  function draw(ctx, f, n, W, H, T) { ... }
  ctx: CanvasRenderingContext2D, already cleared to the background colour
  f: frame index inside this scene (0 .. n-1); n: frames in this scene; W, H: canvas size
  T: theme = {bg, ink, dim, accent, accent2, font}  (use these colours and T.font)
Hard requirements:
- Draw everything from f alone: no requestAnimationFrame, timers, Date, performance,
  Math.random, globals, closures over mutable state, DOM access or network. Do not create
  or load images; you may only draw the already-loaded images this prompt names
  (T.bg_img, T.img[...], T.clipFrame).
- p = f / n is scene progress. Build the diagram up step by step in sync with the narration;
  something must visibly move on every part of the timeline (no static frames).
- Keep the bottom 16% of the canvas free of text and key drawing: captions go there.
- Text timing: every piece of text must be fully visible by p = 0.6 and stay fully visible
  until the scene ends. A typewriter or fade reveal must finish before p = 0.6.
- Text fit: size every panel from ctx.measureText of its longest line plus padding. Never
  clip text or let it run past its panel; if a line is too wide, wrap it or shrink the font
  (but not below the label minimum).
- Large readable type: titles >= H*0.055, labels >= H*0.03. Use ctx.font with T.font.
- Clean slide/keynote style; one accent colour; diagrams, arrows, charts, labels.
- Define helper functions INSIDE draw if you need them. Keep the code compact
  (aim for under 200 lines): reuse small helpers instead of repeating drawing code.
Return ONLY the function source, starting with "function draw(", no prose, no code fence."""

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><style>html,body{margin:0;background:#141210;overflow:hidden}
canvas{display:block}</style></head><body><canvas id="c" width="__W__" height="__H__"></canvas>
<script>
const DUR = __DUR__; const FPS = 30; const W = __W__, H = __H__;
const T = {bg:'#141210', ink:'#f4ede4', dim:'#9a8f85', accent:'#e0603f', accent2:'#7fb0c8',
  font:'"Noto Sans CJK SC","PingFang SC",sans-serif'};
const ctx = document.getElementById('c').getContext('2d');
const BG = __BG__;
const IMGS = __IMGS__;
const loadImg = (src) => new Promise((ok) => { const im = new Image();
  im.onload = () => ok(im); im.onerror = () => ok(null); im.src = src; });
T.img = {};
const bgReady = Promise.all([
  BG ? loadImg(BG).then((im) => { if (im) T.bg_img = im; }) : null,
  ...Object.entries(IMGS).map(([k, src]) => loadImg(src).then((im) => { if (im) T.img[k] = im; })),
]);
const SCENES = [
__SCENES__
];
// AI video clips: per scene, n JPEG frames at 30 fps in clip_<id>/00001.jpg ... next to
// this page. seek() loads the frame it needs and exposes it as T.clipFrame before draw().
const CLIPS = __CLIPS__;
T.clip = {};
for (const [k, n] of Object.entries(CLIPS)) {
  T.clip[k] = { n, frame: (i) => loadImg(`clip_${k}/${String(Math.min(Math.max(i, 0), n - 1) + 1).padStart(5, '0')}.jpg`) };
}
function paintBg() {
  ctx.fillStyle = T.bg; ctx.fillRect(0, 0, W, H);
  if (T.bg_img) ctx.drawImage(T.bg_img, 0, 0, W, H);
}
function fallback(ctx, f, n, W, H, T, title) {
  ctx.fillStyle = T.bg_img ? '#3a2a1c' : T.ink; ctx.font = `600 ${Math.round(H*0.06)}px ${T.font}`;
  ctx.textAlign = 'center'; ctx.globalAlpha = Math.min(1, f / 15);
  ctx.fillText(title, W/2, H*0.42); ctx.globalAlpha = 1; ctx.textAlign = 'left';
}
window.seek = async (frame) => {
  await bgReady;
  let start = 0, i = 0;
  while (i < DUR.length - 1 && frame >= start + DUR[i]) { start += DUR[i]; i++; }
  const f = Math.min(frame - start, DUR[i] - 1), n = DUR[i];
  const cl = T.clip[SCENES[i].id];
  T.clipFrame = cl ? await cl.frame(f) : null;
  ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = 1;
  paintBg();
  if (T.clipFrame) ctx.drawImage(T.clipFrame, 0, 0, W, H);
  ctx.save();
  try { SCENES[i].draw(ctx, f, n, W, H, T); }
  catch (e) { ctx.restore(); ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = 1;
              paintBg(); if (T.clipFrame) ctx.drawImage(T.clipFrame, 0, 0, W, H);
              fallback(ctx, f, n, W, H, T, SCENES[i].title); }
  ctx.restore();
};
</script></body></html>
"""


def scene_draw(scene: dict, idx: int, total: int, frames: int, width: int, height: int,
               context: str = "") -> str:
    user = (f"Canvas W={width} H={height}. This is scene {idx + 1} of {total}; it lasts "
            f"{frames} frames at 30 fps ({frames / 30:.1f} s).\n"
            f"Scene:\n{json.dumps(scene, ensure_ascii=False, indent=1)}")
    if context:
        user += "\n\n" + context
    src = _extract(ask(SCENE_SYSTEM, user, max_tokens=12000), "function draw(")
    src = src[: src.rfind("}") + 1]
    if "function draw(" not in src:
        raise ValueError(f"scene {scene['id']}: no draw function")
    return src


BG_NOTE = """A background image is available as T.bg_img (an HTMLImageElement, already loaded,
exactly W x H). The page shell paints it before calling draw(), so every frame already shows it.
Do NOT clear the canvas or fill the whole frame with an opaque colour: the background would vanish.
For text, draw a semi-transparent rounded panel only behind the text.
The background is light paper: use dark ink for text, e.g. '#3a2a1c', and T.accent for highlights."""


def scene_html(scenes: list, frames: list, width: int, height: int, log=None,
               context: str = "", bg_data_url: str = "", images: dict | None = None,
               clips: dict | None = None) -> str:
    """One Opus call per scene, run in parallel; each returns a draw() function.
    context: extra facts every scene needs (e.g. pixel coordinates of map places).
    bg_data_url: optional data: URL of a W x H background painted under every frame.
    images: optional {scene_id: data URL} of AI illustrations, exposed as T.img[id].
    clips: optional {scene_id: frame count} of AI video clips whose JPEG frames sit in
    clip_<id>/ next to index.html; the shell exposes the current one as T.clipFrame."""
    from concurrent.futures import ThreadPoolExecutor
    images = images or {}
    clips = clips or {}
    ctx = "\n\n".join(x for x in ((BG_NOTE if bg_data_url else ""), context) if x)

    def one(i):
        s = scenes[i]
        brief = {k: s[k] for k in ("id", "title", "narration", "visual")}
        sctx = ctx
        if s["id"] in images:
            note = IMG_NOTE.format(sid=s["id"], place=s.get("illustration", {}).get("place",
                                                                                  "background"))
            sctx = "\n\n".join(x for x in (ctx, note) if x)
        if s["id"] in clips:
            sctx = "\n\n".join(x for x in (sctx, CLIP_SCENE_NOTE) if x)
        try:
            return scene_draw(brief, i, len(scenes), frames[i], width, height, sctx), None
        except Exception as e:  # noqa: BLE001 - fall back to a title card for this scene
            return None, f"{s['id']}: {e}"

    with ThreadPoolExecutor(max_workers=min(4, len(scenes))) as ex:
        results = list(ex.map(one, range(len(scenes))))
    failed = [err for _, err in results if err]
    if log:
        for err in failed:
            log(f"scene code fallback: {err}")
    if len(failed) == len(scenes):
        raise ValueError("every scene failed: " + "; ".join(failed))
    blocks = []
    for s, (src, _) in zip(scenes, results):
        title = json.dumps(s["title"] or s["id"], ensure_ascii=False)
        body = (f"(() => {{ {src}\n return draw; }})()" if src
                else f"(ctx, f, n, W, H, T) => fallback(ctx, f, n, W, H, T, {title})")
        blocks.append(f"{{ id: {json.dumps(s['id'])}, title: {title}, draw: {body} }}")
    return (PAGE.replace("__W__", str(width)).replace("__H__", str(height))
                .replace("__BG__", json.dumps(bg_data_url))
                .replace("__IMGS__", json.dumps(images))
                .replace("__CLIPS__", json.dumps(clips))
                .replace("__DUR__", json.dumps(frames)).replace("__SCENES__", ",\n".join(blocks)))


ARRANGE_SYSTEM = """You turn a short arrangement note for an instrumental music piece into style
tags for the YuE2 music model. Input may be in any language. Output ONE line of concise English
tags separated by commas: instruments, how the piece develops section by section (e.g. "solo
guqin intro, xiao flute joins, soft strings in the middle, slow fade out"), tempo, mood. At most
40 words. No sentences, no quotes, no lyrics, no singing or vocal words. Output only the line."""


def arrangement_tags(note: str) -> str:
    """English style tags for an arrangement note; never empty on success."""
    line = ask(ARRANGE_SYSTEM, note.strip(), max_tokens=200).strip().splitlines()[0]
    line = re.sub(r"\b(vocals?|singing|sung|lyrics?|choir|voice)\b", "", line, flags=re.I)
    line = re.sub(r"\s*,\s*(,\s*)+", ", ", line).strip(" ,.\"'")
    if not line:
        raise ValueError("empty arrangement tags")
    return line


def _extract(text: str, start: str, lower: bool = False) -> str:
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\n", "", t)
    t = re.sub(r"\n```\s*$", "", t)
    i = (t.lower() if lower else t).find(start.lower() if lower else start)
    if i < 0:
        raise ValueError(f"model output has no {start!r}")
    t = t[i:]
    if start == "[":
        j = t.rfind("]")
        t = t[: j + 1]
    return t
