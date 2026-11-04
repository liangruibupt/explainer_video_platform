"""Validator tests (no GPU, no model calls)."""
import copy

import pytest

from app.workflows import store
from app.workflows.validate import check_types, levels, topo_order, validate

MODS = {"image", "video", "music"}


def codes(errs):
    return sorted(e["code"] for e in errs)


def cover():
    return copy.deepcopy(store.template("wf_cover_image"))


def test_node_declarations_are_consistent():
    check_types()


@pytest.mark.parametrize("wf", store.templates(), ids=lambda w: w["id"])
def test_templates_validate(wf):
    assert validate(wf, MODS) == []


def test_cycle_detected():
    wf = cover()
    # n4.output (any, passthrough of an image) back into n2.idea (text) also mismatches;
    # use a text->text loop instead: a second agent.write fed by and feeding the chain.
    wf["nodes"].append({"id": "w1", "type": "agent.write", "params": {"instruction": "x"}})
    wf["nodes"].append({"id": "w2", "type": "agent.write", "params": {"instruction": "y"}})
    wf["edges"] += [{"from": "w1.text", "to": "w2.text"}, {"from": "w2.text", "to": "w1.text"}]
    errs = validate(wf, MODS)
    assert "cycle" in codes(errs)
    assert any("w1" in e["message"] and "w2" in e["message"] for e in errs if e["code"] == "cycle")


def test_self_loop():
    wf = cover()
    wf["nodes"].append({"id": "w1", "type": "agent.write", "params": {"instruction": "x"}})
    wf["edges"].append({"from": "w1.text", "to": "w1.text"})
    assert "cycle" in codes(validate(wf, MODS))


def test_unknown_port_and_node():
    wf = cover()
    wf["edges"][0] = {"from": "n1.txt", "to": "n2.idea"}
    wf["edges"].append({"from": "n9.text", "to": "n2.idea"})
    errs = validate(wf, MODS)
    assert {"bad_port", "bad_edge"} <= set(codes(errs))
    bp = next(e for e in errs if e["code"] == "bad_port")
    assert bp["node"] == "n1" and bp["port"] == "txt"


def test_type_mismatch():
    wf = cover()
    # image output into a text input
    wf["nodes"].append({"id": "w1", "type": "agent.write", "params": {"instruction": "x"}})
    wf["edges"].append({"from": "n3.image", "to": "w1.text"})
    errs = validate(wf, MODS)
    e = next(e for e in errs if e["code"] == "type_mismatch")
    assert e["node"] == "w1" and e["port"] == "text" and "image" in e["message"]


def test_passthrough_type_is_resolved():
    wf = cover()
    # review.human output is "any" but carries the image: into a text input must fail
    wf["nodes"].append({"id": "w1", "type": "agent.write", "params": {"instruction": "x"}})
    wf["edges"].append({"from": "n4.output", "to": "w1.text"})
    assert "type_mismatch" in codes(validate(wf, MODS))
    # ...and into video.h3.first_frame (image) it is fine
    wf = cover()
    wf["nodes"].append({"id": "v1", "type": "video.h3", "params": {"prompt": "a lake"}})
    wf["edges"].append({"from": "n4.output", "to": "v1.first_frame"})
    assert validate(wf, MODS) == []


def test_missing_required_param_and_input():
    wf = cover()
    wf["nodes"][0]["params"]["text"] = "  "
    wf["edges"] = [e for e in wf["edges"] if e["to"] != "n4.input"]
    errs = validate(wf, MODS)
    assert {"missing_param", "missing_input"} <= set(codes(errs))
    assert any(e["node"] == "n1" and e["port"] == "text" for e in errs)
    assert any(e["node"] == "n4" and e["port"] == "input" for e in errs)


def test_required_unless_input():
    wf = cover()
    wf["edges"] = [e for e in wf["edges"] if e["to"] != "n3.prompt"]
    errs = validate(wf, MODS)
    assert any(e["code"] == "missing_param" and e["node"] == "n3" and e["port"] == "prompt"
               for e in errs)
    wf["nodes"][2]["params"]["prompt"] = "a cat"
    # n2 now feeds nothing, which is allowed
    assert validate(wf, MODS) == []


def test_bad_param_values():
    wf = cover()
    wf["nodes"][2]["params"].update(ratio="4:3", seed=-1, steps="many", bogus=1)
    errs = validate(wf, MODS)
    msgs = " ".join(e["message"] for e in errs)
    assert codes(errs).count("bad_param") == 3 and "unknown_param" in codes(errs)
    assert "4:3" in msgs and ">= 0" in msgs


def test_bool_is_not_an_integer():
    wf = cover()
    wf["nodes"][2]["params"]["seed"] = True
    assert "bad_param" in codes(validate(wf, MODS))


def test_two_edges_into_one_input():
    wf = cover()
    wf["edges"].append({"from": "n1.text", "to": "n3.prompt"})
    assert "multiple_inputs" in codes(validate(wf, MODS))


def test_gpu_module_must_be_configured():
    errs = validate(cover(), {"video", "music"})
    e = next(e for e in errs if e["code"] == "module_unavailable")
    assert e["node"] == "n3"


def test_unknown_type_duplicate_id_and_format():
    wf = cover()
    wf["nodes"].append({"id": "n2", "type": "agent.write", "params": {"instruction": "x"}})
    wf["nodes"].append({"id": "x1", "type": "web.search", "params": {}})
    assert {"duplicate_id", "unknown_type"} <= set(codes(validate(wf, MODS)))
    assert codes(validate({"name": "x", "nodes": []}, MODS)) == ["format"]
    assert codes(validate([], MODS)) == ["format"]


def test_topo_and_levels():
    wf = store.template("wf_mv_from_one_line")
    order, cyc = topo_order(wf["nodes"], wf["edges"])
    assert not cyc and order.index("n1") == 0 and order[-1] == "n10"
    lv = levels(wf["nodes"], wf["edges"])
    assert lv["n1"] == 0 and lv["n2"] == lv["n4"] == lv["n6"] == 1 and lv["n7"] == 3
    assert lv["n10"] == lv["n9"] + 1
