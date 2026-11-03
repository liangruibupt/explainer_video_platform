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
Rules: 5-8 scenes; first scene is a title card, last is a short recap.
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


def storyboard(topic: str, language: str, seconds: int, brief: str = "",
               illustrations: int = 0) -> list:
    user = (f"Topic: {topic}\nNarration language: {language}\n"
            f"Target total length: about {seconds} seconds.")
    if brief:
        user += ("\n\nBrief from the user (follow it; it overrides the default scene count "
                 "and must be used for facts):\n" + brief)
    if illustrations:
        user += ILLUSTRATION_NOTE.format(n=illustrations)
    text = ask(STORYBOARD_SYSTEM, user, max_tokens=4000)
    scenes = json.loads(_extract(text, "["))
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
    if illustrations:  # never more than asked for, first ones win
        seen = 0
        for s in out:
            if "illustration" in s:
                seen += 1
                if seen > illustrations:
                    del s["illustration"]
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
  (T.bg_img, T.img[...]).
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
  ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = 1;
  paintBg();
  ctx.save();
  try { SCENES[i].draw(ctx, f, n, W, H, T); }
  catch (e) { ctx.restore(); ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = 1;
              paintBg(); fallback(ctx, f, n, W, H, T, SCENES[i].title); }
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
               context: str = "", bg_data_url: str = "", images: dict | None = None) -> str:
    """One Opus call per scene, run in parallel; each returns a draw() function.
    context: extra facts every scene needs (e.g. pixel coordinates of map places).
    bg_data_url: optional data: URL of a W x H background painted under every frame.
    images: optional {scene_id: data URL} of AI illustrations, exposed as T.img[id]."""
    from concurrent.futures import ThreadPoolExecutor
    images = images or {}
    ctx = "\n\n".join(x for x in ((BG_NOTE if bg_data_url else ""), context) if x)

    def one(i):
        s = scenes[i]
        brief = {k: s[k] for k in ("id", "title", "narration", "visual")}
        sctx = ctx
        if s["id"] in images:
            note = IMG_NOTE.format(sid=s["id"], place=s.get("illustration", {}).get("place",
                                                                                  "background"))
            sctx = "\n\n".join(x for x in (ctx, note) if x)
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
                .replace("__DUR__", json.dumps(frames)).replace("__SCENES__", ",\n".join(blocks)))


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
