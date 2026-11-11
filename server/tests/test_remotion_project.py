"""Part A of docs/REMOTION_CONTRACT.md: shotcraft catalog, scene generation, project files.

The LLM is mocked. Type-checks and stills run the real shared Remotion install when it exists
(it does on the studio box); otherwise those two tests are skipped.
"""
import json
import os
from pathlib import Path

import pytest

from app.remotion import catalog, generate, project, render

HAS_BASE = (render.BASE / "node_modules" / ".bin" / "remotion").exists()

GOOD = """import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import type { PropField } from "../types";
import { EASE } from "../lib/Polish";
export const DEFAULTS = { title: "TITLE", color: "#ffffff", background: "#203040" };
export const schema: PropField[] = [{ type: "text", key: "title", label: "标题" }];
export const Scene: React.FC<Partial<typeof DEFAULTS> & { duration?: number }> = (p) => {
  const o = { ...DEFAULTS, ...p };
  const dur = p.duration ?? 90;
  const f = useCurrentFrame();
  const a = interpolate(f, [0, Math.max(1, dur / 4)], [0, 1],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: EASE.snappy });
  return (
    <AbsoluteFill style={{ background: o.background, justifyContent: "center", alignItems: "center" }}>
      <div style={{ color: o.color, fontSize: 120, opacity: a }}>{o.title}</div>
    </AbsoluteFill>
  );
};
"""
TYPE_ERR = GOOD.replace("const dur = p.duration ?? 90;", "const dur: string = p.duration ?? 90;")
BLANK = GOOD.replace("{o.title}</div>", "</div>")


@pytest.fixture(autouse=True)
def _layout_off(monkeypatch):
    """The vision layout check is off unless a test turns it on (it would call Bedrock).
    Stills go through the per-frame path so tests can fake render.still; the real batch path
    (stills.cjs) is covered by the HAS_BASE tests, which re-enable it."""
    monkeypatch.setenv("EVP_LAYOUT_CHECK", "0")
    monkeypatch.setenv("EVP_BATCH_STILLS", "0")


def _job(n=2):
    return {"id": "t", "scenes": [{"id": f"scene_{i}", "title": f"标题{i}",
                                   "narration": f"旁白{i}", "visual": "v"} for i in range(n)]}


class FakeLLM:
    """Returns the pick JSON for the picker prompt and per-scene modules from a script."""

    def __init__(self, picks, modules):
        self.picks, self.modules, self.calls = picks, modules, []

    def __call__(self, system, user, max_tokens=8000):
        self.calls.append((system, user))
        if system == generate.PICK_SYSTEM:
            return json.dumps(self.picks)
        sid = json.loads(user.split("Scene:\n", 1)[1].split("\n\n", 1)[0])["id"]
        q = self.modules[sid]
        return q.pop(0) if len(q) > 1 else q[0]


# ---- catalog ------------------------------------------------------------------------------

def test_front_matter_and_seconds():
    fm = catalog.parse_front_matter("---\nname: x\n一句话: 甲：乙\n时长: 4–5s\n---\nbody")
    assert fm == {"name": "x", "一句话": "甲：乙", "时长": "4–5s"}
    assert catalog.seconds_of("4–5s") == 4.0
    assert catalog.seconds_of("约 7.5s（225f@30fps）") == 7.5
    assert catalog.seconds_of("120f") == 4.0
    assert catalog.seconds_of("很短") is None


def _fake_shotcraft(root: Path):
    for cat, card, imp in (("opening", "a", "../../_fixtures/Polish"),
                           ("data", "b", "../../_fixtures/Fixtures"),
                           ("outro", "nodemo", None)):
        md = root / "references" / "shots" / cat / f"{card}.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(f"---\nname: {card}\n一句话: {card} 摘要\n适用: 用途\n时长: 3s\n能量: 中\n---\n## 意图\n")
        if imp:
            dd = root / "demos" / cat / card
            dd.mkdir(parents=True)
            (dd / "Demo.tsx").write_text(f"import {{ X }} from '{imp}';\nexport const D = 1;\n")


def test_catalog_index(tmp_path):
    _fake_shotcraft(tmp_path)
    cs = catalog.cards(tmp_path)
    assert [c.id for c in cs] == ["data/b", "opening/a"]  # card without demo is dropped
    b = catalog.by_id(tmp_path)["data/b"]
    assert b.needs_ui and not catalog.by_id(tmp_path)["opening/a"].needs_ui
    assert b.seconds == 3.0 and b.summary == "b 摘要" and b.md.name == "b.md"
    assert "data/b [UI-mock] | b 摘要" in catalog.compact(tmp_path)
    assert "Demo.tsx" in catalog.demo_source(b)


def test_real_checkout_catalog():
    if not catalog.SHOTCRAFT.is_dir():
        pytest.skip("no shotcraft checkout")
    cs = catalog.by_id()
    assert len(cs) > 100
    for cid in generate.DEFAULT_CARDS.values():
        assert cid in cs and not cs[cid].needs_ui
    assert all(c.summary for c in cs.values())


# ---- generated files ----------------------------------------------------------------------

def test_index_ts_and_timeline():
    scenes = [{"id": "title_card", "title": "T"}, {"id": "2-Step", "title": "S"},
              {"id": "title-card", "title": "dup"}]
    sts = project.stems(scenes)
    assert sts == ["title_card", "s2_step", "title_card_2"]
    ts = project.index_ts(scenes)
    assert 'import * as title_card from "./title_card";' in ts
    assert '"2-Step": s2_step' in ts and '"title-card": title_card_2' in ts
    tl = project.timeline(scenes, [100, 50, 25], 1080, 1920, "#000000", {"title_card": "opening/a"})
    assert [(s["from"], s["duration"]) for s in tl["scenes"]] == [(0, 100), (100, 50), (150, 25)]
    assert tl["width"] == 1080 and tl["fps"] == 30 and tl["scenes"][0]["card"] == "opening/a"
    assert tl["scenes"][1]["card"] == project.FALLBACK_CARD and tl["scenes"][0]["label"] == "S1 T"
    assert tl["revision"] != project.timeline(scenes, [1, 1, 1], 1, 1, "", {})["revision"]


def test_check_tsx():
    assert generate.check_tsx(GOOD) == []
    bad = generate.check_tsx(GOOD.replace('"../lib/Polish"', '"../../_fixtures/Fixtures"')
                             .replace("const f =", "const r = Math.random(); const f ="))
    assert any("not allowed" in b for b in bad) and any("Math.random" in b for b in bad)
    assert any("DEFAULTS" in b for b in generate.check_tsx(GOOD.replace("export const DEFAULTS", "const DEFAULTS")))
    assert generate.extract_tsx("```tsx\n" + GOOD + "```") == GOOD


def test_tsc_error_mapping():
    out = ("src/scenes/a.tsx(3,5): error TS2322: bad\n"
           "src/scenes/index.ts(4,20): error TS2306: File './b' is not a module \"./b\"\n"
           "src/Main.tsx(1,1): error TS1: other\n")
    m = project.tsc_errors(out, ["a", "b"])
    assert set(m) == {"a", "b"} and "TS2322" in m["a"]


def test_pick_cards_defaults(monkeypatch):
    cards = list(catalog.by_id().values())
    monkeypatch.setattr(generate.llm, "ask", FakeLLM(
        {"look": {"background": "#112233", "accent": "nope"},
         "cards": {"scene_0": "typography/paper-title-card", "scene_1": "made/up"}}, {}))
    look, picks = generate.pick_cards(_job(3)["scenes"], [90] * 3, 1920, 1080, {}, {}, False, cards)
    assert look["background"] == "#112233" and look["accent"] == generate.DEFAULT_LOOK["accent"]
    assert picks == {"scene_0": "typography/paper-title-card",
                     "scene_1": generate.DEFAULT_CARDS["middle"],
                     "scene_2": generate.DEFAULT_CARDS["last"]}


def test_fallback_tsx_shape():
    src = project.fallback_tsx({"id": "x", "title": "标题", "narration": "长" * 80}, generate.DEFAULT_LOOK)
    assert 'export { Scene, schema } from "../lib/TitleCard";' in src
    assert '"title": "标题"' in src and "…" in src


# ---- write_project ------------------------------------------------------------------------

def _run(tmp_path, monkeypatch, modules, n=2, still=None, typecheck=None, images=None):
    d = tmp_path / "job"
    d.mkdir()
    job = _job(n)
    fake = FakeLLM({"cards": {"scene_0": "typography/paper-title-card",
                              "scene_1": "data/chart-live-moves"}}, modules)
    monkeypatch.setattr(generate.llm, "ask", fake)
    if still:
        monkeypatch.setattr(render, "still", still)
    if typecheck:
        monkeypatch.setattr(render, "typecheck", typecheck)
    logs = []
    proj = project.write_project(job, d, [90] * n, 1920, 1080, images=images or {}, clips={},
                                 background=None, context="CTX", log=logs.append)
    return job, proj, logs, fake


def _ok_still(proj, frame, out, log=None, timeout=300):
    from PIL import Image
    im = Image.new("RGB", (64, 36), "red")
    im.paste((0, 0, 255), (0, 0, 20, 20))
    im.save(out)


def test_write_project_mocked(tmp_path, monkeypatch):
    img = tmp_path / "pic.png"
    _ok_still(None, 0, img)

    def tc(proj, log=None, timeout=180):
        bad = [p.name for p in (proj / "src" / "scenes").glob("*.tsx") if "dur: string" in p.read_text()]
        if bad:
            log.write_text("".join(f"src/scenes/{b}(7,9): error TS2322: Type 'number' is not "
                                   "assignable to type 'string'.\n" for b in bad))
            raise RuntimeError("tsc failed")

    # scene_0 is fine; scene_1 fails type-check twice -> one regeneration, then the fallback.
    job, proj, logs, fake = _run(tmp_path, monkeypatch,
                                 {"scene_0": [GOOD], "scene_1": [TYPE_ERR, TYPE_ERR]},
                                 still=_ok_still, typecheck=tc, images={"scene_0": img})
    assert [s["card"] for s in job["scenes"]] == ["typography/paper-title-card", project.FALLBACK_CARD]
    tl = json.loads((proj / "src" / "data" / "timeline.json").read_text())
    assert [s["card"] for s in tl["scenes"]] == [s["card"] for s in job["scenes"]]
    assert (proj / "src" / "scenes" / "scene_0.tsx").read_text() == GOOD
    assert "lib/TitleCard" in (proj / "src" / "scenes" / "scene_1.tsx").read_text()
    assert not (proj / "src" / "scenes" / "s01.tsx").exists()  # template sample removed
    assert '"scene_1": scene_1' in (proj / "src" / "scenes" / "index.ts").read_text()
    pub = proj / "public" / "img" / "scene_0.png"
    assert pub.exists() and not pub.is_symlink()
    # prompts: one pick + scene_0 + scene_1 twice; the retry carries the tsc error and old source
    assert len(fake.calls) == 4
    retry = [u for s, u in fake.calls if "FAILED" in u]
    assert len(retry) == 1 and "TS2322" in retry[0] and "dur: string" in retry[0]
    s0 = [u for s, u in fake.calls if '"id": "scene_0"' in u and s == generate.SCENE_SYSTEM]
    assert len(s0) == 1 and "CTX" in s0[0] and 'staticFile("img/scene_0.png")' in s0[0]
    assert any("scene_1 fallback" in x for x in logs) and any("scene_0 ok" in x for x in logs)
    assert (proj / "src" / "data" / "captions.json").read_text().strip() == "[]"


def test_still_failure_regenerates_then_falls_back(tmp_path, monkeypatch):
    calls = []

    def still(proj, frame, out, log=None, timeout=300):
        calls.append(frame)
        if frame >= 90:  # scene_1 (frames 90..179)
            raise RuntimeError("remotion still failed")
        _ok_still(proj, frame, out)

    job, proj, logs, fake = _run(tmp_path, monkeypatch, {"scene_0": [GOOD], "scene_1": [GOOD]},
                                 still=still, typecheck=lambda *a, **k: None)
    assert calls == [45, 135, 135]  # midpoints; scene_1 retried once after regeneration
    assert job["scenes"][1]["card"] == project.FALLBACK_CARD
    assert job["scenes"][0]["card"] == "typography/paper-title-card"


def test_llm_failure_falls_back(tmp_path, monkeypatch):
    def boom(system, user, max_tokens=8000):
        raise RuntimeError("bedrock down")
    d = tmp_path / "j"
    d.mkdir()
    monkeypatch.setattr(generate.llm, "ask", boom)
    monkeypatch.setattr(render, "typecheck", lambda *a, **k: None)
    job = _job(2)
    project.write_project(job, d, [60, 60], 1920, 1080, images={}, clips={}, background=None,
                          context="", log=lambda m: None)
    assert [s["card"] for s in job["scenes"]] == [project.FALLBACK_CARD] * 2


@pytest.mark.skipif(not HAS_BASE, reason="no shared Remotion install")
def test_write_project_real_tsc_and_still(tmp_path, monkeypatch):
    # Real tsc + real batched stills: scene_0 renders, scene_1 is blank both times -> fallback.
    monkeypatch.setenv("EVP_BATCH_STILLS", "1")
    job, proj, logs, fake = _run(tmp_path, monkeypatch, {"scene_0": [GOOD], "scene_1": [BLANK, BLANK]})
    assert [s["card"] for s in job["scenes"]] == ["typography/paper-title-card", project.FALLBACK_CARD]
    assert (proj / ".check" / "scene_0.png").exists()
    render.typecheck(proj)  # the final project compiles
    assert any("flat colour" in u for s, u in fake.calls)
    t = json.loads((proj / ".check" / "result.json").read_text())["timing"]
    assert t["main"]["still"]["n"] == 3 and t["main"]["tsc"]["n"] >= 2 and t["total_s"] > 0


@pytest.mark.skipif(not HAS_BASE, reason="no shared Remotion install")
def test_batch_stills_match_single_stills(tmp_path, monkeypatch):
    # stills.cjs (one bundle, one browser) gives the same pixels as `remotion still`.
    from PIL import Image, ImageChops
    monkeypatch.setenv("EVP_BATCH_STILLS", "1")
    job, proj, logs, fake = _run(tmp_path, monkeypatch, {"scene_0": [GOOD], "scene_1": [GOOD2]})
    frames = [5, 60, 130]
    errs = render.stills(proj, frames + [100000], [tmp_path / f"b{f}.png" for f in frames + [100000]])
    assert set(errs) == {100000}  # a frame past the end fails alone
    for f in frames:
        render.still(proj, f, (tmp_path / f"s{f}.png").resolve())
        a, b = (Image.open(tmp_path / f"{k}{f}.png").convert("RGBA") for k in "bs")
        assert a.size == (1920, 1080) and ImageChops.difference(a, b).getbbox() is None


# ---- layout check (vision, §3.9) ----------------------------------------------------------

GOOD2 = GOOD.replace('"TITLE"', '"TITLE2"')
OK = json.dumps({"ok": True, "issues": []})
ISSUE = json.dumps({"ok": False, "issues": [
    {"kind": "clipped", "where": "left edge: earth circle", "fix": "move it right by 10% of width"}]})
TWO = json.dumps({"ok": False, "issues": [
    {"kind": "overlap", "where": "y-axis ticks", "fix": "space them"},
    {"kind": "empty", "where": "right half", "fix": "fill it"}]})


class FakeVision:
    """Scripted layout reviews per scene id: the n-th review of a scene gets replies[sid][n]."""

    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def __call__(self, system, user, images, max_tokens=2000):
        assert system == generate.LAYOUT_SYSTEM
        sid = json.loads(user.split("Scene:\n", 1)[1].split("\n\nThis is a REWRITE", 1)[0])["id"]
        n = sum(1 for c in self.calls if c[0] == sid)
        self.calls.append((sid, user, len(images)))
        q = self.replies[sid]
        return q[min(n, len(q) - 1)]


def _tc_strict(proj, log=None, timeout=180):
    bad = [p.name for p in (proj / "src" / "scenes").glob("*.tsx") if "dur: string" in p.read_text()]
    if bad:
        log.write_text("".join(f"src/scenes/{b}(7,9): error TS2322: bad\n" for b in bad))
        raise RuntimeError("tsc failed")


def _run_layout(tmp_path, monkeypatch, modules, replies, still=None):
    monkeypatch.setenv("EVP_LAYOUT_CHECK", "1")
    vision = FakeVision(replies)
    monkeypatch.setattr(generate.llm, "ask_images", vision)
    frames = []

    def st(proj, frame, out, log=None, timeout=300):
        frames.append(frame)
        (still or _ok_still)(proj, frame, out, log, timeout)

    job, proj, logs, fake = _run(tmp_path, monkeypatch, modules, still=st, typecheck=_tc_strict)
    res = json.loads((proj / ".check" / "result.json").read_text())
    return job, proj, logs, fake, vision, res, frames


def test_layout_frames():
    assert project.layout_frames(171) == [21, 43, 77, 128, 159]
    assert project.layout_frames(281) == [34, 70, 126, 211, 269]  # TCP step2: title covered f12-45
    assert project.layout_frames(30) == [12, 14, 18, 22]  # never before frame 12
    assert project.layout_frames(10) == [9]


def test_parse_review():
    r = generate.parse_review("```json\n" + ISSUE + "\n```")
    assert r == {"ok": False, "issues": [{"kind": "clipped", "where": "left edge: earth circle",
                                         "fix": "move it right by 10% of width"}]}
    r = generate.parse_review(json.dumps({"ok": True, "issues": [{"kind": "weird", "where": "x"}]}))
    assert r["ok"] is False and r["issues"][0]["kind"] == "other"  # issues win over ok
    with pytest.raises(ValueError):
        generate.parse_review(json.dumps({"ok": False, "issues": []}))
    with pytest.raises(ValueError):
        generate.parse_review(json.dumps({"ok": "yes", "issues": []}))


def test_layout_ok_path(tmp_path, monkeypatch):
    job, proj, logs, fake, vision, res, frames = _run_layout(
        tmp_path, monkeypatch, {"scene_0": [GOOD], "scene_1": [GOOD]},
        {"scene_0": [OK], "scene_1": [OK]})
    assert [s["layout"] for s in job["scenes"]] == ["ok", "ok"]
    assert len(vision.calls) == 2 and all(n == 5 for _, _, n in vision.calls)  # ONE call, 5 stills
    # midpoint stills 45, 135, then the layout stills at 12/22/40/68/78 of each 90-frame scene
    assert frames == [45, 135, 12, 22, 40, 68, 78, 102, 112, 130, 158, 168]
    assert res["layout"]["scene_1"]["status"] == "ok" and res["layout_calls"] == 2
    assert "frames 12, 22, 40, 68, 78" in vision.calls[0][1] and "1920x1080" in vision.calls[0][1]
    assert "the first 2 during the entrance" in vision.calls[0][1]
    assert "REWRITE" not in vision.calls[0][1]
    assert len(fake.calls) == 3  # no rewrite


def test_layout_fix_path(tmp_path, monkeypatch):
    job, proj, logs, fake, vision, res, frames = _run_layout(
        tmp_path, monkeypatch, {"scene_0": [GOOD], "scene_1": [GOOD, GOOD2]},
        {"scene_0": [OK], "scene_1": [ISSUE, OK]})
    assert [s["layout"] for s in job["scenes"]] == ["ok", "fixed"]
    assert (proj / "src" / "scenes" / "scene_1.tsx").read_text() == GOOD2
    rec = res["layout"]["scene_1"]
    assert rec["status"] == "fixed" and rec["before"][0]["kind"] == "clipped" and rec["after"] == []
    retry = [u for s, u in fake.calls if "FAILED" in u]
    assert len(retry) == 1 and "left edge: earth circle" in retry[0] and "Layout review" in retry[0]
    assert '"TITLE"' in retry[0]  # previous module goes along
    assert len(vision.calls) == 3 and job["scenes"][1]["card"] != project.FALLBACK_CARD
    assert "layout_issues" not in job["scenes"][1]
    # the confirmation pass re-checks the earlier issues explicitly
    assert "REWRITE" in vision.calls[2][1] and "left edge: earth circle" in vision.calls[2][1]


@pytest.mark.parametrize("rewrite,second", [(TYPE_ERR, OK), (GOOD2, TWO), (GOOD2, ISSUE)])
def test_layout_rewrite_worse_keeps_old(tmp_path, monkeypatch, rewrite, second):
    # tsc failure, more issues, or as many issues -> the previous working module stays.
    job, proj, logs, fake, vision, res, frames = _run_layout(
        tmp_path, monkeypatch, {"scene_0": [GOOD], "scene_1": [GOOD, rewrite]},
        {"scene_0": [OK], "scene_1": [ISSUE, second]})
    assert (proj / "src" / "scenes" / "scene_1.tsx").read_text() == GOOD
    rec = res["layout"]["scene_1"]
    assert rec["status"] == "issues_left" and rec["rewrite"] == "reverted"
    assert rec["after"] == rec["before"] and len(rec["after"]) == 1
    assert job["scenes"][1]["layout"] == "issues_left"
    assert job["scenes"][1]["card"] == "data/chart-live-moves"  # never the title card
    assert job["scenes"][1]["layout_issues"] == ["clipped: left edge: earth circle"]
    assert any("remaining" in x for x in logs)
    assert frames[-1] == 135  # .check/scene_1.png re-rendered from the restored module
    if rewrite == TYPE_ERR:
        assert len(vision.calls) == 2 and "tsc" in rec["note"]


def test_layout_unconfirmed_rewrite_is_not_fixed(tmp_path, monkeypatch):
    # the confirmation reply is unreadable -> no "fixed"; the previous module stays
    job, proj, logs, fake, vision, res, frames = _run_layout(
        tmp_path, monkeypatch, {"scene_0": [GOOD], "scene_1": [GOOD, GOOD2]},
        {"scene_0": [OK], "scene_1": [ISSUE, "looks fine to me"]})
    rec = res["layout"]["scene_1"]
    assert rec["status"] == "issues_left" and rec["rewrite"] == "reverted"
    assert "not confirmed" in rec["note"]
    assert (proj / "src" / "scenes" / "scene_1.tsx").read_text() == GOOD


def test_layout_fewer_issues_is_improved(tmp_path, monkeypatch):
    job, proj, logs, fake, vision, res, frames = _run_layout(
        tmp_path, monkeypatch, {"scene_0": [GOOD], "scene_1": [GOOD, GOOD2]},
        {"scene_0": [OK], "scene_1": [TWO, ISSUE]})
    rec = res["layout"]["scene_1"]
    assert rec["status"] == "improved" and rec["rewrite"] == "kept" and len(rec["after"]) == 1
    assert rec["rewrite_issues"] == rec["after"] and len(rec["before"]) == 2
    assert (proj / "src" / "scenes" / "scene_1.tsx").read_text() == GOOD2
    assert job["scenes"][1]["layout"] == "improved"
    assert job["scenes"][1]["layout_issues"] == ["clipped: left edge: earth circle"]
    assert frames[-1] != 135  # kept rewrite: no restore still


def test_layout_bad_json_is_ok(tmp_path, monkeypatch):
    job, proj, logs, fake, vision, res, frames = _run_layout(
        tmp_path, monkeypatch, {"scene_0": [GOOD], "scene_1": [GOOD]},
        {"scene_0": ["I think it looks fine"], "scene_1": ['{"ok": false, "issues": ']})
    assert [s["layout"] for s in job["scenes"]] == ["ok", "ok"]
    assert "unparsable" in res["layout"]["scene_0"]["note"]
    assert any("unparsable" in x for x in logs) and len(fake.calls) == 3


def test_layout_call_error_is_skipped(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bedrock down")
    monkeypatch.setenv("EVP_LAYOUT_CHECK", "1")
    monkeypatch.setattr(generate.llm, "ask_images", boom)
    job, proj, logs, fake = _run(tmp_path, monkeypatch, {"scene_0": [GOOD], "scene_1": [GOOD]},
                                 still=_ok_still, typecheck=_tc_strict)
    assert [s["layout"] for s in job["scenes"]] == ["skipped", "skipped"]
    assert [s["card"] for s in job["scenes"]] == ["typography/paper-title-card", "data/chart-live-moves"]


def test_layout_switch_off(tmp_path, monkeypatch):
    vision = FakeVision({"scene_0": [ISSUE], "scene_1": [ISSUE]})
    monkeypatch.setattr(generate.llm, "ask_images", vision)
    job, proj, logs, fake = _run(tmp_path, monkeypatch, {"scene_0": [GOOD], "scene_1": [GOOD]},
                                 still=_ok_still, typecheck=_tc_strict)  # autouse: EVP_LAYOUT_CHECK=0
    assert vision.calls == [] and [s["layout"] for s in job["scenes"]] == ["skipped", "skipped"]
    res = json.loads((proj / ".check" / "result.json").read_text())
    assert res["layout"]["scene_0"]["reason"] == "EVP_LAYOUT_CHECK=0" and res["layout_calls"] == 0


def test_ask_images_downscales(monkeypatch):
    import io

    from PIL import Image

    from app import llm
    sent = {}

    class Client:
        def converse_stream(self, **kw):
            sent.update(kw)
            return {"stream": [{"contentBlockDelta": {"delta": {"text": OK}}},
                               {"messageStop": {"stopReason": "end_turn"}}]}
    monkeypatch.setattr(llm, "_bedrock", lambda: Client())
    buf = io.BytesIO()
    Image.new("RGB", (1080, 1920), "red").save(buf, "PNG")
    assert llm.ask_images("sys", "look", [buf.getvalue()] * 3) == OK
    content = sent["messages"][0]["content"]
    assert [list(c) for c in content] == [["image"]] * 3 + [["text"]]
    with Image.open(io.BytesIO(content[0]["image"]["source"]["bytes"])) as im:
        assert im.format == "JPEG" and im.size == (540, 960)
    assert sent["modelId"] == llm.MODEL_ID


def test_pinned_cards_skip_picker_and_timing(tmp_path, monkeypatch):
    monkeypatch.setenv("EVP_REMOTION_PIN_CARDS", "1")
    d = tmp_path / "job"
    (d / "remotion" / ".check").mkdir(parents=True)
    (d / "remotion" / ".check" / "result.json").write_text(json.dumps({"look": {"accent": "#123456"}}))
    job = _job(2)
    job["scenes"][0]["card"], job["scenes"][1]["card"] = "data/chart-live-moves", "opening/orbit-ring-title-open"
    fake = FakeLLM({}, {"scene_0": [GOOD], "scene_1": [GOOD]})
    monkeypatch.setattr(generate.llm, "ask", fake)
    monkeypatch.setattr(render, "still", _ok_still)
    monkeypatch.setattr(render, "typecheck", lambda *a, **k: None)
    proj = project.write_project(job, d, [90, 90], 1920, 1080, images={}, clips={},
                                 background=None, context="", log=lambda m: None)
    assert not any(s == generate.PICK_SYSTEM for s, u in fake.calls) and len(fake.calls) == 2
    assert [s["card"] for s in job["scenes"]] == ["data/chart-live-moves", "opening/orbit-ring-title-open"]
    res = json.loads((proj / ".check" / "result.json").read_text())
    assert res["look"]["accent"] == "#123456" and res["timing"]["pinned_cards"] is True
    assert res["timing"]["main"]["generate"]["n"] == 2 and res["timing"]["main"]["still"]["n"] == 2
