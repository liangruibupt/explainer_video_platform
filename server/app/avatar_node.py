"""Workflow nodes of the 数字人 module (docs/AVATAR_CONTRACT.md).

  tts.polly            text -> speech (Polly neural, narration path of the explainer jobs)
  avatar.infinitetalk  audio + portrait (or reference video) -> lip-synced presenter video

Declared in the shape of app.workflows.nodes; app/workflows/nodes.py calls register(NODES)
(guarded like the input.asset node), tests register into a copy.
"""
from pathlib import Path

from . import avatar

AVATAR = "avatar.infinitetalk"
TTS = "tts.polly"


def _tts(inputs, p, ctx):
    text = (inputs.get("text") or p.get("text") or "").strip()
    try:
        wav = avatar.speech(text, p["voice"])
    except avatar.AvatarError as e:
        raise ValueError(str(e)) from None
    out = ctx.dir / "speech.wav"
    out.write_bytes(wav)
    ctx.meta.update(voice=p["voice"], chars=len(text), seconds=avatar.duration(out))
    return {"audio": str(out)}


def _avatar(inputs, p, ctx):
    audio = Path(inputs["audio"]).read_bytes()
    img = Path(inputs["portrait"]).read_bytes() if inputs.get("portrait") else None
    vid = Path(inputs["ref_video"]).read_bytes() if inputs.get("ref_video") else None
    if img and vid:
        raise ValueError("avatar.infinitetalk: 人像图片和参考视频只连一个")
    if not img and not vid:
        raise ValueError("avatar.infinitetalk: 需要连一张人像图片（portrait）或参考视频（ref_video）")
    meta = {}
    try:
        seg_s = 0 if vid else avatar.check_segment_s(p.get("segment_s"))
        wav, _ = avatar.to_wav16k(audio)
        segments = avatar.split_audio(wav, seg_s)
        prefix = {"s": ""}

        def on_segment(k, n, cached):
            if n > 1:
                prefix["s"] = f"第 {k}/{n} 段 · "
                if hasattr(ctx, "note"):
                    ctx.note(f"第 {k}/{n} 段" + ("：复用已生成的段" if cached else "：开始生成"))

        def on_wait(sec):
            if prefix["s"] and hasattr(ctx, "note"):
                ctx.note(prefix["s"] + (f"等待 GPU（{sec // 60} 分钟）" if sec >= 60
                                        else "已提交，等待 GPU"))
            else:
                ctx.on_wait(sec)
        mp4 = avatar.generate_long(segments, ctx.dir, image=img, video=vid, prompt=p["prompt"],
                                   model=p["model"], size=p["size"], fast=p["fast"],
                                   seed=p["seed"], on_wait=on_wait, on_segment=on_segment,
                                   meta_out=meta)
    except avatar.AvatarError as e:
        raise ValueError(str(e)) from None
    out = ctx.dir / "video.mp4"
    out.write_bytes(mp4)
    ctx.meta.update(model=p["model"] or avatar.default_model(), worker=meta,
                    segments=len(segments), seconds=avatar.duration(out))
    return {"video": str(out)}


DECLARATIONS = {
    TTS: {
        "type": TTS, "label": "语音合成（Polly）", "group": "音频", "runner": "cpu",
        "inputs": {"text": "text"}, "optional": ["text"], "outputs": {"audio": "audio"},
        "params": {"text": {"type": "string", "default": "", "required_unless_input": "text",
                            "label": "文本（未连线时用）"},
                   "voice": {"type": "string", "default": "Zhiyu",
                             "enum": list(avatar.VOICES), "label": "声音"}},
        "passthrough": {}, "cache": True, "version": 1,
        "doc": f"Amazon Polly 神经语音，和讲解视频旁白同一套读音规则（多音字、英文、缩写）。"
               f"最多 {avatar.SCRIPT_MAX} 字，输出 wav。",
        "run": _tts,
    },
    AVATAR: {
        "type": AVATAR, "label": "数字人口播", "group": "视频", "runner": "gpu:avatar",
        "inputs": {"audio": "audio", "portrait": "image", "ref_video": "video"},
        "optional": ["portrait", "ref_video"], "outputs": {"video": "video"},
        "params": {"model": {"type": "string", "default": "", "enum": ["", *avatar.MODELS],
                             "label": "模型（空 = 默认推荐）"},
                   "prompt": {"type": "string", "default": "", "label": "场景描述（英文更稳）"},
                   "size": {"type": "string", "default": "480", "enum": list(avatar.SIZES)},
                   "fast": {"type": "boolean", "default": True, "label": "快速（少步数 LoRA）"},
                   "seed": {"type": "integer", "default": 42, "min": 0, "max": 2**31 - 1},
                   "segment_s": {"type": "integer", "default": avatar.SEGMENT_S, "min": 0,
                                 "max": avatar.SEGMENT_MAX_S,
                                 "label": "分段：每段最长秒数（0 = 不分段）"}},
        "passthrough": {}, "cache": True, "version": 2,
        "doc": "人像图片（或参考视频）+ 语音 → 口型、表情和上半身动作同步的口播视频，带原音轨。"
               f"语音超过 segment_s（默认 {avatar.SEGMENT_S} 秒）时在停顿处切段，每段都从原人像生成再拼接"
               "（身份不累积漂移，接缝处 0.3 秒画面淡入淡出，声音连续）；参考视频输入不分段。"
               "只用本人、已获授权或 AI 生成的人像。音频最长 10 分钟。",
        "run": _avatar,
    },
}


def register(nodes: dict) -> list:
    """Add tts.polly (unless a TTS node is already there) and avatar.infinitetalk."""
    added = []
    for t, d in DECLARATIONS.items():
        if t == TTS and t in nodes:
            continue
        nodes[t] = dict(d, params={k: dict(v) for k, v in d["params"].items()})
        added.append(t)
    return added
