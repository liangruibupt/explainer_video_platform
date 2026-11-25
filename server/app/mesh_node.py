"""Workflow node asset.image3d (docs/PREVIS_CONTRACT.md section 11): prompt or image -> a
library model (GLB at real size). Same steps as POST /api/assets/model/image3d
(mesh_api.run_image3d): concept image on gpu:image when no image is wired, Hunyuan3D-2 on
gpu:mesh, then part A's normalize. Not registered at import time: integration calls
register(NODES); tests register into a copy.
"""
from pathlib import Path

from . import mesh, mesh_api

TYPE = "asset.image3d"


def _run(inputs, p, ctx):
    prompt = (inputs.get("prompt") or p.get("prompt") or "").strip()
    img = Path(inputs["image"]).read_bytes() if inputs.get("image") else None
    if not prompt and img is None:
        raise ValueError("asset.image3d: 需要提示词（prompt）或一张图片（image）")
    try:
        target = mesh_api.check_target({"axis": p["target_axis"], "metres": p["target_metres"]})
        opts = mesh.check_options(p.get("model") or "", p.get("seed", 42))
        png = mesh.prepare_image(img) if img is not None else None
    except mesh.MeshError as e:
        raise ValueError(f"asset.image3d: {e}") from None
    req = {"prompt": prompt, "image_asset": None, "target": target, "model": opts["model"],
           "texture": bool(p.get("texture")), "seed": opts["seed"],
           "name": (p.get("name") or prompt or "图生 3D")[:60], "project_id": p.get("project_id")}
    if png is not None:  # a wired image wins over the prompt (no concept image)
        req["prompt"] = ""
    out = mesh_api.run_image3d(req, ctx.dir, png=png, note=getattr(ctx, "note", None),
                               on_wait=ctx.on_wait)
    rec = out["asset"] or {}
    ctx.meta.update(model=opts["model"], texture=req["texture"], target=target,
                    mesh=out["mesh_meta"], asset_id=rec.get("id"))
    return {"model": rec.get("id")}


DECLARATION = {
    "type": TYPE, "label": "图生 3D", "group": "资产", "runner": "gpu:mesh",
    "inputs": {"prompt": "text", "image": "image"}, "optional": ["prompt", "image"],
    "outputs": {"model": "model"},
    "params": {
        "prompt": {"type": "string", "default": "", "label": "提示词（未连线时用）"},
        "target_axis": {"type": "string", "default": "longest", "enum": list(mesh_api.AXES),
                        "label": "按哪条边定尺寸（w 宽 / h 高 / d 深 / longest 最长边）"},
        "target_metres": {"type": "number", "default": 0.3, "min": 0.001, "max": 1000,
                          "label": "这条边的长度（米）"},
        "model": {"type": "string", "default": "", "enum": ["", *mesh.MODELS],
                  "label": "模型（空 = 默认）"},
        "texture": {"type": "boolean", "default": False, "label": "贴图（Paint，慢几分钟）"},
        "seed": {"type": "integer", "default": 42, "min": 0, "max": 2**31 - 1},
    },
    "passthrough": {}, "cache": True, "version": 1,
    "doc": "一个物体的提示词或图片 → 带真实尺寸的 3D 模型（GLB）入素材库。没连图片时先用图像模块按"
           "「单个物体，纯白背景，三分之二侧视角」出概念图，再用 Hunyuan3D-2 生成网格（默认只出几何），"
           "最后按目标尺寸缩放、落地。输出素材库 id。",
    "run": _run,
}


def register(nodes: dict) -> dict:
    nodes[TYPE] = dict(DECLARATION, params={k: dict(v) for k, v in DECLARATION["params"].items()})
    return nodes[TYPE]
