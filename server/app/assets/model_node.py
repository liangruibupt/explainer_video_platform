"""The asset.build workflow node: parametric AssetSpec (json) -> model asset id
(docs/PREVIS_CONTRACT.md section 11). Declared like assets/node.py; integration calls
register(NODES). Port type "model" (value = library asset id) is added to validate.py by
integration.
"""
from ..previs import assets_build

TYPE = "asset.build"


def _run(inputs, p, ctx):
    spec = inputs.get("spec")
    # accept a PrevisPlan asset entry {id, kind: "parametric", spec} as well as a bare spec
    if isinstance(spec, dict) and "parts" not in spec and isinstance(spec.get("spec"), dict):
        spec = spec["spec"]
    if not isinstance(spec, dict):
        raise ValueError("spec 输入必须是 AssetSpec JSON 对象")
    try:
        meta = assets_build.build(spec)
    except assets_build.BuildError as e:
        lines = "；".join(f"{i.get('code')}: {i.get('message')}" for i in e.issues[:10])
        ctx.meta["issues"] = e.issues
        raise ValueError(f"资产校验失败：{lines}") from None
    ctx.meta["asset"] = {"id": meta["id"], "kind": "model", "name": meta.get("name"),
                         "size": meta.get("size"), "tris": meta.get("tris")}
    return {"model": meta["id"]}


DECLARATION = {
    "type": TYPE, "label": "3D 资产构建", "group": "3D", "runner": "cpu",
    "inputs": {"spec": "json"}, "optional": [],
    "outputs": {"model": "model"},
    "params": {},
    "passthrough": {}, "cache": True, "version": 1,
    "doc": "把参数化资产规格（AssetSpec）构建成带真实尺寸的 GLB，校验后存进素材库，输出素材 id。",
    "run": _run,
}


def register(nodes: dict) -> dict:
    """Add asset.build to a node table (the real NODES at integration, a copy in tests)."""
    nodes[TYPE] = dict(DECLARATION)
    return nodes[TYPE]
