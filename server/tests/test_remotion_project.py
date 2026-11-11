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
    # Real tsc + real still: scene_0 renders, scene_1 is blank (flat colour) both times -> fallback.
    job, proj, logs, fake = _run(tmp_path, monkeypatch, {"scene_0": [GOOD], "scene_1": [BLANK, BLANK]})
    assert [s["card"] for s in job["scenes"]] == ["typography/paper-title-card", project.FALLBACK_CARD]
    assert (proj / ".check" / "scene_0.png").exists()
    render.typecheck(proj)  # the final project compiles
    assert any("flat colour" in u for s, u in fake.calls)
