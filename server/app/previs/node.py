"""The previs.render workflow node (docs/PREVIS_CONTRACT.md section 11, part B).

Inputs: plan (PrevisPlan, json), job (json: {"dir": "<job dir>"} or {"id": "<job id>"}; the job
dir holds words.json, durations.json, scenes.json and, for the film's sound, narration_bgm.wav).
Writes the plan to <job>/previs/plan.json, compiles and checks every shot, writes the Remotion
project, renders each shot to shots/<id>.mp4 and the whole film (Main) to final.mp4.
Outputs: video (the film, copied into ctx.dir) and shots (json: per scene clip, seconds, report).

Declared in the shape of app.workflows.nodes; integration calls register(NODES).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .. import pipeline
from . import shots

TYPE = "previs.render"


def _job_dir(job) -> Path:
    if isinstance(job, str):
        job = {"dir": job} if "/" in job else {"id": job}
    if not isinstance(job, dict):
        raise ValueError("job 输入要是 {dir} 或 {id}")
    if job.get("dir"):
        return Path(job["dir"])
    if job.get("id"):
        return pipeline.job_dir(str(job["id"]))
    raise ValueError("job 输入缺少 dir 或 id")


def _run(inputs, p, ctx):
    d = _job_dir(inputs.get("job"))
    plan = inputs.get("plan")
    if not isinstance(plan, dict) or not plan.get("shots"):
        raise ValueError("plan 输入不是有效的 PrevisPlan")
    for f in ("words.json", "durations.json"):
        if not (d / f).exists():
            raise ValueError(f"任务目录缺少 {f}（先跑配音和字幕）")
    (d / "previs").mkdir(parents=True, exist_ok=True)
    (d / "previs" / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1))
    res = shots.compile_all(d, size=p.get("size") or None)
    bad = {k: v for k, v in res.items() if v["errors"]}
    if bad:
        first = next(i for v in bad.values() for i in v["report"] if i["severity"] == "error")
        raise RuntimeError(f"{len(bad)} 个镜头检查不通过（{', '.join(bad)}）：{first['message']}")
    if p.get("shadows") is False:
        for sid in res:
            f = d / "previs" / f"{sid}.json"
            j = json.loads(f.read_text())
            j["style"]["shadows"] = False
            f.write_text(json.dumps(j, ensure_ascii=False, separators=(",", ":")))
    log_lines = []
    proj = shots.write_project(d, log=log_lines.append)
    try:  # captions + voice + sfx data, the same step as remotion jobs
        from .. import remotion
        job = json.loads((d / "job.json").read_text()) if (d / "job.json").exists() else {"scenes": json.loads((d / "scenes.json").read_text())}
        remotion.prepare_media(job, d, proj, log_lines.append)
    except Exception as e:  # noqa: BLE001 - a silent film still renders
        log_lines.append(f"previs: media step skipped: {e}")
    out = {}
    for sid, r in res.items():
        x = shots.render_shot(d, sid)
        out[sid] = {"clip": x["clip"], "seconds": x["seconds"], "stills": x["stills"],
                    "report": r["report"], "plan_png": r["plan_png"]}
    film = shots.render_main(d)
    dst = Path(ctx.dir) / "final.mp4"
    shutil.copyfile(film["video"], dst)
    ctx.meta["previs"] = {"shots": {k: v["seconds"] for k, v in out.items()}, "film_seconds": film["seconds"],
                          "log": log_lines[-20:]}
    return {"video": str(dst), "shots": out}


DECLARATION = {
    "type": TYPE, "label": "3D 白模预演渲染", "group": "视频", "runner": "cpu",
    "inputs": {"plan": "json", "job": "json"}, "optional": [],
    "outputs": {"video": "video", "shots": "json"},
    "params": {"size": {"type": "string", "default": "", "label": "尺寸（如 1920x1080，空 = 按任务比例）"},
               "shadows": {"type": "boolean", "default": True, "label": "阴影"}},
    "passthrough": {}, "cache": True, "version": 1,
    "doc": "按预演规划编译镜头（锚点换算 + 逐帧检查），每个镜头渲染成独立片段，再出整片。",
    "run": _run,
}


def register(nodes: dict) -> dict:
    """Add previs.render to a node table (the real NODES at integration, a copy in tests)."""
    nodes[TYPE] = dict(DECLARATION)
    return nodes[TYPE]
