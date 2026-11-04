"""Workflow validator (DESIGN.md section 6, step 2).

validate(wf) returns a list of errors; an empty list means the workflow can run. Each
error is {"code", "message", "node"?, "port"?, "edge"?} so the page and the Agent can point
at the offending node or port.
"""
import json
import re
from pathlib import Path

from .nodes import NODES, PORT_TYPES

MODULES_DIR = Path(__file__).resolve().parents[3] / "infra" / "gpu_ec2" / "modules"
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
PYTYPES = {"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,)}


def configured_modules() -> set:
    """GPU modules with a worker config (infra/gpu_ec2/modules/<m>.json)."""
    out = set()
    for p in MODULES_DIR.glob("*.json"):
        try:
            out.add(json.loads(p.read_text())["module"])
        except (ValueError, KeyError):
            pass
    return out


def parse_port(ref):
    """'n3.audio' -> ('n3', 'audio'); None when malformed."""
    if not isinstance(ref, str) or ref.count(".") != 1:
        return None
    a, b = ref.split(".")
    return (a, b) if a and b else None


def topo_order(nodes: list, edges: list):
    """Kahn's algorithm over node ids; returns (order, cyclic node ids)."""
    ids = [n["id"] for n in nodes]
    deps = {i: set() for i in ids}
    for e in edges:
        f, t = parse_port(e.get("from")), parse_port(e.get("to"))
        if f and t and f[0] in deps and t[0] in deps:
            deps[t[0]].add(f[0])
    order, done = [], set()
    while True:
        ready = [i for i in ids if i not in done and deps[i] <= done]
        if not ready:
            break
        order += ready
        done |= set(ready)
    return order, [i for i in ids if i not in done]


def levels(nodes: list, edges: list) -> dict:
    """{node id: column} where column = longest path from a source (for the graph view)."""
    order, _ = topo_order(nodes, edges)
    lv = {i: 0 for i in order}
    for i in order:
        for e in edges:
            f, t = parse_port(e.get("from")), parse_port(e.get("to"))
            if f and t and t[0] == i and f[0] in lv:
                lv[i] = max(lv[i], lv[f[0]] + 1)
    return lv


def output_type(wf_nodes: dict, edges: list, node_id: str, port: str, seen=None):
    """Resolved type of an output port; 'any' passthrough outputs take the type of the
    edge feeding the named input."""
    n = wf_nodes.get(node_id)
    if not n or n.get("type") not in NODES:
        return None
    decl = NODES[n["type"]]
    t = decl["outputs"].get(port)
    if t != "any":
        return t
    src_in = decl["passthrough"].get(port)
    seen = seen or set()
    if not src_in or (node_id, port) in seen:
        return "any"
    seen.add((node_id, port))
    for e in edges:
        f, to = parse_port(e.get("from")), parse_port(e.get("to"))
        if f and to and to == (node_id, src_in):
            return output_type(wf_nodes, edges, f[0], f[1], seen)
    return "any"


def _check_param(nid, name, schema, value, errs):
    def err(msg):
        errs.append({"code": "bad_param", "node": nid, "port": name, "message": msg})
    types = PYTYPES.get(schema.get("type", "string"), (str,))
    if isinstance(value, bool) and schema.get("type") in ("integer", "number"):
        return err(f"{nid}.{name} must be a {schema['type']}, got a boolean")
    if not isinstance(value, types):
        return err(f"{nid}.{name} must be a {schema.get('type', 'string')}")
    if "enum" in schema and value not in schema["enum"]:
        return err(f"{nid}.{name} must be one of {schema['enum']}, got {value!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "min" in schema and value < schema["min"]:
            err(f"{nid}.{name} must be >= {schema['min']}")
        if "max" in schema and value > schema["max"]:
            err(f"{nid}.{name} must be <= {schema['max']}")


def validate(wf, modules=None) -> list:
    errs = []
    if not isinstance(wf, dict):
        return [{"code": "format", "message": "workflow must be a JSON object"}]
    nodes, edges = wf.get("nodes"), wf.get("edges", [])
    if not isinstance(nodes, list) or not nodes:
        return [{"code": "format", "message": "workflow needs a non-empty nodes list"}]
    if not isinstance(edges, list):
        return [{"code": "format", "message": "edges must be a list"}]
    if not str(wf.get("name") or "").strip():
        errs.append({"code": "format", "message": "workflow needs a name"})
    modules = configured_modules() if modules is None else set(modules)

    by_id = {}
    for n in nodes:
        if not isinstance(n, dict):
            errs.append({"code": "format", "message": "each node must be an object"})
            continue
        nid = n.get("id")
        if not isinstance(nid, str) or not ID_RE.match(nid):
            errs.append({"code": "bad_id", "node": str(nid),
                         "message": f"node id {nid!r} must match {ID_RE.pattern}"})
            continue
        if nid in by_id:
            errs.append({"code": "duplicate_id", "node": nid, "message": f"duplicate node id {nid}"})
            continue
        by_id[nid] = n
        if n.get("type") not in NODES:
            errs.append({"code": "unknown_type", "node": nid,
                         "message": f"{nid}: unknown node type {n.get('type')!r}"})
        if not isinstance(n.get("params", {}), dict):
            errs.append({"code": "bad_param", "node": nid, "message": f"{nid}: params must be an object"})
            n["params"] = {}

    # edges: ports exist, types match, one edge per input
    fed = {}
    for k, e in enumerate(edges):
        if not isinstance(e, dict):
            errs.append({"code": "format", "edge": k, "message": f"edge {k} must be an object"})
            continue
        f, t = parse_port(e.get("from")), parse_port(e.get("to"))
        label = f"{e.get('from')} -> {e.get('to')}"
        if not f or not t:
            errs.append({"code": "bad_edge", "edge": k,
                         "message": f"edge {label}: ports are written node.port"})
            continue
        bad = False
        for (nid, port), side in ((f, "outputs"), (t, "inputs")):
            n = by_id.get(nid)
            if not n:
                errs.append({"code": "bad_edge", "edge": k, "node": nid,
                             "message": f"edge {label}: no node {nid}"})
                bad = True
            elif n.get("type") in NODES and port not in NODES[n["type"]][side]:
                have = ", ".join(NODES[n["type"]][side]) or "none"
                errs.append({"code": "bad_port", "edge": k, "node": nid, "port": port,
                             "message": f"edge {label}: {n['type']} has no "
                                        f"{side[:-1]} port {port!r} (has: {have})"})
                bad = True
        if bad:
            continue
        if f[0] == t[0]:
            errs.append({"code": "cycle", "edge": k, "node": f[0],
                         "message": f"edge {label} connects a node to itself"})
            continue
        if t in fed:
            errs.append({"code": "multiple_inputs", "edge": k, "node": t[0], "port": t[1],
                         "message": f"input {t[0]}.{t[1]} has more than one edge"})
        fed[t] = f
        tn = by_id[t[0]]
        if tn.get("type") in NODES and by_id[f[0]].get("type") in NODES:
            want = NODES[tn["type"]]["inputs"][t[1]]
            have = output_type(by_id, edges, f[0], f[1])
            if want != "any" and have not in ("any", want):
                errs.append({"code": "type_mismatch", "edge": k, "node": t[0], "port": t[1],
                             "message": f"edge {label}: {have} output into {want} input"})

    _, cyc = topo_order(list(by_id.values()), [e for e in edges if isinstance(e, dict)])
    if cyc:
        errs.append({"code": "cycle", "node": cyc[0],
                     "message": "workflow has a cycle through " + ", ".join(cyc)})

    # params and required inputs
    for nid, n in by_id.items():
        decl = NODES.get(n.get("type"))
        if not decl:
            continue
        params = n.get("params") or {}
        for name in params:
            if name not in decl["params"]:
                errs.append({"code": "unknown_param", "node": nid, "port": name,
                             "message": f"{nid}: {n['type']} has no param {name!r} "
                                        f"(has: {', '.join(decl['params']) or 'none'})"})
        for name, schema in decl["params"].items():
            value = params.get(name, schema.get("default"))
            need = schema.get("required")
            alt = schema.get("required_unless_input")
            if alt and (nid, alt) not in fed:
                need = True
            if need and (value is None or (isinstance(value, str) and not value.strip())):
                why = f" (or connect input {alt})" if alt else ""
                errs.append({"code": "missing_param", "node": nid, "port": name,
                             "message": f"{nid}: required param {name!r} is empty{why}"})
                continue
            if value is not None and name in params:
                _check_param(nid, name, schema, value, errs)
        for port in decl["inputs"]:
            if port not in decl["optional"] and (nid, port) not in fed:
                errs.append({"code": "missing_input", "node": nid, "port": port,
                             "message": f"{nid}: input {port!r} ({decl['inputs'][port]}) "
                                        "is not connected"})
        r = decl["runner"]
        if r.startswith("gpu:") and r[4:] not in modules:
            errs.append({"code": "module_unavailable", "node": nid,
                         "message": f"{nid}: GPU module {r[4:]!r} is not configured "
                                    "(infra/gpu_ec2/modules)"})
    return errs


def check_types():
    """Self-check of the node library declarations (used by the tests)."""
    for t, d in NODES.items():
        for port, ty in list(d["inputs"].items()) + list(d["outputs"].items()):
            assert ty in PORT_TYPES, (t, port, ty)
        for o in d["optional"]:
            assert o in d["inputs"], (t, o)
        for out, src in d["passthrough"].items():
            assert d["outputs"][out] == "any" and src in d["inputs"], (t, out)
