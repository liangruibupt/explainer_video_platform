"""Linked parts: a parametric library model's spec reaches the planner, a plan asset can extend
it ("extends", merged spec), and the linked_parts warning catches a flywheel placed as its own
spinning object beside an engine whose crank turns (the 2026-11-25 integration run)."""
import copy
import json
from pathlib import Path

from app.previs import plan, plan_schema as ps

EX = Path(__file__).resolve().parents[1] / "app" / "previs" / "examples" / "four_stroke"
PLAN = json.loads((EX / "plan.json").read_text())
SCENES = json.loads((EX / "scenes.json").read_text())
SPEC = PLAN["assets"][0]["spec"]
LIB_ID = "asset_20261125-035356-37e474"


def lib_engine(parametric=True):
    """A library record like meta.json of the built four_stroke engine."""
    rec = {"id": LIB_ID, "name": "单缸四冲程发动机", "kind": "model",
           "size": [0.3, 0.52, 0.22],
           "bbox": {"min": [-0.15, 0, -0.11], "max": [0.15, 0.52, 0.11]},
           "rig": {"dofs": SPEC["dofs"]},
           "parts": [{"id": p["id"]} for p in SPEC["parts"]],
           "anchors": {k: [0, 0, 0] for k in SPEC["anchors"]},
           "source": ({"type": "parametric", "spec": SPEC} if parametric
                      else {"type": "image3d"})}
    return rec


FLY_SPEC = {  # the separate flywheel the planner wrote in the integration run (trimmed)
    "id": "flywheel", "name": "飞轮", "version": 1, "units": "m",
    "params": {"R": {"value": 0.12}},
    "dofs": {"spin": {"min": -3600, "max": 3600, "default": 0}},
    "dims": {"w": "2*R", "h": "0.26 + R", "d": 0.13},
    "parts": [
        {"id": "base", "shape": "box", "size": [0.2, 0.02, 0.12], "at": [0, 0.01, 0]},
        {"id": "wheel", "shape": "group", "at": [0, 0.26, 0.045],
         "joint": {"type": "hinge", "axis": "z", "dof": "spin"}},
        {"id": "rim", "shape": "tube", "parent": "wheel", "r_outer": "R", "r_inner": "R - 0.04",
         "h": 0.05, "axis": "z"}],
    "anchors": {"rim_top": {"part": "wheel", "at": [0, "R", 0]}},
}


def separate_flywheel_plan(x=0.42, spin_in_s03=True):
    p = copy.deepcopy(PLAN)
    p["assets"] = [{"id": "engine", "kind": "library", "asset": LIB_ID},
                   {"id": "flywheel", "kind": "parametric", "spec": copy.deepcopy(FLY_SPEC)}]
    p["sets"][0]["objects"] = [{"id": "eng", "asset": "@engine", "at": [0, 0, 0]},
                               {"id": "fly", "asset": "@flywheel", "at": [x, 0, 0]}]
    if spin_in_s03:
        p["shots"][2]["actions"].append({"op": "spin", "target": "fly", "dof": "spin",
                                         "deg_per_s": 120, "at": {"word": "做功"}})
    return p


FLY_PARTS = [  # the same flywheel as parts of the engine, on the crank axis, driven by crank
    {"id": "flywheel", "shape": "tube", "r_outer": 0.1, "r_inner": 0.07, "h": 0.03, "axis": "z",
     "at": [0, 0.2, 0.13], "color": "accent2",
     "joint": {"type": "hinge", "axis": "z", "dof": "crank"}},
    {"id": "flywheel_hub", "shape": "cylinder", "parent": "flywheel", "r": 0.02, "h": 0.03,
     "axis": "z", "color": "accent1"},
]


def extended_plan():
    p = copy.deepcopy(PLAN)
    p["assets"] = [{"id": "engine_fw", "kind": "parametric", "extends": LIB_ID,
                    "spec": {"id": "engine_fw", "name": "带飞轮的单缸发动机", "version": 1,
                             "units": "m", "dims": {"w": 0.3, "h": 0.52, "d": 0.255},
                             "parts": copy.deepcopy(FLY_PARTS),
                             "anchors": {"flywheel_rim": {"part": "flywheel", "at": [0, 0.1, 0]}}}}]
    p["sets"][0]["objects"][0]["asset"] = "@engine_fw"
    p["shots"][2]["actions"].append({"op": "label", "text": "飞轮", "attach": "eng.flywheel_rim",
                                     "at": {"word": "做功"}})
    return p


def codes(p, lib):
    return [(i["code"], i["severity"]) for i in ps.validate(p, SCENES, lib)]


# ------------------------------------------------------------- the planner sees the spec


def test_library_summary_has_spec_only_for_parametric_models():
    s = plan.library_summary([lib_engine()])[0]
    assert s["spec"]["parts"][0]["id"] == SPEC["parts"][0]["id"] and s["id"] == LIB_ID
    assert "spec" not in plan.library_summary([lib_engine(parametric=False)])[0]


def test_system_prompt_carries_spec_and_the_linked_parts_rule():
    sp = plan.system_prompt([lib_engine()])
    lib_part = sp.split("## Library models")[1]
    assert '"crank_pin"' in lib_part and '"piston_y"' in lib_part  # parts + drivers of the spec
    assert "MUST be parts of ONE asset" in sp and '"extends"' in sp


# ------------------------------------------------------------- extends


def test_merge_spec_keeps_base_and_appends_new_parts():
    m = ps.merge_spec(SPEC, extended_plan()["assets"][0]["spec"])
    ids = [p["id"] for p in m["parts"]]
    assert ids[:len(SPEC["parts"])] == [p["id"] for p in SPEC["parts"]]
    assert ids[-2:] == ["flywheel", "flywheel_hub"]
    assert m["id"] == "engine_fw" and m["dims"]["d"] == 0.255
    assert list(m["drivers"]) == list(SPEC["drivers"]) and "crank" in m["dofs"]
    assert "chamber" in m["anchors"] and "flywheel_rim" in m["anchors"]
    assert ps.merge_spec(SPEC, m) == m  # idempotent
    rep = ps.merge_spec(SPEC, {"parts": [{"id": "head", "shape": "box", "size": [0.2, 0.04, 0.2],
                                          "at": [0, 0.48, 0]}]})
    assert [p["id"] for p in rep["parts"]] == [p["id"] for p in SPEC["parts"]]  # replaced in place
    assert next(p for p in rep["parts"] if p["id"] == "head")["size"][0] == 0.2


def test_extended_plan_validates_without_changing_the_callers_plan():
    p = extended_plan()
    before = copy.deepcopy(p)
    assert codes(p, [lib_engine()]) == []
    assert p == before  # validate checks a merged copy
    ps.resolve_extends(p, [lib_engine()])
    assert len(p["assets"][0]["spec"]["parts"]) == len(SPEC["parts"]) + 2
    again = copy.deepcopy(p)
    ps.resolve_extends(again, [lib_engine()])
    assert again == p and codes(p, [lib_engine()]) == []


def test_extends_unknown_or_not_parametric():
    p = extended_plan()
    assert ("unknown_asset", "error") in codes(p, [])
    assert ("unknown_asset", "error") in codes(p, [lib_engine(parametric=False)])
    q = extended_plan()
    q["assets"][0]["kind"] = "library"
    q["assets"][0]["asset"] = LIB_ID
    assert ("schema", "error") in codes(q, [lib_engine()])


def test_extends_uses_base_names():
    p = extended_plan()  # the shots use eng.chamber, eng.intake_valve, DOF crank of the base
    p["assets"][0]["spec"]["parts"][0]["joint"]["dof"] = "nope"
    assert ("unknown_dof", "error") in codes(p, [lib_engine()])


def test_previs_plan_writes_the_merged_spec():
    ans = json.dumps(extended_plan(), ensure_ascii=False)
    out = plan.previs_plan(SCENES, "四冲程发动机", "16:9", [lib_engine()], ask=lambda *a: ans)
    a = out["assets"][0]
    assert a["extends"] == LIB_ID and any(x["id"] == "crank_pin" for x in a["spec"]["parts"])


def test_build_assets_builds_the_merged_spec():
    built = []

    def build(spec):
        built.append(spec)
        return {"id": "asset_20261125-050000-abcdef"}

    p = extended_plan()
    rep = plan.build_assets(p, SCENES, [lib_engine()], build=build, shape=False)
    assert [x["id"] for x in built[0]["parts"]][-2:] == ["flywheel", "flywheel_hub"]
    assert rep["asset_ids"] == {"@engine_fw": "asset_20261125-050000-abcdef"}
    assert rep["plan_issues"] == []


# ------------------------------------------------------------- linked_parts


def test_linked_parts_flags_the_separate_flywheel():
    issues = [i for i in ps.validate(separate_flywheel_plan(), SCENES, [lib_engine()])
              if i["code"] == "linked_parts"]
    assert len(issues) == 1 and issues[0]["severity"] == "warn"
    assert "fly" in issues[0]["message"] and "eng" in issues[0]["message"]
    assert issues[0]["where"] == "sets.bench.objects.fly"
    assert not ps.errors(ps.validate(separate_flywheel_plan(), SCENES, [lib_engine()]))


def test_linked_parts_low_false_positives():
    lib = [lib_engine()]
    # far away: two machines side by side, each on its own
    assert ("linked_parts", "warn") not in codes(separate_flywheel_plan(x=2.0), lib)
    # near but not animated in the same shot (s03 animates only the engine)
    assert ("linked_parts", "warn") not in codes(separate_flywheel_plan(spin_in_s03=False), lib)
    # the fixed version: one asset, flywheel jointed to the crank
    assert ("linked_parts", "warn") not in codes(extended_plan(), lib)
    # the example plan
    assert ps.validate(PLAN, SCENES) == []
    # a near object whose DOF is not a free rotation (a door hinge 0..90) is not flagged
    p = separate_flywheel_plan()
    p["assets"][1]["spec"]["dofs"]["spin"] = {"min": 0, "max": 90, "default": 0}
    p["shots"][2]["actions"][-1] = {"op": "pose", "target": "fly", "dof": "spin", "to": 90,
                                    "at": {"word": "做功"}}
    assert ("linked_parts", "warn") not in codes(p, lib)


def test_linked_parts_with_rotated_object_and_library_size_only():
    lib = lib_engine()
    del lib["bbox"]  # size only: centred box
    p = separate_flywheel_plan()
    p["sets"][0]["objects"][1]["rot_deg"] = [0, 90, 0]
    assert ("linked_parts", "warn") in codes(p, [lib])
