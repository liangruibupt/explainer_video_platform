"""plan_schema: the expr.ts port agrees with the TypeScript one, and validate() accepts the
four_stroke fixture and rejects each broken variant with the right code."""
import copy
import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.previs import plan_schema as ps

EX = Path(__file__).resolve().parents[1] / "app" / "previs" / "examples" / "four_stroke"
CORE = Path(__file__).resolve().parents[1] / "app" / "previs" / "js" / "core"
PLAN = json.loads((EX / "plan.json").read_text())
SCENES = json.loads((EX / "scenes.json").read_text())


def codes(plan, scenes=SCENES, lib=()):
    return [i["code"] for i in ps.validate(plan, scenes, lib)]


def errs(plan, scenes=SCENES, lib=()):
    return [i["code"] for i in ps.validate(plan, scenes, lib) if i["severity"] == "error"]


def mutate(fn):
    p = copy.deepcopy(PLAN)
    fn(p)
    return p


# ------------------------------------------------------------- expressions (core/core.test.ts)

def near(a, b, eps=1e-9):
    assert abs(a - b) < eps, (a, b)


def test_expr_arithmetic_and_precedence():
    E = ps.evaluate
    near(E("1 + 2 * 3", {}), 7)
    near(E("(1 + 2) * 3", {}), 9)
    near(E("2 ^ 3 ^ 2", {}), 512)
    near(E("-2 ^ 2", {}), -4)
    near(E("bore/2 + 0.01", {"bore": 0.08}), 0.05)
    near(E("1e-3 * 2", {}), 0.002)
    near(E(0.25, {}), 0.25)


def test_expr_degree_trig_and_functions():
    E = ps.evaluate
    near(E("sin(90)", {}), 1)
    near(E("cos(180)", {}), -1)
    near(E("atan2(1, 1)", {}), 45)
    near(E("clamp(5, 0, 2)", {}), 2)
    near(E("max(1, 4, 2)", {}), 4)
    near(E("pi", {}), math.pi)
    near(E("mod(-90, 720)", {}), 630)
    near(E("mod(800, 720)", {}), 80)


def test_expr_errors():
    for bad in ["1 +", "foo", "nope(1)", "sin(1,2)", "1 / 0", "2 $ 3", "(1"]:
        with pytest.raises(ps.ExprError):
            ps.evaluate(bad, {})
    assert sorted(ps.names("bore/2 + sin(crank) * pi")) == ["bore", "crank"]


def test_four_stroke_drivers_finite_and_valve_timing():
    spec = PLAN["assets"][0]["spec"]
    env0 = {k: p["value"] for k, p in spec["params"].items()}
    for c in range(-1440, 1441, 5):
        env = dict(env0, crank=c)
        for k, e in spec["drivers"].items():
            env[k] = ps.evaluate(e, env)
        cyc = c % 720
        if 5 < cyc < 175:
            assert env["intake_lift"] > 0 and env["exhaust_lift"] == 0
        if 545 < cyc < 715:
            assert env["exhaust_lift"] > 0 and env["intake_lift"] == 0


# Cases run through BOTH implementations; results must agree (value to 1e-12, or both throw).
AGREE = [
    "1 + 2 * 3", "(1 + 2) * 3", "2 ^ 3 ^ 2", "-2 ^ 2", "bore/2 + 0.01", "1e-3 * 2",
    "sin(90)", "cos(180)", "atan2(1, 1)", "clamp(5, 0, 2)", "max(1, 4, 2)", "pi",
    "mod(-90, 720)", "mod(800, 720)", "1 +", "foo", "nope(1)", "sin(1,2)", "1 / 0", "2 $ 3",
    "(1", "-(-3)", "--2", "2^-1", "+5", "round(2.5)", "round(-2.5)", "round(-0.4)", "floor(-1.5)",
    "ceil(1.2)", "sqrt(-1)", "asin(2)", "min(1, 1/0)", "max(0, sqrt(-1))", "abs(-3) * 2",
    "acos(0.5)", "tan(45)", "atan(1)", "mod(5, 0)", "1.5e2 + .5", "2 3", "min()", "sin()",
    "clamp(1,2)", "0 ^ -1", "1 ^ (1/0)", "(-8) ^ (1/3)", "1e400", "r*cos(crank) + sqrt(rod^2 - (r*sin(crank))^2) - (r + rod)",
    "lift*max(0, sin(cycle))*clamp((360 - cycle)*1000, 0, 1)", "-asin(r*sin(crank)/rod)",
    "2 * (3 + 4) ^ 2 / 7 - 1", "1 - - 1", "4 / 2 / 2", "2 ^ 0.5 ^ 2", "a_b + _c", "1..2", ".",
]
ENV = {"bore": 0.08, "r": 0.045, "rod": 0.16, "crank": 37.0, "lift": 0.008, "cycle": 100.0,
       "a_b": 2.0, "_c": 3.0}


def _py(expr):
    try:
        return {"v": ps.evaluate(expr, ENV)}
    except ps.ExprError:
        return {"err": True}


NM = Path(os.environ.get("EVP_TOOLS", Path.home() / ".cache" / "explainer-video")) \
    / "remotion-base" / "node_modules"


@pytest.mark.skipif(not (NM / ".bin" / "esbuild").exists() or not shutil.which("node"),
                    reason="needs node + the shared esbuild install")
def test_expr_agrees_with_typescript(tmp_path):
    entry = tmp_path / "agree.ts"
    entry.write_text(
        f'import {{ evaluate, names }} from {json.dumps(str(CORE / "expr.ts"))};\n'
        f"const cases: string[] = {json.dumps(AGREE)};\n"
        f"const env = {json.dumps(ENV)};\n"
        "const out = cases.map((c) => { try { return { v: evaluate(c, env) }; }"
        " catch (e) { return { err: true }; } });\n"
        "const nm = cases.map((c) => { try { return names(c).sort(); } catch (e) { return null; } });\n"
        "console.log(JSON.stringify({ out, nm }));\n")
    bundle = tmp_path / "agree.mjs"
    subprocess.run([str(NM / ".bin" / "esbuild"), str(entry), "--bundle", "--platform=node",
                    "--format=esm", "--log-level=warning", f"--outfile={bundle}"], check=True)
    res = json.loads(subprocess.run(["node", str(bundle)], check=True, capture_output=True,
                                    text=True).stdout)
    assert len(res["out"]) == len(AGREE)
    for expr, ts, nm in zip(AGREE, res["out"], res["nm"]):
        py = _py(expr)
        assert ("err" in py) == ("err" in ts), (expr, py, ts)
        if "v" in ts:
            assert abs(py["v"] - ts["v"]) <= 1e-12 * max(1, abs(ts["v"])), (expr, py, ts)
        try:
            pn = sorted(ps.names(expr))
        except ps.ExprError:
            pn = None
        assert pn == nm, (expr, pn, nm)


# ------------------------------------------------------------- the validator

def test_fixture_is_valid():
    assert ps.validate(PLAN, SCENES) == []


def test_word_anchor_ignores_punctuation_and_counts_occurrences():
    n = SCENES[1]["narration"]  # 第一步进气，活塞下行，…第二步压缩，活塞上行…
    assert ps.count_occurrences(n, "活塞") == 2
    assert ps.count_occurrences(n, "进气，活塞") == 1
    assert ps.count_occurrences(n, "气活") == 1  # punctuation is not in words.json either
    ok = mutate(lambda p: p["shots"][1]["actions"].append(
        {"op": "label", "text": "活塞", "attach": "eng.piston_top",
         "at": {"word": "活塞", "occurrence": 2}}))
    assert errs(ok) == []
    bad = mutate(lambda p: p["shots"][1]["actions"].append(
        {"op": "label", "text": "活塞", "attach": "eng.piston_top",
         "at": {"word": "活塞", "occurrence": 3}}))
    assert errs(bad) == ["anchor_not_found"]


def _lib():
    return [{"id": "asset_20261125-010203-abcdef", "name": "齿轮", "size": [0.2, 0.2, 0.05],
             "rig": {"dofs": {"turn": {"min": -360, "max": 360, "default": 0}}},
             "anchors": {"hub": [0, 0.1, 0]}, "parts": [{"id": "body"}]}]


S = lambda i: (lambda p: p["shots"][i])  # noqa: E731
SPEC = lambda p: p["assets"][0]["spec"]  # noqa: E731
PARTS = lambda p: {x["id"]: x for x in SPEC(p)["parts"]}  # noqa: E731

BROKEN = [
    ("version", lambda p: p.update(version=2), "schema"),
    ("unknown parent", lambda p: PARTS(p)["piston"].update(parent="nope"), "unknown_parent"),
    ("parent cycle", lambda p: (PARTS(p)["crank"].update(parent="crank_pin")), "cycle"),
    ("duplicate part", lambda p: SPEC(p)["parts"].append(dict(SPEC(p)["parts"][0])),
     "duplicate_id"),
    ("driver expr", lambda p: SPEC(p)["drivers"].update(piston_y="foo(1)"), "expr_error"),
    ("driver unknown name", lambda p: SPEC(p)["drivers"].update(rod_ang="crank + bogus"),
     "expr_error"),
    ("size uses a dof", lambda p: PARTS(p)["head"].update(size=[0.16, "crank", 0.16]),
     "expr_error"),
    ("tube inner >= outer", lambda p: PARTS(p)["cylinder"].update(r_inner="bore"),
     "bad_shape_param"),
    ("missing radius", lambda p: PARTS(p)["spark_plug"].pop("r"), "bad_shape_param"),
    ("bad shape", lambda p: PARTS(p)["head"].update(shape="cube"), "schema"),
    ("bad colour", lambda p: PARTS(p)["head"].update(color="red"), "schema"),
    ("joint dof", lambda p: PARTS(p)["crank"]["joint"].update(dof="cam"), "unknown_dof"),
    ("bad anchor", lambda p: SPEC(p)["anchors"].update(spark="plug"), "bad_anchor"),
    ("spec id", lambda p: SPEC(p).update(id="other"), "schema"),
    ("bad id", lambda p: p["assets"][0].update(id="Four-Stroke"), "bad_id"),
    ("unknown asset", lambda p: p["sets"][0]["objects"][0].update(asset="@nope"),
     "unknown_asset"),
    ("missing @", lambda p: p["sets"][0]["objects"][0].update(asset="four_stroke"),
     "unknown_asset"),
    ("set pose dof", lambda p: p["sets"][0]["objects"][0].update(pose={"cam": 0}),
     "unknown_dof"),
    ("set pose range", lambda p: p["sets"][0]["objects"][0].update(pose={"crank": 5000}),
     "dof_range"),
    ("mark point", lambda p: p["sets"][0]["marks"].update(x="eng.nowhere"), "unknown_point"),
    ("unknown set", lambda p: S(1)(p).update(set="garage"), "unknown_set"),
    ("missing shot", lambda p: p["shots"].pop(2), "missing_shot"),
    ("one key", lambda p: S(0)(p)["camera"]["keys"].pop(), "too_few_keys"),
    ("word not found", lambda p: S(1)(p)["camera"]["keys"][1]["at"].update(word="涡轮"),
     "anchor_not_found"),
    ("occurrence", lambda p: S(0)(p)["actions"][1]["at"].update(occurrence=2),
     "anchor_not_found"),
    ("two time kinds", lambda p: S(0)(p)["actions"][0]["at"].update(word="活塞"), "bad_time"),
    ("no time kind", lambda p: S(0)(p)["actions"][0].update(at={}), "bad_time"),
    ("key order", lambda p: S(1)(p)["camera"]["keys"].reverse(), "time_order"),
    ("until before at", lambda p: S(2)(p)["actions"][1].update(
        at={"word": "做功"}, until={"word": "点火"}), "time_order"),
    ("pos and orbit", lambda p: S(0)(p)["camera"]["keys"][0].update(pos=[1, 1, 1]), "schema"),
    ("look anchor", lambda p: S(1)(p)["camera"]["keys"][0].update(look="eng.nope"),
     "unknown_point"),
    ("look mark", lambda p: S(1)(p)["camera"]["keys"][0].update(look="nomark"),
     "unknown_point"),
    ("orbit around", lambda p: S(0)(p)["camera"]["keys"][0]["orbit"].update(around="car"),
     "unknown_point"),
    ("pose dof", lambda p: S(1)(p)["actions"][1].update(dof="cam"), "unknown_dof"),
    ("pose range", lambda p: S(1)(p)["actions"][1].update(to=2000), "dof_range"),
    ("pose on a part", lambda p: S(1)(p)["actions"][1].update(target="eng.piston"),
     "unknown_target"),
    ("highlight part", lambda p: S(1)(p)["actions"][2].update(target="eng.turbo"),
     "unknown_target"),
    ("section target", lambda p: S(1)(p)["actions"][0].update(targets=["car"]),
     "unknown_target"),
    ("label point", lambda p: S(0)(p)["actions"][1].update(attach="eng.piston"),
     "unknown_point"),
    ("bad op", lambda p: S(0)(p)["actions"].append({"op": "wiggle", "at": {"t": 0}}), "schema"),
    ("bad ease", lambda p: S(0)(p)["camera"]["keys"][1].update(ease="bounce"), "schema"),
    ("image3d target", lambda p: p["assets"].append(
        {"id": "cat", "kind": "image3d", "prompt": "一只坐着的猫"}), "missing_target"),
    ("library unknown", lambda p: p["assets"].append(
        {"id": "gear", "kind": "library", "asset": "asset_20990101-000000-000000"}),
     "unknown_asset"),
]


@pytest.mark.parametrize("name,fn,code", BROKEN, ids=[b[0] for b in BROKEN])
def test_broken_variant_is_rejected(name, fn, code):
    got = errs(mutate(fn))
    assert code in got, (name, got)


def test_extra_and_duplicate_shot():
    p = mutate(lambda p: p["shots"].append(copy.deepcopy(p["shots"][0])))
    assert errs(p) == ["duplicate_id"]
    p = mutate(lambda p: p["shots"][2].update(id="s09"))
    assert sorted(errs(p)) == ["extra_shot", "missing_shot"]


def test_scale_is_a_warning_only():
    p = mutate(lambda p: p["sets"][0]["objects"][0].update(scale=2))
    issues = ps.validate(p, SCENES)
    assert [(i["code"], i["severity"]) for i in issues] == [("scale_not_1", "warn")]


def test_library_and_image3d_assets():
    lib_id = _lib()[0]["id"]

    def add(p):
        p["assets"] += [{"id": "gear", "kind": "library", "asset": lib_id},
                        {"id": "cat", "kind": "image3d", "prompt": "一只坐着的猫",
                         "target": {"axis": "h", "metres": 0.3}}]
        p["sets"][0]["objects"] += [{"id": "g", "asset": "@gear", "at": [0.6, 0, 0]},
                                    {"id": "g2", "asset": lib_id, "at": [-0.6, 0, 0]},
                                    {"id": "cat", "asset": "@cat", "at": [0, 0, 0.6]}]
        p["shots"][0]["actions"] += [
            {"op": "spin", "target": "g", "dof": "turn", "deg_per_s": 90, "at": {"mark": "start"}},
            {"op": "label", "text": "齿轮", "attach": "g2.hub", "at": {"t": 1}},
            {"op": "highlight", "target": "cat.body", "at": {"t": 0}, "until": {"t": 2}}]
    p = mutate(add)
    assert errs(p, lib=_lib()) == []
    assert "unknown_asset" in errs(p)  # same plan without the library list
    p["shots"][0]["actions"][-3]["dof"] = "crank"
    assert errs(p, lib=_lib()) == ["unknown_dof"]


def test_messages_say_where():
    p = mutate(lambda p: S(1)(p)["camera"]["keys"][1]["at"].update(word="涡轮"))
    (i,) = ps.validate(p, SCENES)
    assert i["where"] == "shots.s02.camera.keys[1].at" and "涡轮" in i["message"]


def test_not_an_object():
    assert errs("nope") == ["schema"]
    assert "schema" in errs({"version": 1, "assets": [], "sets": [], "shots": []})
