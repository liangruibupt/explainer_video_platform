"""Static checks for a PrevisPlan (docs/PREVIS_CONTRACT.md sections 4-7), pure Python.

Two parts:
- a port of core/expr.ts (lexer, names(), evaluate()) with JavaScript number semantics, so a
  spec expression that passes here also passes in the Node builder (tests compare both);
- validate(plan, scenes, library_assets) -> list of CheckIssue dicts
  {code, severity, message, where}. Mirrors the shapes in core/types.ts. No rendering, no
  geometry: bounding boxes, collisions and frame timing are checked later by A and B.

Issue codes (all "error" unless noted):
  schema           wrong JSON type, missing field, unknown enum value
  bad_id           id is not [a-z][a-z0-9_]* (library asset ids are "asset_...")
  duplicate_id     two assets / sets / objects / parts / shots share an id
  expr_error       an expression does not parse, uses an unknown name/function, or is not finite
  unknown_parent   part parent is not a part of the same asset;  cycle: parent loop
  bad_shape_param  shape parameter missing, or r <= 0, r_inner >= r_outer, ...
  bad_anchor       asset anchor refers to an unknown part
  unknown_asset    set object refers to an asset not in the plan / library
  unknown_set      shot refers to an unknown set
  unknown_target   action target is not an object (or object.part where parts are allowed)
  unknown_point    point is not [x,y,z], a mark, an object, or object.anchor
  unknown_dof      pose / spin / set pose uses a DOF the asset does not have
  dof_range        pose value outside the DOF's [min, max]
  missing_shot     a storyboard scene has no shot;  extra_shot: a shot has no scene
  bad_time         a TimeAnchor sets none or several of t / frame / word / mark, or bad values
  anchor_not_found a word anchor's text (with occurrence) is not in the scene's narration
  too_few_keys     a shot has fewer than 2 camera keys
  time_order       camera keys out of order / at the same time, or an action's until is not
                   after its at (only where the order is known without durations: mark
                   start/end and word anchors without offset)
  missing_target   image3d asset without target
  scale_not_1      (warn) set object scale is not 1, real size is lost
  linked_parts     (warn) an object with one free-rotation DOF is animated next to another
                   animated object in the same shot: parts that move together should be
                   parts of ONE asset (see _check_linked)

A parametric asset may carry "extends": "<library id>" of a parametric library model; its
spec is then checked (and built) as merge_spec(library spec, plan spec).
"""
import copy
import math
import re

# ----------------------------------------------------------------- expressions (core/expr.ts)


class ExprError(ValueError):
    def __init__(self, message: str, expr: str):
        super().__init__(f'{message} (in "{expr}")')
        self.expr = expr


_D = math.pi / 180
_NAN = float("nan")
_INF = float("inf")


def _num(f):
    """Wrap a math function so domain / overflow errors give NaN / Inf like JavaScript."""
    def g(*a):
        if any(math.isnan(x) for x in a):
            return _NAN
        try:
            return f(*a)
        except OverflowError:
            return _INF
        except (ValueError, ZeroDivisionError):
            return _NAN
    return g


def _js_round(x):  # Math.round: half up
    if math.isnan(x) or math.isinf(x):
        return x
    return float(math.floor(x + 0.5))


def _js_floor(x):
    return x if math.isnan(x) or math.isinf(x) else float(math.floor(x))


def _js_ceil(x):
    return x if math.isnan(x) or math.isinf(x) else float(math.ceil(x))


def _js_rem(x, m):  # JavaScript %: sign of the dividend, NaN for m = 0 or infinite x
    if m == 0 or math.isinf(x) or math.isnan(x) or math.isnan(m):
        return _NAN
    if math.isinf(m):
        return x
    return math.fmod(x, m)


def _js_min(*a):
    return _NAN if any(math.isnan(x) for x in a) else min(a)


def _js_max(*a):
    return _NAN if any(math.isnan(x) for x in a) else max(a)


def _js_div(a, b):
    if b == 0:
        if a == 0 or math.isnan(a):
            return _NAN
        return math.copysign(_INF, a) * math.copysign(1, b)
    return a / b


def _js_pow(a, b):
    if b == 0:
        return 1.0
    if math.isnan(a) or math.isnan(b) or (abs(a) == 1 and math.isinf(b)):
        return _NAN
    try:
        return math.pow(a, b)
    except OverflowError:
        return _INF
    except (ValueError, ZeroDivisionError):
        if a == 0 and b < 0:
            return _INF
        return _NAN


def _js_mul(a, b):
    try:
        return a * b
    except OverflowError:
        return _INF


FUNCS = {
    # name: (min args, max args, fn)
    "sin": (1, 1, _num(lambda x: math.sin(x * _D))),
    "cos": (1, 1, _num(lambda x: math.cos(x * _D))),
    "tan": (1, 1, _num(lambda x: math.tan(x * _D))),
    "asin": (1, 1, _num(lambda x: math.asin(x) / _D)),
    "acos": (1, 1, _num(lambda x: math.acos(x) / _D)),
    "atan": (1, 1, _num(lambda x: math.atan(x) / _D)),
    "atan2": (2, 2, _num(lambda y, x: math.atan2(y, x) / _D)),
    "sqrt": (1, 1, _num(lambda x: math.inf if x == math.inf else math.sqrt(x))),
    "abs": (1, 1, abs),
    "min": (1, 16, _js_min),
    "max": (1, 16, _js_max),
    "clamp": (3, 3, lambda x, lo, hi: _js_min(hi, _js_max(lo, x))),
    "floor": (1, 1, _js_floor),
    "ceil": (1, 1, _js_ceil),
    "round": (1, 1, _js_round),
    "mod": (2, 2, lambda x, m: _js_rem(_js_rem(x, m) + m, m)),
}
CONSTS = {"pi": math.pi}

_NUM_RE = re.compile(r"(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _lex(src: str) -> list:
    out, i = [], 0
    while i < len(src):
        c = src[i]
        if c in " \t":
            i += 1
        elif c.isascii() and (c.isdigit() or c == "."):
            m = _NUM_RE.match(src, i)
            if not m:
                raise ExprError(f"bad number at {i}", src)
            out.append(("num", float(m.group(0))))
            i = m.end()
        elif c.isascii() and (c.isalpha() or c == "_"):
            m = _ID_RE.match(src, i)
            out.append(("id", m.group(0)))
            i = m.end()
        elif c in "+-*/^(),":
            out.append(("op", c))
            i += 1
        else:
            raise ExprError(f'unexpected "{c}"', src)
    return out


def names(src: str) -> list:
    """Names an expression refers to (excluding functions and constants), first-seen order."""
    t = _lex(src)
    seen = []
    for i, (k, v) in enumerate(t):
        nxt = t[i + 1] if i + 1 < len(t) else None
        if k == "id" and v not in CONSTS and not (nxt == ("op", "(")) and v not in seen:
            seen.append(v)
    return seen


def evaluate(src, env: dict) -> float:
    """Value of an expression or number, same result as core/expr.ts evaluate()."""
    if isinstance(src, bool) or not isinstance(src, (int, float, str)):
        raise ExprError("not a number or expression", str(src))
    if not isinstance(src, str):
        if not math.isfinite(src):
            raise ExprError("not a finite number", str(src))
        return float(src)
    toks = _lex(src)
    p = [0]

    def peek():
        return toks[p[0]] if p[0] < len(toks) else None

    def is_op(v):
        t = peek()
        return t is not None and t[0] == "op" and t[1] == v

    def expect(v):
        if not is_op(v):
            raise ExprError(f'expected "{v}"', src)
        p[0] += 1

    def primary():
        t = peek()
        if t is None:
            raise ExprError("unexpected end", src)
        k, v = t
        if k == "num":
            p[0] += 1
            return v
        if k == "op" and v == "(":
            p[0] += 1
            x = add()
            expect(")")
            return x
        if k == "op" and v in "-+":
            p[0] += 1
            x = power()
            return -x if v == "-" else x
        if k == "id":
            p[0] += 1
            if is_op("("):
                p[0] += 1
                f = FUNCS.get(v)
                if not f:
                    raise ExprError(f"unknown function {v}", src)
                args = []
                if not is_op(")"):
                    args.append(add())
                    while is_op(","):
                        p[0] += 1
                        args.append(add())
                expect(")")
                if len(args) < f[0] or len(args) > f[1]:
                    raise ExprError(f"{v} takes {f[0]}-{f[1]} args", src)
                return float(f[2](*args))
            if v in env:
                return float(env[v])
            if v in CONSTS:
                return CONSTS[v]
            raise ExprError(f"unknown name {v}", src)
        raise ExprError(f'unexpected "{v}"', src)

    def power():
        b = primary()
        if is_op("^"):
            p[0] += 1
            return _js_pow(b, unary())
        return b

    def unary():
        if is_op("-"):
            p[0] += 1
            return -unary()
        if is_op("+"):
            p[0] += 1
            return unary()
        return power()

    def mul():
        v = unary()
        while is_op("*") or is_op("/"):
            o = toks[p[0]][1]
            p[0] += 1
            r = unary()
            v = _js_mul(v, r) if o == "*" else _js_div(v, r)
        return v

    def add():
        v = mul()
        while is_op("+") or is_op("-"):
            o = toks[p[0]][1]
            p[0] += 1
            r = mul()
            v = v + r if o == "+" else v - r
        return v

    v = add()
    if p[0] != len(toks):
        raise ExprError(f'unexpected "{_tok_str(toks[p[0]])}"', src)
    if not math.isfinite(v):
        raise ExprError("result is not finite", src)
    return v


def _tok_str(t) -> str:
    k, v = t
    if k == "num":  # JS prints 1.0 as "1"
        return str(int(v)) if float(v).is_integer() and abs(v) < 1e21 else repr(v)
    return v


# ----------------------------------------------------------------- the plan validator

ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
LIB_ID_RE = re.compile(r"^asset_[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SLOTS = ("clay", "accent1", "accent2", "accent3", "accent4", "glass")
SHAPES = ("box", "cylinder", "tube", "cone", "sphere", "capsule", "torus", "extrude",
          "lathe", "group")
# shape -> required fields (beyond id/shape)
SHAPE_REQ = {"box": ("size",), "cylinder": ("r", "h"), "tube": ("r_outer", "r_inner", "h"),
             "cone": ("r", "h"), "sphere": ("r",), "capsule": ("r", "h"),
             "torus": ("r", "tube"), "extrude": ("profile",), "lathe": ("profile",),
             "group": ()}
NUM_FIELDS = ("r", "r_top", "r_outer", "r_inner", "h", "tube", "depth", "bevel", "angle_deg")
EASES = ("linear", "in", "out", "inOut", "inOutCubic", "hold")
AXES = ("x", "y", "z")
OPS = ("pose", "spin", "move", "show", "hide", "highlight", "label", "dimension", "section",
       "explode")
# Same character class as skills/explainer-video-pipeline/scripts/captions.py PUNCT: these
# characters get no entry in words.json, so word anchors are matched on the narration without them.
PUNCT = re.compile(r'[\s，。、！？；：,.!?;:“”"\'（）()…—-]')


def strip_punct(s: str) -> str:
    return PUNCT.sub("", s)


def count_occurrences(text: str, word: str) -> int:
    """How many times word occurs in text (both without punctuation), overlapping matches."""
    t, w = strip_punct(text), strip_punct(word)
    if not w:
        return 0
    n, i = 0, t.find(w)
    while i >= 0:
        n += 1
        i = t.find(w, i + 1)
    return n


class _V:
    def __init__(self):
        self.issues = []

    def add(self, code, message, where="", severity="error"):
        it = {"code": code, "severity": severity, "message": message}
        if where:
            it["where"] = where
        self.issues.append(it)


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _expr(v: _V, x, allowed: set, where: str, env: dict | None = None):
    """Check a Num: a finite number, or an expression using only `allowed` names.
    Returns its value when env covers every name (else None)."""
    if _is_num(x):
        return float(x)
    if not isinstance(x, str):
        v.add("schema", f"{where} 应为数字或表达式字符串", where)
        return None
    try:
        used = names(x)
    except ExprError as e:
        v.add("expr_error", f"表达式无法解析：{e}", where)
        return None
    bad = [n for n in used if n not in allowed]
    if bad:
        v.add("expr_error", f"表达式 {x!r} 用了未定义的名字 {', '.join(bad)}", where)
        return None
    if env is not None and all(n in env for n in used):
        try:
            return evaluate(x, env)
        except ExprError as e:
            v.add("expr_error", f"表达式求值失败：{e}", where)
    return None


def _vec3(v: _V, x, allowed: set, where: str, env=None):
    if not (isinstance(x, list) and len(x) == 3):
        v.add("schema", f"{where} 应为 [x, y, z]", where)
        return None
    vals = [_expr(v, c, allowed, f"{where}[{i}]", env) for i, c in enumerate(x)]
    return vals if all(c is not None for c in vals) else None


def _color(v: _V, c, where):
    if c is not None and not (isinstance(c, str) and (c in SLOTS or COLOR_RE.match(c))):
        v.add("schema", f"{where} 颜色应为 #rrggbb 或 {'/'.join(SLOTS)}", where)


def _enum(v: _V, val, allowed, where, required=False):
    if val is None:
        if required:
            v.add("schema", f"缺少 {where}", where)
        return
    if val not in allowed:
        v.add("schema", f"{where} = {val!r} 不在 {list(allowed)} 中", where)


def _obj(v: _V, x, where) -> bool:
    if not isinstance(x, dict):
        v.add("schema", f"{where} 应为对象", where)
        return False
    return True


# ---------- parametric asset spec


def check_spec(spec, where: str = "spec") -> list:
    """Static checks for one AssetSpec (no geometry). Returns CheckIssue dicts."""
    v = _V()
    _check_spec(v, spec, where)
    return v.issues


def _check_spec(v: _V, spec, w: str) -> dict:
    """Returns asset info {dofs, parts, anchors} for reference checks."""
    info = {"dofs": {}, "parts": set(), "anchors": set()}
    if not _obj(v, spec, w):
        return info
    sid = spec.get("id")
    if not (isinstance(sid, str) and ID_RE.match(sid)):
        v.add("bad_id", f"{w}.id 应为小写字母开头的 [a-z0-9_]：{sid!r}", f"{w}.id")
    if not isinstance(spec.get("name"), str) or not spec.get("name"):
        v.add("schema", f"{w}.name 应为中文名称字符串", f"{w}.name")
    if spec.get("version") != 1:
        v.add("schema", f"{w}.version 应为 1", f"{w}.version")
    if spec.get("units") != "m":
        v.add("schema", f'{w}.units 应为 "m"', f"{w}.units")
    # params
    params = spec.get("params", {})
    env = {}
    if _obj(v, params, f"{w}.params"):
        for k, p in params.items():
            pw = f"{w}.params.{k}"
            if not _ID_RE.fullmatch(k) or k in CONSTS:
                v.add("bad_id", f"参数名 {k!r} 不合法", pw)
            if not isinstance(p, dict) or not _is_num(p.get("value")):
                v.add("schema", f"{pw} 应为 {{value: 数字, ...}}", pw)
                continue
            env[k] = float(p["value"])
            for b in ("min", "max"):
                if b in p and not _is_num(p[b]):
                    v.add("schema", f"{pw}.{b} 应为数字", f"{pw}.{b}")
            if _is_num(p.get("min")) and _is_num(p.get("max")) and not (
                    p["min"] <= p["value"] <= p["max"]):
                v.add("schema", f"{pw}.value 不在 [min, max] 内", pw)
            _enum(v, p.get("unit"), ("m", "deg", ""), f"{pw}.unit")
    pnames = set(env)
    # dofs
    dofs = spec.get("dofs", {}) or {}
    if _obj(v, dofs, f"{w}.dofs"):
        for k, d in dofs.items():
            dw = f"{w}.dofs.{k}"
            if not _ID_RE.fullmatch(k):
                v.add("bad_id", f"自由度名 {k!r} 不合法", dw)
            if not isinstance(d, dict) or not all(_is_num(d.get(f)) for f in
                                                  ("min", "max", "default")):
                v.add("schema", f"{dw} 应为 {{min, max, default}}（数字）", dw)
                continue
            if not d["min"] <= d["default"] <= d["max"]:
                v.add("dof_range", f"{dw}.default 不在 [min, max] 内", dw)
            if k in pnames:
                v.add("duplicate_id", f"自由度 {k} 和参数重名", dw)
            info["dofs"][k] = d
            env[k] = float(d["default"])
    # drivers (ordered)
    drivers = spec.get("drivers", {}) or {}
    known = pnames | set(info["dofs"])
    if _obj(v, drivers, f"{w}.drivers"):
        for k, e in drivers.items():
            dw = f"{w}.drivers.{k}"
            if not isinstance(e, str):
                v.add("schema", f"{dw} 应为表达式字符串", dw)
                continue
            if k in known:
                v.add("duplicate_id", f"驱动量 {k} 和参数或自由度重名", dw)
            val = _expr(v, e, known, dw, env)
            if val is not None:
                env[k] = val
            known.add(k)
    joint_names = known  # params + dofs + drivers
    # dims
    dims = spec.get("dims")
    if _obj(v, dims, f"{w}.dims"):
        size = []
        for f in ("w", "h", "d"):
            if f not in dims:
                v.add("schema", f"缺少 {w}.dims.{f}", f"{w}.dims")
            else:
                x = _expr(v, dims[f], pnames, f"{w}.dims.{f}", env)
                if x is not None and x <= 0:
                    v.add("bad_shape_param", f"{w}.dims.{f} 应大于 0", f"{w}.dims.{f}")
                size.append(x)
        if len(size) == 3 and all(x is not None and x > 0 for x in size):
            # shortcut: assumes the rest pose is centred on x/z and stands on y = 0 (what the
            # builder's floating / below_ground checks enforce for y); good enough for the
            # linked_parts distance test, not for anything that needs the real bbox
            info["bbox"] = ([-size[0] / 2, 0.0, -size[2] / 2], [size[0] / 2, size[1], size[2] / 2])
        if "tol" in dims and not (_is_num(dims["tol"]) and dims["tol"] > 0):
            v.add("schema", f"{w}.dims.tol 应为正数", f"{w}.dims.tol")
    # parts
    parts = spec.get("parts")
    parents = {}
    if not isinstance(parts, list) or not parts:
        v.add("schema", f"{w}.parts 应为非空数组", f"{w}.parts")
        parts = []
    for i, part in enumerate(parts):
        pw = f"{w}.parts[{i}]"
        if not _obj(v, part, pw):
            continue
        pid = part.get("id")
        if not (isinstance(pid, str) and ID_RE.match(pid)):
            v.add("bad_id", f"{pw}.id 不合法：{pid!r}", pw)
            continue
        pw = f"{w}.parts.{pid}"
        if pid in info["parts"]:
            v.add("duplicate_id", f"部件 id {pid} 重复", pw)
        info["parts"].add(pid)
        parents[pid] = part.get("parent")
    for i, part in enumerate(parts):
        if not isinstance(part, dict) or not isinstance(part.get("id"), str):
            continue
        _check_part(v, part, f"{w}.parts.{part['id']}", info["parts"], pnames, env,
                    joint_names)
    # parent chain cycles
    for pid in parents:
        seen, cur = set(), pid
        while cur is not None and cur in parents:
            if cur in seen:
                v.add("cycle", f"部件 {pid} 的父部件链有环", f"{w}.parts.{pid}")
                break
            seen.add(cur)
            cur = parents[cur]
    # anchors
    anchors = spec.get("anchors", {}) or {}
    if _obj(v, anchors, f"{w}.anchors"):
        for k, a in anchors.items():
            aw = f"{w}.anchors.{k}"
            info["anchors"].add(k)
            if isinstance(a, str):
                if a not in info["parts"]:
                    v.add("bad_anchor", f"锚点 {k} 指向不存在的部件 {a}", aw)
            elif isinstance(a, list):
                _vec3(v, a, pnames, aw, env)
            elif isinstance(a, dict):
                if a.get("part") not in info["parts"]:
                    v.add("bad_anchor", f"锚点 {k} 指向不存在的部件 {a.get('part')}", aw)
                _vec3(v, a.get("at"), pnames, f"{aw}.at", env)
            else:
                v.add("schema", f"{aw} 应为部件 id、[x,y,z] 或 {{part, at}}", aw)
    if "max_tris" in spec and not (_is_int(spec["max_tris"]) and spec["max_tris"] > 0):
        v.add("schema", f"{w}.max_tris 应为正整数", f"{w}.max_tris")
    return info


def _check_part(v, part, pw, part_ids, pnames, env, joint_names):
    shape = part.get("shape")
    if shape not in SHAPES:
        v.add("schema", f"{pw}.shape = {shape!r} 不是 {', '.join(SHAPES)} 之一", pw)
    parent = part.get("parent")
    if parent is not None and parent not in part_ids:
        v.add("unknown_parent", f"{pw} 的父部件 {parent} 不存在", pw)
    if parent == part.get("id"):
        v.add("cycle", f"{pw} 的父部件是它自己", pw)
    if "at" in part:
        _vec3(v, part["at"], pnames, f"{pw}.at", env)
    if "rot_deg" in part:
        _vec3(v, part["rot_deg"], pnames, f"{pw}.rot_deg", env)
    _color(v, part.get("color"), f"{pw}.color")
    if "opacity" in part and not (_is_num(part["opacity"]) and 0 <= part["opacity"] <= 1):
        v.add("schema", f"{pw}.opacity 应在 0..1", f"{pw}.opacity")
    vals = {}
    for f in NUM_FIELDS:
        if f in part:
            vals[f] = _expr(v, part[f], pnames, f"{pw}.{f}", env)
    if shape in SHAPE_REQ:
        for f in SHAPE_REQ[shape]:
            if f not in part:
                v.add("bad_shape_param", f"{shape} 部件缺少 {f}", pw)
    for f in ("r", "h", "r_outer", "tube", "depth"):
        if vals.get(f) is not None and vals[f] <= 0:
            v.add("bad_shape_param", f"{pw}.{f} 应大于 0", f"{pw}.{f}")
    if vals.get("r_inner") is not None and vals["r_inner"] < 0:
        v.add("bad_shape_param", f"{pw}.r_inner 不能为负", f"{pw}.r_inner")
    if (vals.get("r_inner") is not None and vals.get("r_outer") is not None
            and vals["r_inner"] >= vals["r_outer"]):
        v.add("bad_shape_param", f"{pw} 内径不小于外径", pw)
    if "size" in part:
        s = _vec3(v, part["size"], pnames, f"{pw}.size", env)
        if s and any(c <= 0 for c in s):
            v.add("bad_shape_param", f"{pw}.size 每个分量都应大于 0", f"{pw}.size")
    if "axis" in part:
        _enum(v, part["axis"], AXES, f"{pw}.axis")
    if "segments" in part and not (_is_int(part["segments"]) and part["segments"] >= 3):
        v.add("schema", f"{pw}.segments 应为 >= 3 的整数", f"{pw}.segments")
    if "profile" in part:
        prof = part["profile"]
        if not (isinstance(prof, list) and len(prof) >= (3 if shape == "extrude" else 2)
                and all(isinstance(q, list) and len(q) == 2 for q in prof)):
            v.add("bad_shape_param", f"{pw}.profile 应为 [[x,y], ...] 点列", f"{pw}.profile")
        else:
            for j, q in enumerate(prof):
                for c in q:
                    _expr(v, c, pnames, f"{pw}.profile[{j}]", env)
    j = part.get("joint")
    if j is not None:
        jw = f"{pw}.joint"
        if _obj(v, j, jw):
            _enum(v, j.get("type"), ("hinge", "slide"), f"{jw}.type", required=True)
            ax = j.get("axis")
            if not (ax in AXES or (isinstance(ax, list) and len(ax) == 3
                                   and all(_is_num(c) for c in ax) and any(ax))):
                v.add("schema", f"{jw}.axis 应为 x/y/z 或非零 [x,y,z]", jw)
            if j.get("dof") not in joint_names:
                v.add("unknown_dof", f"{jw}.dof {j.get('dof')!r} 不是自由度或驱动量", jw)
            if "scale" in j:
                _expr(v, j["scale"], joint_names, f"{jw}.scale")
    arr = part.get("array")
    if arr is not None:
        aw = f"{pw}.array"
        if _obj(v, arr, aw):
            _enum(v, arr.get("kind"), ("linear", "radial"), f"{aw}.kind", required=True)
            if not (_is_int(arr.get("count")) and arr["count"] >= 1):
                v.add("schema", f"{aw}.count 应为正整数", aw)
            if arr.get("kind") == "linear":
                _vec3(v, arr.get("step"), pnames, f"{aw}.step", env)
            elif arr.get("kind") == "radial":
                _enum(v, arr.get("axis"), AXES, f"{aw}.axis", required=True)
                if "angle_deg" in arr:
                    _expr(v, arr["angle_deg"], pnames, f"{aw}.angle_deg", env)


# ---------- library assets


def library_info(a: dict) -> dict:
    """{id, name, size, dofs, parts, anchors} from a library model's meta.json (ModelMeta plus
    the store's id/name) or an already-slim dict with those keys."""
    rig = a.get("rig") or {}
    dofs = a.get("dofs") if isinstance(a.get("dofs"), dict) else rig.get("dofs") or {}
    parts = a.get("parts") or []
    part_ids = [p["id"] if isinstance(p, dict) else str(p) for p in parts]
    anchors = a.get("anchors") or {}
    anchor_names = list(anchors) if isinstance(anchors, dict) else [str(x) for x in anchors]
    for k in (a.get("anchor_parts") or {}):
        if k not in anchor_names:
            anchor_names.append(k)
    out = {"id": a.get("id"), "name": a.get("name", ""), "size": a.get("size"),
           "dofs": dofs, "parts": part_ids, "anchors": anchor_names}
    src = a.get("source") or {}
    if isinstance(a.get("spec"), dict):  # slim dict that already carries the spec
        out["spec"] = a["spec"]
    elif src.get("type") == "parametric" and isinstance(src.get("spec"), dict):
        out["spec"] = src["spec"]
    bb = a.get("bbox")
    if isinstance(bb, dict) and isinstance(bb.get("min"), list) and isinstance(bb.get("max"), list):
        out["bbox"] = (list(bb["min"]), list(bb["max"]))
    return out


# ---------- extends: a parametric asset built on a library model's spec


def merge_spec(base: dict, spec: dict) -> dict:
    """The spec of a parametric asset that extends a library model (contract 5): the library
    model's spec with the plan's spec on top. params / dofs / drivers / anchors: merged by
    name (the plan wins, new drivers are evaluated after the base ones); parts: base parts in
    order, a plan part with the same id replaces it, new parts are appended; dims and
    max_tris: the plan's when given. id / name / version / units come from the plan's spec.
    Idempotent: merging an already merged spec again gives the same spec."""
    out = copy.deepcopy(base)
    spec = copy.deepcopy(spec)
    for k in ("params", "dofs", "drivers", "anchors"):
        if isinstance(spec.get(k), dict):
            m = dict(out.get(k) or {})
            m.update(spec[k])
            out[k] = m
    parts = [p for p in out.get("parts") or [] if isinstance(p, dict)]
    index = {p.get("id"): i for i, p in enumerate(parts)}
    for p in spec.get("parts") or []:
        if isinstance(p, dict) and p.get("id") in index:
            parts[index[p["id"]]] = p
        else:
            parts.append(p)
    out["parts"] = parts
    for k, v in spec.items():
        if k not in ("params", "dofs", "drivers", "anchors", "parts"):
            out[k] = v
    return out


def resolve_extends(plan, library_assets=()) -> list:
    """Replace the spec of every parametric asset with "extends": "<library id>" by the merged
    spec (merge_spec), in place. Returns issues for an unknown or non-parametric base."""
    v = _V()
    if not isinstance(plan, dict) or not isinstance(plan.get("assets"), list):
        return v.issues
    lib = {}
    for a in library_assets or ():
        info = library_info(a)
        if info["id"]:
            lib[info["id"]] = info
    for a in plan["assets"]:
        if not isinstance(a, dict) or "extends" not in a:
            continue
        w = f"assets.{a.get('id')}.extends"
        if a.get("kind") != "parametric":
            v.add("schema", f"{w}：只有 parametric 资产可以写 extends", w)
            continue
        base = lib.get(a["extends"]) if isinstance(a["extends"], str) else None
        if base is None:
            v.add("unknown_asset", f"{w}：素材库里没有 {a['extends']!r}", w)
        elif not isinstance(base.get("spec"), dict):
            v.add("unknown_asset", f"{w}：素材库模型 {a['extends']} 不是参数化资产，没有规格可以扩展", w)
        elif isinstance(a.get("spec"), dict):
            a["spec"] = merge_spec(base["spec"], a["spec"])
    return v.issues


# ---------- whole plan


def validate(plan, scenes, library_assets=()) -> list:
    """Every issue of a PrevisPlan against its storyboard (scenes: [{id, narration}, ...]).
    library_assets: library models the plan may refer to by "asset_..." id."""
    v = _V()
    if not _obj(v, plan, "plan"):
        return v.issues
    if any(isinstance(a, dict) and "extends" in a for a in plan.get("assets") or []):
        plan = copy.deepcopy(plan)  # check the merged specs without changing the caller's plan
        v.issues.extend(resolve_extends(plan, library_assets))
    if plan.get("version") != 1:
        v.add("schema", "plan.version 应为 1", "version")
    lib = {}
    for a in library_assets or ():
        info = library_info(a)
        if info["id"]:
            lib[info["id"]] = info
    # assets
    assets = {}  # "@id" or library id -> info
    alist = plan.get("assets")
    if not isinstance(alist, list):
        v.add("schema", "plan.assets 应为数组", "assets")
        alist = []
    for i, a in enumerate(alist):
        w = f"assets[{i}]"
        if not _obj(v, a, w):
            continue
        aid = a.get("id")
        if not (isinstance(aid, str) and ID_RE.match(aid)):
            v.add("bad_id", f"{w}.id 不合法：{aid!r}", w)
            continue
        w = f"assets.{aid}"
        if f"@{aid}" in assets:
            v.add("duplicate_id", f"资产 id {aid} 重复", w)
        kind = a.get("kind")
        if kind == "parametric":
            info = _check_spec(v, a.get("spec"), f"{w}.spec")
            if isinstance(a.get("spec"), dict) and a["spec"].get("id") != aid:
                v.add("schema", f"{w}.spec.id 应等于资产 id {aid}", f"{w}.spec.id")
        elif kind == "image3d":
            if not isinstance(a.get("prompt"), str) or not a["prompt"].strip():
                v.add("schema", f"{w}.prompt 应为非空字符串", f"{w}.prompt")
            t = a.get("target")
            if t is None:
                v.add("missing_target", f"图生 3D 资产 {aid} 必须写 target（例如 "
                      '{"axis": "h", "metres": 0.3}）', f"{w}.target")
            elif _obj(v, t, f"{w}.target"):
                _enum(v, t.get("axis"), ("w", "h", "d", "longest"), f"{w}.target.axis",
                      required=True)
                if not (_is_num(t.get("metres")) and t["metres"] > 0):
                    v.add("schema", f"{w}.target.metres 应为正数（米）", f"{w}.target.metres")
            if "image_asset" in a and not (isinstance(a["image_asset"], str)
                                           and LIB_ID_RE.match(a["image_asset"])):
                v.add("schema", f"{w}.image_asset 应为素材库 id", f"{w}.image_asset")
            info = {"dofs": {}, "parts": {"body"}, "anchors": set()}
        elif kind == "library":
            ref = a.get("asset")
            if ref not in lib:
                v.add("unknown_asset", f"素材库里没有 {ref!r}", f"{w}.asset")
                info = {"dofs": {}, "parts": set(), "anchors": set(), "unknown": True}
            else:
                info = _lib_ref_info(lib[ref])
        else:
            v.add("schema", f"{w}.kind 应为 parametric / image3d / library", f"{w}.kind")
            info = {"dofs": {}, "parts": set(), "anchors": set(), "unknown": True}
        assets[f"@{aid}"] = info
    for lid, li in lib.items():
        assets.setdefault(lid, _lib_ref_info(li))
    # sets
    sets = {}
    slist = plan.get("sets")
    if not isinstance(slist, list) or not slist:
        v.add("schema", "plan.sets 应为非空数组", "sets")
        slist = []
    for i, s in enumerate(slist):
        w = f"sets[{i}]"
        if not _obj(v, s, w):
            continue
        sid = s.get("id")
        if not isinstance(sid, str) or not sid:
            v.add("bad_id", f"{w}.id 应为非空字符串", w)
            continue
        w = f"sets.{sid}"
        if sid in sets:
            v.add("duplicate_id", f"布景 id {sid} 重复", w)
        sets[sid] = _check_set(v, s, w, assets)
    # shots
    scene_list = [s for s in (scenes or []) if isinstance(s, dict)]
    scene_ids = [s.get("id") for s in scene_list]
    narr = {s.get("id"): str(s.get("narration", "")) for s in scene_list}
    shots = plan.get("shots")
    if not isinstance(shots, list):
        v.add("schema", "plan.shots 应为数组", "shots")
        shots = []
    seen = {}
    for i, sh in enumerate(shots):
        w = f"shots[{i}]"
        if not _obj(v, sh, w):
            continue
        shid = sh.get("id")
        w = f"shots.{shid}"
        if shid in seen:
            v.add("duplicate_id", f"镜头 {shid} 出现了不止一次（每段只能一个镜头）", w)
        seen[shid] = True
        if shid not in narr:
            v.add("extra_shot", f"镜头 {shid} 没有对应的分镜段（分镜段 id：{', '.join(map(str, scene_ids))}）", w)
        _check_shot(v, sh, w, sets, narr.get(shid))
    for sid in scene_ids:
        if sid not in seen:
            v.add("missing_shot", f"分镜段 {sid} 没有镜头", f"shots.{sid}")
    _check_linked(v, slist, shots, assets)
    return v.issues


def _lib_ref_info(li: dict) -> dict:
    info = {"dofs": li["dofs"], "parts": set(li["parts"]), "anchors": set(li["anchors"])}
    if li.get("bbox"):
        info["bbox"] = li["bbox"]
    elif isinstance(li.get("size"), list) and len(li["size"]) == 3:
        w, h, d = (float(x) for x in li["size"])
        info["bbox"] = ([-w / 2, 0.0, -d / 2], [w / 2, h, d / 2])
    return info


LINK_GAP = 0.05  # metres: objects closer than this (or half the smaller one's size) are "near"
ROTARY_SPAN = 360  # a DOF whose range spans a full turn or more is a free rotation


def _world_box(o: dict, info: dict):
    """Set-space AABB of a set object from its asset's rest-pose box; None when unknown.
    shortcut: any rotation is covered by the box's largest horizontal half extent, so the box
    is never too small; upgrade to rotated corners if near-misses matter."""
    bb = info.get("bbox")
    at = o.get("at") or [0, 0, 0]
    if not bb or not (isinstance(at, list) and len(at) == 3 and all(_is_num(c) for c in at)):
        return None
    lo, hi = [float(c) for c in bb[0]], [float(c) for c in bb[1]]
    if any(_is_num(c) and c for c in (o.get("rot_deg") or [])):
        r = max(abs(lo[0]), abs(hi[0]), abs(lo[2]), abs(hi[2]))
        lo, hi = [-r, lo[1], -r], [r, hi[1], r]
    s = o.get("scale", 1) if _is_num(o.get("scale", 1)) else 1
    return ([at[k] + s * lo[k] for k in range(3)], [at[k] + s * hi[k] for k in range(3)])


def _box_gap(a, b) -> float:
    return math.sqrt(sum(max(0.0, b[0][k] - a[1][k], a[0][k] - b[1][k]) ** 2 for k in range(3)))


def _rotary(d) -> bool:
    return (isinstance(d, dict) and _is_num(d.get("min")) and _is_num(d.get("max"))
            and d["max"] - d["min"] >= ROTARY_SPAN)


def _check_linked(v: _V, slist, shots, assets):
    """linked_parts (warn): parts that move together drawn as separate set objects. Fires when,
    in one shot, object B (an asset with exactly one DOF, a free rotation of a full turn or
    more) is posed or spun while another object A of the same set is posed or spun on a free
    rotation DOF too, and their boxes are less than max(LINK_GAP, half of B's largest size)
    apart. A flywheel on its own stand beside an engine whose crank turns, or two separate
    gears, are the cases this catches; two machines far apart, a turntable next to something
    static, or a door swinging next to a turning crank are not flagged."""
    sets = {}
    for s in slist:
        if isinstance(s, dict) and isinstance(s.get("objects"), list):
            objs = {}
            for o in s["objects"]:
                if isinstance(o, dict) and isinstance(o.get("asset"), str) and o.get("id"):
                    info = assets.get(o["asset"])
                    if info and not info.get("unknown"):
                        objs[o["id"]] = (o, info)
            sets[s.get("id")] = objs
    flagged = set()
    for sh in shots:
        if not isinstance(sh, dict) or sh.get("set") not in sets:
            continue
        objs = sets[sh["set"]]
        moved = {}  # object id -> DOFs posed / spun in this shot
        for a in sh.get("actions") or []:
            if isinstance(a, dict) and a.get("op") in ("pose", "spin") and a.get("target") in objs:
                moved.setdefault(a["target"], set()).add(a.get("dof"))
        # the add-on (fewer parts) is named first when both objects qualify
        for b_id, b_dofs in sorted(moved.items(), key=lambda kv: len(objs[kv[0]][1]["parts"])):
            ob, ib = objs[b_id]
            dofs = ib["dofs"]
            if len(dofs) != 1:
                continue
            (name, d), = dofs.items()
            if name not in b_dofs or not _rotary(d):
                continue
            bb = _world_box(ob, ib)
            if bb is None:
                continue
            reach = max(LINK_GAP, 0.5 * max(bb[1][k] - bb[0][k] for k in range(3)))
            for a_id in moved:
                if a_id == b_id or frozenset((b_id, a_id)) in flagged:
                    continue
                ba = _world_box(*objs[a_id])
                if ba is None or not any(_rotary(objs[a_id][1]["dofs"].get(n))
                                         for n in moved[a_id]):
                    continue
                gap = _box_gap(ba, bb)
                if gap < reach:
                    flagged.add(frozenset((b_id, a_id)))
                    v.add("linked_parts",
                          f"镜头 {sh.get('id')}：物体 {b_id}（只有一个转动自由度 {name}）和同样在动的物体 "
                          f"{a_id} 相距只有 {gap * 100:.0f} cm，看起来是一起运动的部件却是两个独立物体、"
                          f"各有各的自由度。把 {b_id} 的部件做进 {a_id} 的资产（参数化资产可以用 extends "
                          f"扩展素材库模型），用关节接到同一个自由度或它的驱动量上",
                          f"sets.{sh['set']}.objects.{b_id}", severity="warn")

def _check_set(v: _V, s: dict, w: str, assets: dict) -> dict:
    """Returns {objects: {id: asset info}, marks: set}."""
    out = {"objects": {}, "marks": set()}
    if s.get("units") != "m":
        v.add("schema", f'{w}.units 应为 "m"', f"{w}.units")
    if "background" in s and not (isinstance(s["background"], str)
                                  and COLOR_RE.match(s["background"])):
        v.add("schema", f"{w}.background 应为 #rrggbb", f"{w}.background")
    g = s.get("ground")
    if g is not None and _obj(v, g, f"{w}.ground"):
        _enum(v, g.get("kind"), ("grid", "plain", "none"), f"{w}.ground.kind", required=True)
        if "size" in g and not (_is_num(g["size"]) and g["size"] > 0):
            v.add("schema", f"{w}.ground.size 应为正数", f"{w}.ground.size")
        if "color" in g and not (isinstance(g["color"], str) and COLOR_RE.match(g["color"])):
            v.add("schema", f"{w}.ground.color 应为 #rrggbb", f"{w}.ground.color")
    _enum(v, s.get("lights"), ("studio", "top", "dramatic"), f"{w}.lights")
    objs = s.get("objects")
    if not isinstance(objs, list) or not objs:
        v.add("schema", f"{w}.objects 应为非空数组", f"{w}.objects")
        objs = []
    for i, o in enumerate(objs):
        ow = f"{w}.objects[{i}]"
        if not _obj(v, o, ow):
            continue
        oid = o.get("id")
        if not (isinstance(oid, str) and ID_RE.match(oid)):
            v.add("bad_id", f"{ow}.id 不合法：{oid!r}", ow)
            continue
        ow = f"{w}.objects.{oid}"
        if oid in out["objects"]:
            v.add("duplicate_id", f"物体 id {oid} 重复", ow)
        ref = o.get("asset")
        info = assets.get(ref) if isinstance(ref, str) else None
        if info is None:
            hint = "（同一规划里的资产写成 @<资产 id>）" if isinstance(ref, str) and \
                f"@{ref}" in assets else ""
            v.add("unknown_asset", f"物体 {oid} 引用的资产 {ref!r} 不存在{hint}", f"{ow}.asset")
            info = {"dofs": {}, "parts": set(), "anchors": set(), "unknown": True}
        out["objects"][oid] = info
        for f in ("at", "rot_deg"):
            if f in o:
                _vec3(v, o[f], set(), f"{ow}.{f}", {})
        if "scale" in o:
            if not (_is_num(o["scale"]) and o["scale"] > 0):
                v.add("schema", f"{ow}.scale 应为正数（统一缩放）", f"{ow}.scale")
            elif o["scale"] != 1:
                v.add("scale_not_1", f"物体 {oid} 的 scale = {o['scale']}，真实尺寸会被破坏",
                      f"{ow}.scale", severity="warn")
        _color(v, o.get("color"), f"{ow}.color")
        pose = o.get("pose")
        if pose is not None and _obj(v, pose, f"{ow}.pose"):
            for k, x in pose.items():
                _dof_value(v, info, k, x, f"{ow}.pose.{k}", oid)
    marks = s.get("marks", {}) or {}
    if _obj(v, marks, f"{w}.marks"):
        for k, m in marks.items():
            mw = f"{w}.marks.{k}"
            if k in out["objects"]:
                v.add("duplicate_id", f"标记点 {k} 和物体重名", mw)
            out["marks"].add(k)
            if isinstance(m, list):
                _vec3(v, m, set(), mw, {})
            elif isinstance(m, str):
                _point_str(v, m, out, mw, allow_marks=False)
            else:
                v.add("schema", f"{mw} 应为 [x,y,z]、\"物体\" 或 \"物体.锚点\"", mw)
    return out


def _dof_value(v, info, k, x, where, oid):
    if info.get("unknown"):
        return
    d = info["dofs"].get(k)
    if d is None:
        v.add("unknown_dof", f"物体 {oid} 没有自由度 {k}（可用：{', '.join(info['dofs']) or '无'}）",
              where)
    elif not _is_num(x):
        v.add("schema", f"{where} 应为数字", where)
    elif not d["min"] <= x <= d["max"]:
        v.add("dof_range", f"物体 {oid} 的 {k} = {x} 超出范围 [{d['min']}, {d['max']}]", where)


def _point_str(v, p: str, st: dict, where: str, allow_marks=True):
    if "." in p:
        oid, _, anc = p.partition(".")
        info = st["objects"].get(oid)
        if info is None:
            v.add("unknown_point", f"点 {p!r}：没有物体 {oid}", where)
        elif not info.get("unknown") and anc not in info["anchors"]:
            v.add("unknown_point", f"点 {p!r}：物体 {oid} 没有锚点 {anc}（可用：{', '.join(sorted(info['anchors'])) or '无'}）", where)
        return
    if p in st["objects"] or (allow_marks and p in st["marks"]):
        return
    v.add("unknown_point", f"点 {p!r} 既不是标记点也不是物体", where)


def _point(v, p, st, where):
    if isinstance(p, str):
        _point_str(v, p, st, where)
    elif isinstance(p, list):
        _vec3(v, p, set(), where, {})
    else:
        v.add("schema", f"{where} 应为 [x,y,z]、标记点名、\"物体\" 或 \"物体.锚点\"", where)


def _target(v, t, st, where, allow_part: bool):
    if not isinstance(t, str):
        v.add("schema", f"{where} 应为物体 id 字符串", where)
        return None
    oid, dot, part = t.partition(".")
    info = st["objects"].get(oid)
    if info is None:
        v.add("unknown_target", f"目标 {t!r}：布景里没有物体 {oid}", where)
        return None
    if dot:
        if not allow_part:
            v.add("unknown_target", f"目标 {t!r} 这里只能是物体，不能是部件", where)
        elif not info.get("unknown") and part not in info["parts"]:
            v.add("unknown_target", f"目标 {t!r}：物体 {oid} 没有部件 {part}", where)
    return info


def _time(v, a, where, narration):
    if not _obj(v, a, where):
        return
    keys = [k for k in ("t", "frame", "word", "mark") if k in a]
    if len(keys) != 1:
        v.add("bad_time", f"{where} 必须正好写 t / frame / word / mark 中的一个（现在是 {keys or '无'}）", where)
        return
    k = keys[0]
    if k == "t" and not (_is_num(a["t"]) and a["t"] >= 0):
        v.add("bad_time", f"{where}.t 应为 >= 0 的秒数", where)
    if k == "frame" and not (_is_int(a["frame"]) and a["frame"] >= 0):
        v.add("bad_time", f"{where}.frame 应为 >= 0 的整数", where)
    if k == "mark":
        _enum(v, a["mark"], ("start", "end"), f"{where}.mark")
    if "offset" in a and not _is_num(a["offset"]):
        v.add("bad_time", f"{where}.offset 应为秒数", where)
    if "edge" in a:
        _enum(v, a["edge"], ("start", "end"), f"{where}.edge")
    occ = a.get("occurrence", 1)
    if not (_is_int(occ) and occ >= 1):
        v.add("bad_time", f"{where}.occurrence 应为 >= 1 的整数", where)
        occ = 1
    if k == "word":
        word = a["word"]
        if not isinstance(word, str) or not strip_punct(word):
            v.add("bad_time", f"{where}.word 应为旁白里的一串字", where)
        elif narration is not None:
            n = count_occurrences(narration, word)
            if n < occ:
                msg = (f"旁白里找不到「{word}」" if n == 0
                       else f"「{word}」在旁白里只出现 {n} 次，没有第 {occ} 次")
                v.add("anchor_not_found", f"{where}：{msg}", where)
    elif "occurrence" in a or "edge" in a:
        v.add("bad_time", f"{where}：occurrence / edge 只用于 word 锚点", where,
              severity="warn")


def _time_pos(a, narration):
    """Order of a TimeAnchor inside the scene without knowing durations: -1 for mark start,
    the character index for a word (edge end: its last character + 0.5), inf for mark end;
    None when it cannot be placed (t, frame, an offset, a word that is not found)."""
    if not isinstance(a, dict) or a.get("offset"):
        return None
    if a.get("mark") == "start" and len(a) == 1:
        return -1.0
    if a.get("mark") == "end" and len(a) == 1:
        return math.inf
    word = a.get("word")
    if isinstance(word, str) and narration is not None and strip_punct(word):
        t, wd = strip_punct(narration), strip_punct(word)
        occ = a.get("occurrence", 1)
        i = -1
        for _ in range(occ if _is_int(occ) and occ >= 1 else 1):
            i = t.find(wd, i + 1)
            if i < 0:
                return None
        return i + len(wd) - 0.5 if a.get("edge") == "end" else float(i)
    return None


def _check_shot(v: _V, sh: dict, w: str, sets: dict, narration):
    st = sets.get(sh.get("set"))
    if st is None:
        v.add("unknown_set", f"镜头引用的布景 {sh.get('set')!r} 不存在", f"{w}.set")
        st = {"objects": {}, "marks": set()}
        skip_refs = True
    else:
        skip_refs = False
    cam = sh.get("camera")
    keys = []
    if _obj(v, cam, f"{w}.camera"):
        keys = cam.get("keys")
        _enum(v, cam.get("path"), ("catmull", "linear"), f"{w}.camera.path")
        if not isinstance(keys, list):
            v.add("schema", f"{w}.camera.keys 应为数组", f"{w}.camera.keys")
            keys = []
    if len(keys) < 2:
        v.add("too_few_keys", f"镜头 {sh.get('id')} 只有 {len(keys)} 个相机关键帧，至少要 2 个",
              f"{w}.camera.keys")
    for i, k in enumerate(keys):
        kw = f"{w}.camera.keys[{i}]"
        if not _obj(v, k, kw):
            continue
        _time(v, k.get("at"), f"{kw}.at", narration)
        has_pos, has_orbit = "pos" in k, "orbit" in k
        if has_pos == has_orbit:
            v.add("schema", f"{kw} 必须正好写 pos 或 orbit 之一", kw)
        if has_pos and not skip_refs:
            _point(v, k["pos"], st, f"{kw}.pos")
        if has_orbit and _obj(v, k["orbit"], f"{kw}.orbit"):
            o = k["orbit"]
            if not skip_refs:
                _point(v, o.get("around"), st, f"{kw}.orbit.around")
            if not (_is_num(o.get("r")) and o["r"] > 0):
                v.add("schema", f"{kw}.orbit.r 应为正数（米）", f"{kw}.orbit.r")
            for f in ("az", "el"):
                if not _is_num(o.get(f)):
                    v.add("schema", f"{kw}.orbit.{f} 应为角度（度）", f"{kw}.orbit.{f}")
            if _is_num(o.get("el")) and not -90 < o["el"] < 90:
                v.add("schema", f"{kw}.orbit.el 应在 (-90, 90) 内", f"{kw}.orbit.el")
        if "look" not in k:
            v.add("schema", f"{kw} 缺少 look", kw)
        elif not skip_refs:
            _point(v, k["look"], st, f"{kw}.look")
        if "lens_mm" in k and not (_is_num(k["lens_mm"]) and 8 <= k["lens_mm"] <= 400):
            v.add("schema", f"{kw}.lens_mm 应为 8..400 的数字", f"{kw}.lens_mm")
        if "roll" in k and not _is_num(k["roll"]):
            v.add("schema", f"{kw}.roll 应为角度（度）", f"{kw}.roll")
        _enum(v, k.get("ease"), EASES, f"{kw}.ease")
        if "cut" in k and not isinstance(k["cut"], bool):
            v.add("schema", f"{kw}.cut 应为 true/false", f"{kw}.cut")
    prev = None
    for i, k in enumerate(keys):
        pos = _time_pos(k.get("at") if isinstance(k, dict) else None, narration)
        if pos is None:
            continue
        if prev is not None and pos <= prev[1]:
            v.add("time_order", f"{w}.camera.keys[{i}] 的时间不晚于 keys[{prev[0]}]，"
                  "相机关键帧要按时间顺序排列，且不能同时", f"{w}.camera.keys[{i}].at")
        prev = (i, pos)
    actions = sh.get("actions", []) or []
    if not isinstance(actions, list):
        v.add("schema", f"{w}.actions 应为数组", f"{w}.actions")
        actions = []
    for i, a in enumerate(actions):
        aw = f"{w}.actions[{i}]"
        if not _obj(v, a, aw):
            continue
        op = a.get("op")
        if op not in OPS:
            v.add("schema", f"{aw}.op = {op!r} 不是 {', '.join(OPS)} 之一", aw)
            continue
        _time(v, a.get("at"), f"{aw}.at", narration)
        if "until" in a:
            if op in ("show", "hide"):
                v.add("schema", f"{aw}：{op} 没有 until", aw)
            _time(v, a["until"], f"{aw}.until", narration)
            p0, p1 = _time_pos(a.get("at"), narration), _time_pos(a["until"], narration)
            if p0 is not None and p1 is not None and p1 <= p0:
                v.add("time_order", f"{aw}：until 不晚于 at", f"{aw}.until")
        if "ease" in a:
            _enum(v, a["ease"], EASES, f"{aw}.ease")
        if skip_refs:
            continue
        if op in ("pose", "spin"):
            info = _target(v, a.get("target"), st, f"{aw}.target", allow_part=False)
            dof = a.get("dof")
            if info is not None and not info.get("unknown"):
                if dof not in info["dofs"]:
                    v.add("unknown_dof", f"{aw}：物体没有自由度 {dof!r}（可用：{', '.join(info['dofs']) or '无'}）", f"{aw}.dof")
                elif op == "pose":
                    _dof_value(v, info, dof, a.get("to"), f"{aw}.to", a.get("target"))
            if op == "spin" and not _is_num(a.get("deg_per_s")):
                v.add("schema", f"{aw}.deg_per_s 应为数字", aw)
            if op == "pose" and not _is_num(a.get("to")):
                v.add("schema", f"{aw}.to 应为数字", aw)
        elif op == "move":
            _target(v, a.get("target"), st, f"{aw}.target", allow_part=False)
            _vec3(v, a.get("to"), set(), f"{aw}.to", {})
            if "rot_deg" in a:
                _vec3(v, a["rot_deg"], set(), f"{aw}.rot_deg", {})
        elif op in ("show", "hide", "highlight"):
            _target(v, a.get("target"), st, f"{aw}.target", allow_part=True)
            if op == "highlight":
                _color(v, a.get("color"), f"{aw}.color")
            if "fade" in a and not (_is_num(a["fade"]) and a["fade"] >= 0):
                v.add("schema", f"{aw}.fade 应为 >= 0 的秒数", aw)
        elif op == "explode":
            _target(v, a.get("target"), st, f"{aw}.target", allow_part=False)
            if not (_is_num(a.get("factor")) and a["factor"] >= 0):
                v.add("schema", f"{aw}.factor 应为 >= 0 的数字", aw)
        elif op == "label":
            if not isinstance(a.get("text"), str) or not a["text"]:
                v.add("schema", f"{aw}.text 应为非空字符串", aw)
            _point(v, a.get("attach"), st, f"{aw}.attach")
            _enum(v, a.get("side"), ("left", "right", "top"), f"{aw}.side")
        elif op == "dimension":
            _point(v, a.get("from"), st, f"{aw}.from")
            _point(v, a.get("to"), st, f"{aw}.to")
            if "text" in a and not isinstance(a["text"], str):
                v.add("schema", f"{aw}.text 应为字符串", aw)
        elif op == "section":
            ts = a.get("targets")
            if not (isinstance(ts, list) and ts):
                v.add("schema", f"{aw}.targets 应为非空数组", aw)
            else:
                for j, t in enumerate(ts):
                    _target(v, t, st, f"{aw}.targets[{j}]", allow_part=True)
            n = a.get("normal")
            if not (isinstance(n, list) and len(n) == 3 and all(_is_num(c) for c in n)
                    and any(n)):
                v.add("schema", f"{aw}.normal 应为非零 [x,y,z]", aw)
            if not _is_num(a.get("offset")):
                v.add("schema", f"{aw}.offset 应为数字（米）", aw)
    style = sh.get("style")
    if style is not None and _obj(v, style, f"{w}.style"):
        _enum(v, style.get("look"), ("clay", "flat", "wire"), f"{w}.style.look")
        for f in ("shadows", "outline"):
            if f in style and not isinstance(style[f], bool):
                v.add("schema", f"{w}.style.{f} 应为 true/false", f"{w}.style.{f}")
        for f in ("fade_in", "fade_out"):
            if f in style and not (_is_num(style[f]) and style[f] >= 0):
                v.add("schema", f"{w}.style.{f} 应为 >= 0 的秒数", f"{w}.style.{f}")


def errors(issues: list) -> list:
    return [i for i in issues if i.get("severity") == "error"]
