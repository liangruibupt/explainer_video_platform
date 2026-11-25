"""The agent.previs_plan workflow node (contract section 11): scenes: json -> plan: json.

Declared like app/assets/node.py and not registered at import time: integration calls
register(NODES).

Input "scenes" accepts a storyboard list [{id, narration, ...}], {"scenes": [...], "topic"},
or an explainer job reference {"job_id": ...} (reads data/jobs/<id>/scenes.json and writes the
plan to data/jobs/<id>/previs/plan.json as well). Library model assets are offered to the
planner when the asset store has any.
"""
import json
from pathlib import Path

from . import plan

TYPE = "agent.previs_plan"


def _library() -> list:
    try:
        from ..assets import store
        return [store.get(m["id"]) or m for m in store.list_all(kind="model")]
    except Exception:  # noqa: BLE001 - store without the "model" kind yet (part A)
        return []


def _scenes(value) -> tuple:
    """(scenes, topic, job_dir or None) from the node input."""
    if isinstance(value, list):
        return value, "", None
    if isinstance(value, dict):
        if isinstance(value.get("scenes"), list):
            return value["scenes"], str(value.get("topic") or ""), None
        if value.get("job_id"):
            from .. import pipeline
            d = pipeline.job_dir(str(value["job_id"]))
            scenes = json.loads((d / "scenes.json").read_text(encoding="utf-8"))
            topic = str(pipeline.read_job(str(value["job_id"])).get("topic") or "")
            return scenes, topic, d
    raise ValueError("scenes 输入应为分镜列表、{scenes: [...]} 或 {job_id}")


def _run(inputs, p, ctx):
    scenes, topic, job_dir = _scenes(inputs["scenes"])
    topic = p.get("topic") or topic
    meta = {}
    result = plan.previs_plan(scenes, topic, p.get("ratio", "16:9"), _library(), meta=meta)
    ctx.meta["rounds"] = meta.get("rounds")
    ctx.meta["warnings"] = [i for i in meta.get("issues", []) if i.get("severity") == "warn"]
    text = json.dumps(result, ensure_ascii=False, indent=1)
    out = Path(ctx.dir) / "plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    if job_dir is not None:
        (job_dir / "previs").mkdir(parents=True, exist_ok=True)
        (job_dir / "previs" / "plan.json").write_text(text, encoding="utf-8")
    return {"plan": result}


DECLARATION = {
    "type": TYPE, "label": "Agent 规划 3D 预演", "group": "AI Agent", "runner": "agent",
    "inputs": {"scenes": "json"}, "optional": [],
    "outputs": {"plan": "json"},
    "params": {"ratio": {"type": "string", "default": "16:9", "enum": ["16:9", "9:16"],
                         "label": "画面比例"},
               "topic": {"type": "string", "default": "", "label": "主题（可空，取分镜任务的主题）"}},
    "passthrough": {}, "cache": True, "version": 1,
    "doc": "Opus 按分镜写 3D 白模预演规划（资产、布景、每段一个镜头），静态校验不通过时带着错误重写，最多 3 轮。",
    "run": _run,
}


def register(nodes: dict) -> dict:
    """Add agent.previs_plan to a node table (the real NODES at integration, a copy in tests)."""
    nodes[TYPE] = dict(DECLARATION)
    return nodes[TYPE]
