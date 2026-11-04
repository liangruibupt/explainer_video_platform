"""The input.asset workflow node: feeds a library asset into a workflow.

Declared in the same shape as app.workflows.nodes (see node()). It is not registered at
import time; integration calls register(NODES) so the node library stays in one place.

The output has one port per file kind (image / audio / video); only the port matching the
asset's kind gets a value, the asset's file path, so the validator can type-check edges
from the port the user wires. Running a node whose wired port does not match the asset's
kind fails with a clear message.
"""
from . import store

TYPE = "input.asset"


def _wired_ports(ctx) -> set:
    """Output ports of this node that the running workflow connects (empty if unknown)."""
    try:
        from ..workflows import engine
        wf = engine.read_run(ctx.run_id).get("workflow") or {}
    except Exception:  # noqa: BLE001 - outside a real run (tests, previews)
        return set()
    out = set()
    for e in wf.get("edges") or []:
        src = str(e.get("from", ""))
        nid, _, port = src.rpartition(".")
        if nid == ctx.node_id:
            out.add(port)
    return out


def _run(inputs, p, ctx):
    asset_id = (p.get("asset_id") or "").strip()
    if not store.valid_id(asset_id):
        raise ValueError(f"asset_id 格式不对：{asset_id!r}")
    meta = store.get(asset_id)
    if not meta:
        raise ValueError(f"素材不存在：{asset_id}")
    path = store.asset_path(asset_id)
    wired = _wired_ports(ctx)
    wrong = sorted(w for w in wired if w != meta["kind"])
    if wrong:
        raise ValueError(f"素材 {asset_id} 是 {meta['kind']}，但连出的是 {', '.join(wrong)} 端口")
    ctx.meta["asset"] = {"id": asset_id, "kind": meta["kind"], "name": meta.get("name"),
                         "sha256": meta.get("sha256")}
    # the file stays in data/assets/; the engine copies cached outputs into wf_cache
    return {meta["kind"]: str(path)}


DECLARATION = {
    "type": TYPE, "label": "素材输入", "group": "输入", "runner": "cpu",
    "inputs": {}, "optional": [],
    "outputs": {"image": "image", "audio": "audio", "video": "video"},
    "params": {"asset_id": {"type": "string", "required": True, "label": "素材 id"}},
    "passthrough": {}, "cache": False, "version": 1,
    "doc": "从素材库取一个文件（图片 / 音频 / 视频）。连出与素材类型相同的端口。",
    "run": _run,
}


def register(nodes: dict) -> dict:
    """Add input.asset to a node table (the real NODES at integration, a copy in tests)."""
    nodes[TYPE] = dict(DECLARATION)
    return nodes[TYPE]
