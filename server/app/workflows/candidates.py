"""Candidates (DESIGN.md section 5, 候选数).

A GPU generation node (image.qwen, video.h3, music.yue2) takes a param `candidates`
(1-4, default 1). With N > 1 the engine runs the node N times with seeds seed, seed+1, ...
Each candidate is cached on its own: its cache key is the ordinary key of the node with
that seed, so candidate 0 shares its cache entry with a plain candidates=1 run, and raising
N from 3 to 4 only generates the fourth. When all N exist the node goes to waiting_review
with run.nodes[nid].candidates = [{k, seed, outputs, meta, cache_key, cached}], and
review(..., decision="approve", choice=k) passes candidate k's outputs downstream.

With candidates = 1 nothing here runs: the engine takes its ordinary path, and the
`candidates` param is left out of every cache key, so existing keys do not change.

Remembered choices. A candidate set is the ordered list of candidate cache keys; when a
person approves candidate k, remember() writes data/wf_cache/_choices/<sha256 of the keys
joined by newlines>.json = {choice, seed, keys, t} (tmp file + rename). On a later run where
EVERY candidate is a cache hit and a choice exists for exactly that set, the engine takes
it without asking: the node becomes done with candidate k's outputs, note
"沿用上次的选择：候选 k+1（seed s）", choice/auto_choice on the node, and the run goes on
downstream (no waiting_review, so the task center sends no 待审核 notification). If any
candidate was generated in this run, or no choice is stored, the node waits as before.
Only engine.review() writes a choice. To pick again, retry the node (engine.retry): when
the retried node carries candidates it first forgets the stored choice for its set, so
the rerun (all cache hits, nothing generated) waits for review with the same previews.
"""
import hashlib
import json
import time
import uuid

from .nodes import NODES, effective_params

GEN_TYPES = ("image.qwen", "video.h3", "music.yue2")
MAX = 4
SEED_MOD = 2 ** 31
PARAM = {"type": "integer", "default": 1, "min": 1, "max": MAX,
         "label": "候选数（多于 1 个时审核选一个往下走）"}


def register() -> None:
    """Declare the param on the GPU generation nodes (idempotent)."""
    for t in GEN_TYPES:
        if t in NODES:
            NODES[t]["params"].setdefault("candidates", dict(PARAM))


register()


def count(node_type: str, params: dict) -> int:
    """How many candidates a node asks for (1 for nodes without the param)."""
    decl = NODES[node_type]["params"]
    if "candidates" not in decl or "seed" not in decl:
        return 1
    try:
        n = int(params.get("candidates", 1))
    except (TypeError, ValueError):
        return 1
    return max(1, min(MAX, n))


def seeds(base: int, n: int) -> list:
    return [(int(base) + k) % SEED_MOD for k in range(n)]


def generate(run_id: str, nid: str, node: dict, hashes: dict, n: int, call) -> list:
    """Produce n candidates; `call(params, ctx) -> outputs` runs the node once (the engine
    passes its GPU-locked runner). Each candidate gets its own dir nodes/<nid>/c<k>/."""
    from . import engine  # engine imports this module at load time
    t = node["type"]
    decl = NODES[t]
    raw = dict(node.get("params") or {})
    base = effective_params(t, raw)["seed"]
    out = []
    for k, seed in enumerate(seeds(base, n)):
        p_raw = {**raw, "seed": seed}
        key = engine.cache_key(t, p_raw, hashes)
        hit = engine.cache_get(key) if decl["cache"] else None
        if hit:
            out.append({"k": k, "seed": seed, "outputs": hit["outputs"],
                        "meta": hit.get("meta", {}), "cache_key": key, "cached": True})
            continue
        ctx = engine.Ctx(run_id, nid)
        ctx.dir = ctx.dir / f"c{k}"
        ctx.dir.mkdir(parents=True, exist_ok=True)
        ctx.note(f"生成候选 {k + 1}/{n}")
        outputs = call(effective_params(t, p_raw), ctx)
        if decl["cache"]:
            outputs = engine.cache_put(key, t, outputs, ctx.meta)
        out.append({"k": k, "seed": seed, "outputs": outputs, "meta": ctx.meta,
                    "cache_key": key, "cached": False})
    return out


def mark_waiting(run: dict, nid: str, cands: list, t0: float, event) -> None:
    """While waiting, `outputs` holds a preview {"c<k>.<port>": value} so the existing
    file endpoint (GET /api/wf_runs/<id>/file?node=<nid>&port=c1.image) serves every
    candidate. Downstream nodes never read it: they start only once the node is done."""
    n = run["nodes"][nid]
    n.update(status="waiting_review", candidates=cands,
             outputs={f"c{c['k']}.{p}": v for c in cands for p, v in c["outputs"].items()},
             note=f"{len(cands)} 个候选，审核时选一个往下走",
             seconds=round(time.time() - t0, 2))
    event(run, f"{nid} {len(cands)} candidates waiting for review")


def choose(n: dict, choice) -> dict:
    """Outputs of the chosen candidate; records the choice on the node."""
    cands = n["candidates"]
    if isinstance(choice, bool) or not isinstance(choice, int) \
            or not 0 <= choice < len(cands):
        raise ValueError(f"choose a candidate 0..{len(cands) - 1} (got {choice!r})")
    c = cands[choice]
    n.update(choice=choice, cache_key=c["cache_key"], meta=c.get("meta", {}))
    return c["outputs"]


# ---------------------------------------------------------------- remembered choices

def _choices_dir():
    from . import engine
    return engine.CACHE / "_choices"


def set_key(cands: list) -> str:
    """Identity of a candidate set: sha256 of the ordered candidate cache keys."""
    return hashlib.sha256("\n".join(c["cache_key"] for c in cands).encode()).hexdigest()


def remembered(cands: list):
    """The stored choice for this exact set, or None. Only when every candidate is a cache
    hit: a freshly generated candidate is something the person has not seen yet."""
    if not cands or not all(c.get("cached") for c in cands):
        return None
    p = _choices_dir() / f"{set_key(cands)}.json"
    try:
        j = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    k = j.get("choice")
    if isinstance(k, bool) or not isinstance(k, int) or not 0 <= k < len(cands) \
            or j.get("keys") != [c["cache_key"] for c in cands]:
        return None
    return k


def remember(cands: list, choice: int) -> None:
    """Store a person's choice for this set (atomic: tmp file + rename)."""
    d = _choices_dir()
    d.mkdir(parents=True, exist_ok=True)
    key = set_key(cands)
    tmp = d / f".{key}.{uuid.uuid4().hex[:6]}.tmp"
    tmp.write_text(json.dumps({"choice": choice, "seed": cands[choice].get("seed"),
                               "keys": [c["cache_key"] for c in cands], "t": time.time()},
                              ensure_ascii=False))
    tmp.replace(d / f"{key}.json")


def forget(cands: list) -> None:
    """Drop the stored choice for this set, so the next run asks again."""
    if cands:
        (_choices_dir() / f"{set_key(cands)}.json").unlink(missing_ok=True)


def mark_auto(run: dict, nid: str, cands: list, k: int, t0: float, event) -> None:
    """Take a remembered choice: the node is done with candidate k's outputs and keeps the
    candidate list (previews for the inspector, and what retry needs to forget the set)."""
    n = run["nodes"][nid]
    c = cands[k]
    n.update(status="done", candidates=cands, choice=k, auto_choice=True,
             cache_key=c["cache_key"], meta=c.get("meta", {}), outputs=c["outputs"],
             note=f"沿用上次的选择：候选 {k + 1}（seed {c['seed']}）",
             ended=time.time(), seconds=round(time.time() - t0, 2))
    event(run, f"{nid} reused earlier choice: candidate {k}")
