"""Workflow engine (DESIGN.md section 5, 执行).

A run lives in data/wf_runs/<run_id>/run.json:
  {id, name, workflow, status: running|waiting_review|done|failed, created, updated,
   nodes: {nid: {status: pending|running|waiting_review|done|failed|cached, started,
                 ended, seconds, outputs, error, note, cache_key, meta}},
   works: [...], events: [...]}

Scheduling: a driver thread repeatedly starts every pending node whose upstream nodes are
all done/cached; each node runs in its own thread, so independent nodes run in parallel.
GPU nodes take a per-module lock: different modules run at the same time, the same module
is queued (the worker runs one request at a time anyway). The driver can be restarted at
any time from run.json (after a review decision, a retry or a server restart).

Cache: key = sha256 of {node type, node version, effective params, input hashes} where a
file input hashes its bytes and any other value its canonical JSON. Results are stored in
data/wf_cache/<key>/ (outputs.json + files); a hit marks the node "cached" and costs no
model or GPU call. input.text, review.human and output.save are not cached: they are
cheap, and a review must be asked again on every run.
"""
import hashlib
import json
import os
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from ..pipeline import DATA
from . import candidates as cand
from .nodes import FILE_TYPES, NODES, effective_params
from .validate import parse_port, topo_order, validate

RUNS = DATA / "wf_runs"
CACHE = DATA / "wf_cache"
FINAL = ("done", "cached")
_gpu_locks = {}
_gpu_guard = threading.Lock()
_run_locks = {}
_drivers = {}
_guard = threading.Lock()


def _gpu_lock(module):
    with _gpu_guard:
        return _gpu_locks.setdefault(module, threading.Lock())


def _lock(run_id):
    with _guard:
        return _run_locks.setdefault(run_id, threading.RLock())


# ---------------------------------------------------------------- hashing / cache

def file_hash(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def value_hash(value, port_type: str) -> str:
    if port_type in FILE_TYPES or (port_type == "any" and isinstance(value, str)
                                   and value.startswith("/") and Path(value).is_file()):
        return "file:" + file_hash(value)
    return "json:" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False)
                                    .encode()).hexdigest()


def cache_key(node_type: str, params: dict, input_hashes: dict) -> str:
    """sha256 of type + version + effective params + {port: input hash}."""
    d = NODES[node_type]
    eff = effective_params(node_type, params)
    eff.pop("candidates", None)  # each candidate is keyed by its own seed instead
    blob = json.dumps({"type": node_type, "version": d["version"],
                       "params": eff,
                       "inputs": dict(sorted(input_hashes.items()))},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def cache_get(key: str):
    p = CACHE / key / "outputs.json"
    if not p.exists():
        return None
    j = json.loads(p.read_text())
    for v in j["outputs"].values():  # a file vanished: treat as a miss
        if isinstance(v, str) and v.startswith(str(CACHE)) and not Path(v).exists():
            return None
    return j


def cache_put(key: str, node_type: str, outputs: dict, meta: dict) -> dict:
    """Copy output files into data/wf_cache/<key>/ and return outputs pointing there."""
    d = CACHE / key
    tmp = CACHE / f".{key}.{uuid.uuid4().hex[:6]}"
    tmp.mkdir(parents=True, exist_ok=True)
    out = {}
    types = NODES[node_type]["outputs"]
    for port, v in outputs.items():
        if types.get(port) in FILE_TYPES + ("any",) and isinstance(v, str) \
                and Path(v).is_file():
            dst = tmp / f"{port}{Path(v).suffix}"
            shutil.copyfile(v, dst)
            out[port] = str(d / dst.name)
        else:
            out[port] = v
    (tmp / "outputs.json").write_text(json.dumps(
        {"type": node_type, "outputs": out, "meta": meta, "created": time.time()},
        ensure_ascii=False, indent=1))
    if d.exists():
        shutil.rmtree(tmp)
    else:
        tmp.rename(d)
    return out


# ---------------------------------------------------------------- run state

def run_dir(run_id: str) -> Path:
    return RUNS / run_id


def read_run(run_id: str) -> dict:
    p = run_dir(run_id) / "run.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _write(run: dict) -> None:
    run["updated"] = time.time()
    p = run_dir(run["id"]) / "run.json"
    tmp = p.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(run, ensure_ascii=False, indent=1))
    tmp.replace(p)


def update(run_id: str, fn):
    """Read-modify-write run.json under the run's lock; returns the new run."""
    with _lock(run_id):
        run = read_run(run_id)
        fn(run)
        _write(run)
        return run


def list_runs(limit: int = 50) -> list:
    if not RUNS.exists():
        return []
    out = []
    for p in RUNS.glob("*/run.json"):
        r = json.loads(p.read_text())
        out.append({k: r.get(k) for k in ("id", "name", "status", "created", "updated",
                                          "workflow_id")}
                   | {"counts": _counts(r)})
    return sorted(out, key=lambda r: r["created"] or 0, reverse=True)[:limit]


def _counts(run):
    c = {}
    for n in run.get("nodes", {}).values():
        c[n["status"]] = c.get(n["status"], 0) + 1
    return c


def _event(run, msg):
    run.setdefault("events", []).append({"t": time.time(), "msg": msg})
    run["events"] = run["events"][-200:]


def start(workflow: dict, parent: str = "") -> dict:
    errs = validate(workflow)
    if errs:
        raise ValueError(errs)
    run_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = run_dir(run_id)
    d.mkdir(parents=True, exist_ok=False)
    run = {"id": run_id, "name": workflow.get("name"), "workflow_id": workflow.get("id"),
           "workflow": workflow, "status": "running", "created": time.time(),
           "parent": parent or None, "works": [], "events": [],
           "nodes": {n["id"]: {"status": "pending", "type": n["type"]}
                     for n in workflow["nodes"]}}
    _event(run, "run created" + (f" (rerun of {parent})" if parent else ""))
    _write(run)
    drive(run_id)
    return run


def drive(run_id: str) -> None:
    """Start the driver thread for a run; if one is running, ask it for another pass."""
    with _guard:
        if run_id in _drivers:
            _drivers[run_id] = "again"
            return
        _drivers[run_id] = "running"
    threading.Thread(target=_drive, args=(run_id,), daemon=True).start()


def _upstream(wf, nid):
    return {parse_port(e["from"])[0] for e in wf["edges"] if parse_port(e["to"])[0] == nid}


def downstream(wf, nid) -> list:
    out, todo = [], [nid]
    while todo:
        cur = todo.pop()
        for e in wf["edges"]:
            f, t = parse_port(e["from"]), parse_port(e["to"])
            if f[0] == cur and t[0] not in out:
                out.append(t[0])
                todo.append(t[0])
    return out


def _inputs(run, nid):
    wf = run["workflow"]
    vals = {}
    for e in wf["edges"]:
        f, t = parse_port(e["from"]), parse_port(e["to"])
        if t[0] == nid:
            vals[t[1]] = run["nodes"][f[0]]["outputs"][f[1]]
    return vals


def _drive(run_id: str) -> None:
    try:
        while True:
            _drive_pass(run_id)
            with _guard:
                if _drivers.get(run_id) == "again":
                    _drivers[run_id] = "running"
                    continue
                _drivers.pop(run_id, None)
                return
    except Exception:  # noqa: BLE001 - never leave the run marked as driven
        with _guard:
            _drivers.pop(run_id, None)
        raise


def _drive_pass(run_id: str) -> None:
    threads = {}
    while True:
        with _lock(run_id):
            run = read_run(run_id)
            wf = run["workflow"]
            order, _ = topo_order(wf["nodes"], wf["edges"])
            st = run["nodes"]
            launch = []
            for nid in order:
                if st[nid]["status"] != "pending":
                    continue
                ups = _upstream(wf, nid)
                if all(st[u]["status"] in FINAL for u in ups):
                    st[nid]["status"] = "running"
                    st[nid]["started"] = time.time()
                    st[nid].pop("error", None)
                    launch.append(nid)
            if launch:
                _write(run)
        for nid in launch:
            t = threading.Thread(target=_exec, args=(run_id, nid), daemon=True)
            threads[nid] = t
            t.start()
        threads = {k: t for k, t in threads.items() if t.is_alive()}
        if not launch and not threads:
            break
        time.sleep(0.3)
    update(run_id, _settle)


def _settle(run):
    """Run status from node states once nothing is running."""
    s = [n["status"] for n in run["nodes"].values()]
    if any(x == "running" for x in s):
        run["status"] = "running"
    elif any(x == "failed" for x in s):
        run["status"] = "failed"
    elif any(x == "waiting_review" for x in s):
        run["status"] = "waiting_review"
    elif all(x in FINAL for x in s):
        if run["status"] != "done":
            _event(run, "run finished")
        run["status"] = "done"
    else:
        run["status"] = "running"


class Ctx:
    def __init__(self, run_id, nid):
        self.run_id, self.node_id = run_id, nid
        self.run_dir = run_dir(run_id)
        self.dir = self.run_dir / "nodes" / nid
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta = {}
        self._note = None

    def note(self, msg):
        if msg != self._note:
            self._note = msg
            update(self.run_id, lambda r: r["nodes"][self.node_id].__setitem__("note", msg))

    def on_wait(self, sec):
        self.note(f"等待 GPU（{sec // 60} 分钟）" if sec >= 60 else "已提交，等待 GPU")

    def save_work(self, item):
        update(self.run_id, lambda r: (r["works"].__setitem__(
            slice(None), [w for w in r["works"] if w.get("node") != item["node"]] + [item])))


def _warm_downstream(run, nid):
    """Wake the GPU modules of downstream GPU nodes in the background, so their cold starts
    overlap this node's work (video ~12 min, music ~9, image ~8). Only called on a cache
    miss: a rerun whose upstream is all cached boots nothing it will not use."""
    wf = run["workflow"]
    types = {n["id"]: n["type"] for n in wf["nodes"]}
    mods = {NODES[types[d]]["runner"][4:] for d in downstream(wf, nid)
            if NODES[types[d]]["runner"].startswith("gpu:")
            and run["nodes"][d]["status"] == "pending"}
    for m in mods - _warmed.get(run["id"], set()):
        if not WARM_GPUS:
            break
        _warmed.setdefault(run["id"], set()).add(m)

        def warm(m=m):
            try:
                from .. import gpu_client
                gpu_client.gpu(m).ensure_running()
            except Exception:  # noqa: BLE001 - the node's own submit retries the launch
                pass
        threading.Thread(target=warm, daemon=True).start()


_warmed = {}
WARM_GPUS = os.environ.get("EVP_WF_WARM_GPUS", "1") == "1"


def _exec(run_id: str, nid: str) -> None:
    run = read_run(run_id)
    node = next(n for n in run["workflow"]["nodes"] if n["id"] == nid)
    decl = NODES[node["type"]]
    t0 = time.time()
    try:
        inputs = _inputs(run, nid)
        params = effective_params(node["type"], node.get("params") or {})
        hashes = {p: value_hash(v, decl["inputs"][p]) for p, v in inputs.items()}
        key = cache_key(node["type"], node.get("params") or {}, hashes)
        n_cand = cand.count(node["type"], node.get("params") or {})
        if n_cand > 1:
            _exec_candidates(run, run_id, nid, node, decl, hashes, n_cand, t0)
            return
        hit = cache_get(key) if decl["cache"] else None
        if hit:
            def done_cached(r):
                n = r["nodes"][nid]
                n.update(status="cached", outputs=hit["outputs"], cache_key=key,
                         meta=hit.get("meta", {}), ended=time.time(),
                         seconds=round(time.time() - t0, 2))
                n.pop("note", None)
                _event(r, f"{nid} cache hit")
            update(run_id, done_cached)
            return
        if node["type"] == "review.human":
            def wait(r):
                n = r["nodes"][nid]
                n.update(status="waiting_review", cache_key=key, review_input=inputs["input"],
                         note=params.get("note") or "等待人工审核")
                # run status becomes waiting_review in _settle, once nothing else runs
                _event(r, f"{nid} waiting for review")
            update(run_id, wait)
            return
        ctx = Ctx(run_id, nid)
        runner = decl["runner"]
        _warm_downstream(run, nid)
        if runner.startswith("gpu:"):
            lock = _gpu_lock(runner[4:])
            if lock.locked():
                ctx.note("排队：同一 GPU 模块上一个节点还没完成")
            with lock:
                ctx.note("已提交，等待 GPU")
                outputs = decl["run"](inputs, params, ctx)
        else:
            outputs = decl["run"](inputs, params, ctx)
        if decl["cache"]:
            outputs = cache_put(key, node["type"], outputs, ctx.meta)

        def done(r):
            n = r["nodes"][nid]
            n.update(status="done", outputs=outputs, cache_key=key, meta=ctx.meta,
                     ended=time.time(), seconds=round(time.time() - t0, 2))
            n.pop("note", None)
            _event(r, f"{nid} done in {n['seconds']} s")
        update(run_id, done)
    except Exception as e:  # noqa: BLE001 - shown on the node
        tb = traceback.format_exc()[-2000:]

        def failed(r):
            n = r["nodes"][nid]
            n.update(status="failed", error=str(e)[:1000], ended=time.time(),
                     seconds=round(time.time() - t0, 2), trace=tb)
            n.pop("note", None)
            _event(r, f"{nid} failed: {str(e)[:200]}")
        update(run_id, failed)


def _exec_candidates(run, run_id, nid, node, decl, hashes, n, t0):
    """N candidates with seeds seed..seed+N-1 (candidates.py); the node then waits for a
    review that picks one. Exceptions propagate to _exec, which marks the node failed."""
    inputs = _inputs(run, nid)
    runner = decl["runner"]
    params = node.get("params") or {}
    base = effective_params(node["type"], params)["seed"]
    keys = [cache_key(node["type"], {**params, "seed": s}, hashes) for s in cand.seeds(base, n)]
    if not decl["cache"] or not all(cache_get(k) for k in keys):
        _warm_downstream(run, nid)  # only on a miss, as in _exec

    def call(params, ctx):
        if runner.startswith("gpu:"):
            lock = _gpu_lock(runner[4:])
            if lock.locked():
                ctx.note("排队：同一 GPU 模块上一个节点还没完成")
            with lock:
                return decl["run"](inputs, params, ctx)
        return decl["run"](inputs, params, ctx)

    cands = cand.generate(run_id, nid, node, hashes, n, call)
    update(run_id, lambda r: cand.mark_waiting(r, nid, cands, t0, _event))


# ---------------------------------------------------------------- review / retry

def review(run_id: str, nid: str, decision: str, note: str = "", choice=None) -> dict:
    """Approve or reject a waiting node. A node waiting with candidates (candidates.py)
    needs choice=k on approve; candidate k's outputs then flow downstream."""
    def apply(r):
        n = r["nodes"].get(nid)
        if not n or n["status"] != "waiting_review":
            raise ValueError(f"{nid} is not waiting for review")
        if decision == "approve" and n.get("candidates"):
            outputs = cand.choose(n, choice)  # raises before anything is changed
        n["review"] = {"decision": decision, "note": note, "t": time.time()}
        if n.get("candidates") and decision == "approve":
            n["review"]["choice"] = choice
        n["ended"] = time.time()
        n["seconds"] = round(n["ended"] - n.get("started", n["ended"]), 2)
        if decision == "approve" and n.get("candidates"):
            n.update(status="done", outputs=outputs)
            n.pop("note", None)
            r["status"] = "running"
            _event(r, f"{nid} approved candidate {choice}" + (f": {note}" if note else ""))
        elif decision == "approve":
            outputs = {"output": n.pop("review_input")}
            if NODES["review.human"]["cache"]:
                outputs = cache_put(n["cache_key"], "review.human", outputs,
                                    {"approved": time.time(), "note": note})
            n.update(status="done", outputs=outputs)
            n.pop("note", None)
            r["status"] = "running"
            _event(r, f"{nid} approved" + (f": {note}" if note else ""))
        else:
            n.update(status="failed", error="人工审核打回" + (f"：{note}" if note else ""))
            n.pop("note", None)
            r["status"] = "failed"
            _event(r, f"{nid} rejected" + (f": {note}" if note else ""))
    run = update(run_id, apply)
    if decision == "approve":
        drive(run_id)
    return run


def retry(run_id: str, nid: str) -> dict:
    """Rerun one node and everything downstream; upstream results are kept."""
    def apply(r):
        if nid not in r["nodes"]:
            raise ValueError(f"no node {nid}")
        if any(n["status"] == "running" for n in r["nodes"].values()):
            raise ValueError("run still has running nodes")
        for k in [nid] + downstream(r["workflow"], nid):
            r["nodes"][k] = {"status": "pending", "type": r["nodes"][k]["type"]}
        r["works"] = [w for w in r["works"] if w.get("node") in r["nodes"]
                      and r["nodes"][w["node"]]["status"] in FINAL]
        r["status"] = "running"
        _event(r, f"retry from {nid}")
    run = update(run_id, apply)
    drive(run_id)
    return run


def recover() -> None:
    """Called at server start: nodes left 'running' by a dead process become failed (retry
    them); runs waiting for review stay waiting."""
    if not RUNS.exists():
        return
    for p in RUNS.glob("*/run.json"):
        r = json.loads(p.read_text())
        if r.get("status") != "running":
            continue

        def fix(run):
            for n in run["nodes"].values():
                if n["status"] == "running":
                    n.update(status="failed", error="服务重启时节点还在运行，可以单独重试")
            _settle(run)
            if any(n["status"] == "pending" for n in run["nodes"].values()) \
                    and run["status"] == "running":
                run["status"] = "failed"
        update(r["id"], fix)
