// Tests for the shot compiler and checks (part B). Run: server/app/previs/js/test.sh
// (test.sh runs from server/app/previs/js, so fixture paths are relative to it).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { partTransforms } from "../core/rig";
import type { CheckIssue, ModelMeta, PrevisPlan, ShotSpec, WordEntry } from "../core/types";
import { checkAxis, checkShot, plan, planSvg, type RShot } from "./check";
import { compact, compileShot, stripPunct, type AssetInfo } from "./compile";
import { catmull, ease, lensToVfov } from "./ease";
import { buildTestAsset } from "./testkit";

const EX = "../examples/four_stroke";
const J = (f: string) => JSON.parse(readFileSync(`${EX}/${f}`, "utf8"));
const PLAN: PrevisPlan = J("plan.json");
const WORDS: WordEntry[] = J("words.json");
const DUR: number[] = J("durations.json");
const SCENES: { id: string; narration: string }[] = J("scenes.json");
const built = buildTestAsset((PLAN.assets[0] as { spec: never }).spec);
const META: ModelMeta = built.meta;
const ASSETS: Record<string, AssetInfo> = { "@four_stroke": { meta: META, glb: "previs/four_stroke.glb", id: "four_stroke" } };

let n = 0;
const test = (name: string, fn: () => void) => {
  fn();
  n++;
  console.log("ok", name);
};
const near = (a: number, b: number, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

const compile = (shot: string, plan: PrevisPlan = PLAN, durations = DUR) =>
  compileShot({ plan, shot, assets: ASSETS, words: WORDS, durations, order: SCENES.map((s) => s.id), width: 1920, height: 1080 });

/** The fixture plan with shot `id` replaced (other shots unchanged). */
const withShot = (shot: ShotSpec): PrevisPlan => ({ ...PLAN, shots: PLAN.shots.map((s) => (s.id === shot.id ? shot : s)) });
const codes = (xs: CheckIssue[]) => xs.map((x) => x.code);
const metas = { eng: META };
const run = (shot: ShotSpec) => {
  const c = compile(shot.id, withShot(shot));
  return [...c.issues, ...checkShot(c.shot as RShot, metas)];
};
const base = (over: Partial<ShotSpec> = {}): ShotSpec => ({
  id: "s02",
  set: "bench",
  camera: {
    keys: [
      { at: { mark: "start" }, pos: [0.6, 0.5, 0.9], look: "eng.chamber" },
      { at: { mark: "end" }, pos: [0.5, 0.5, 0.95], look: "eng.chamber" },
    ],
  },
  ...over,
});

// ---------------------------------------------------------------- time anchors

test("the five stroke words land within 1 frame of words.json", () => {
  const sceneStart = (id: string) => DUR.slice(0, SCENES.findIndex((s) => s.id === id)).reduce((a, b) => a + b, 0) / 30;
  /** independent lookup: first char of the word in this scene's words.json entries */
  const wordStart = (scene: string, word: string) => {
    const ws = WORDS.filter((w) => w.scene === scene && stripPunct(w.text));
    const txt = ws.map((w) => w.text).join("");
    const i = txt.indexOf(word);
    assert.ok(i >= 0, word);
    return ws[i].start;
  };
  const seen = new Set<string>();
  for (const sid of ["s02", "s03"]) {
    const { shot, issues } = compile(sid);
    assert.equal(issues.filter((i) => i.severity === "error").length, 0, JSON.stringify(issues));
    for (const a of shot.anchors) {
      const w = a.anchor.word;
      if (!w) continue;
      const t = wordStart(sid, w);
      assert.equal(a.word_time, t, `${sid} ${w} word time`);
      const want = (t - sceneStart(sid) + (a.anchor.offset ?? 0)) * 30;
      assert.ok(Math.abs(a.frame - want) <= 1, `${sid} ${a.where} ${w}: frame ${a.frame} vs ${want}`);
      seen.add(w);
    }
  }
  for (const w of ["进气", "压缩", "点火", "做功", "排气"]) assert.ok(seen.has(w), `anchor ${w} resolved`);
});

test("word lookup: punctuation ignored, occurrence, edge, offset, clamp, not found", () => {
  const mk = (at: object) =>
    compile("s02", withShot(base({ actions: [{ op: "highlight", target: "eng.piston", at: at as never }] }))).shot.anchors[0];
  const a1 = mk({ word: "活塞" });
  const a2 = mk({ word: "活塞", occurrence: 2 });
  assert.ok(a2.frame > a1.frame + 30, "second 活塞 is later");
  const p = mk({ word: "进气，活塞" });
  const q = mk({ word: "进气活塞" });
  assert.equal(p.frame, q.frame);
  assert.ok(p.frame > 0);
  const e = mk({ word: "活塞", edge: "end" });
  near(e.word_time! - a1.word_time!, 0.42, 1e-9); // two chars of 0.21 s
  const o = mk({ word: "活塞", offset: 0.5 });
  assert.equal(o.frame, a1.frame + 15);
  const c = compile("s02", withShot(base({ actions: [{ op: "highlight", target: "eng", at: { t: 100 } }] })));
  assert.deepEqual(codes(c.issues), ["anchor_clamped"]);
  assert.equal(c.shot.anchors[0].frame, c.shot.frames - 1);
  const nf = compile("s02", withShot(base({ actions: [{ op: "highlight", target: "eng", at: { word: "涡轮" } }] })));
  assert.deepEqual(codes(nf.issues), ["anchor_not_found"]);
  const two = compile("s02", withShot(base({ actions: [{ op: "highlight", target: "eng", at: { t: 1, frame: 3 } }] })));
  assert.deepEqual(codes(two.issues), ["anchor_not_found"]);
});

// ---------------------------------------------------------------- easing / paths

test("easing endpoints, symmetry, hold", () => {
  for (const k of ["linear", "in", "out", "inOut", "inOutCubic"] as const) {
    near(ease(k, 0), 0);
    near(ease(k, 1), 1);
    let prev = -1;
    for (let u = 0; u <= 1.0001; u += 0.05) {
      const v = ease(k, u);
      assert.ok(v >= prev - 1e-12, `${k} monotonic`);
      prev = v;
    }
  }
  near(ease("inOut", 0.5), 0.5);
  near(ease("inOutCubic", 0.5), 0.5);
  near(ease("in", 0.5), 0.25);
  near(ease("out", 0.5), 0.75);
  assert.equal(ease("hold", 0.99), 0);
  assert.equal(ease("hold", 1), 1);
  near(lensToVfov(35), 37.849289, 1e-5);
});

test("centripetal catmull-rom: through the keys, straight on a line", () => {
  const p0: [number, number, number] = [0, 0, 0];
  const p1: [number, number, number] = [1, 0, 0];
  const p2: [number, number, number] = [2, 1, 0];
  const p3: [number, number, number] = [4, 1, 0];
  assert.deepEqual(catmull(p0, p1, p2, p3, 0).map((x) => +x.toFixed(9)), p1);
  assert.deepEqual(catmull(p0, p1, p2, p3, 1).map((x) => +x.toFixed(9)), p2);
  const m = catmull(null, [0, 0, 0], [2, 0, 0], null, 0.5);
  near(m[0], 1);
  near(m[1], 0);
  // duplicate points do not produce NaN
  assert.ok(catmull([1, 1, 1], [1, 1, 1], [1, 1, 1], [1, 1, 1], 0.3).every(Number.isFinite));
});

test("camera cut: hold before, jump on the cut frame, cuts listed", () => {
  const { shot } = compile("s03");
  assert.deepEqual(shot.cuts, [32]);
  assert.deepEqual(shot.camera[31].slice(0, 3), [0, 0.5, 0.6]); // key 0 held until the cut
  assert.deepEqual(shot.camera[32].slice(0, 3), [0, 0.5, 0.42]); // key 1 exactly on the cut
  near(shot.camera[32][6], lensToVfov(70), 1e-5);
  assert.ok(checkShot(shot as RShot, metas).every((i) => i.code !== "camera_jump"), "a cut is not a jump");
});

test("orbit keys around the same point interpolate on the sphere", () => {
  const { shot } = compile("s01");
  const c = [0, 0.2, 0];
  for (const f of [0, 60, 130, 200, 259]) {
    const r = shot.camera[f];
    const d = Math.hypot(r[0] - c[0], r[1] - c[1], r[2] - c[2]);
    assert.ok(d <= 1.4 + 1e-6 && d >= 1.0 - 1e-6, `radius ${d}`);
  }
  near(Math.hypot(shot.camera[0][0], shot.camera[0][1] - 0.2, shot.camera[0][2]), 1.4, 1e-5);
});

// ---------------------------------------------------------------- tracks

test("constant arrays are stored as one entry; moving parts per frame", () => {
  assert.deepEqual(compact([[1, 2], [1, 2], [1, 2]]), [[1, 2]]);
  assert.equal(compact([[1], [2]]).length, 2);
  const { shot } = compile("s02");
  const o = shot.objects[0];
  assert.equal(o.xform.length, 1);
  assert.equal(o.parts.crankcase.length, 1);
  assert.equal(o.parts.exhaust_valve.length, 1); // closed during intake + compression
  assert.equal(o.parts.crank.length, shot.frames);
  assert.equal(o.parts.intake_valve.length, shot.frames);
  assert.equal(shot.camera.length, shot.frames);
});

test("part tracks are core/rig partTransforms of the pose", () => {
  const { shot } = compile("s02");
  const o = shot.objects[0];
  // crank goes 0 -> 180 linearly between 进气 (32) and 压缩 (152)
  const f = 92;
  const crank = (180 * (f - 32)) / (152 - 32);
  const want = partTransforms(META.rig, { crank });
  for (const [id, rows] of Object.entries(o.parts)) {
    const got = rows[Math.min(f, rows.length - 1)];
    want[id].forEach((x, k) => near(got[k], x, 2e-6));
  }
  // intake valve is open (slid down) at mid-intake, exhaust closed
  assert.ok(o.parts.intake_valve[f][1] < META.rig.rest.intake_valve.at[1] - 0.005);
  near(o.parts.exhaust_valve[0][1], META.rig.rest.exhaust_valve.at[1]);
});

test("spin and chained poses hold their value", () => {
  const s01 = compile("s01").shot.objects[0].parts.crank;
  near(s01[30][5], ((180 * 1) * Math.PI) / 180 - 2 * Math.PI * 0 + 0, 1e-5); // 180 deg after 1 s
  const s03 = compile("s03").shot;
  const crank = s03.objects[0].parts.crank;
  // 360 (= 0 mod 360 for the rotation) at start, 540 at 排气, 720 at the end
  const want540 = partTransforms(META.rig, { crank: 540 }).crank;
  crank[158].forEach((x, k) => near(x, want540[k], 2e-6));
});

// ---------------------------------------------------------------- checks: every code

test("fixture shots: no errors", () => {
  for (const id of ["s01", "s02", "s03"]) {
    const c = compile(id);
    const all = [...c.issues, ...checkShot(c.shot as RShot, metas)];
    assert.deepEqual(all.filter((i) => i.severity === "error"), [], id);
  }
});

test("camera_inside", () => {
  const s = base();
  s.camera.keys[0].pos = [0, 0.12, 0]; // inside the crankcase
  s.camera.keys[1].pos = [0, 0.12, 0.05];
  assert.ok(codes(run(s)).includes("camera_inside"));
});

test("look_out_of_frame", () => {
  const s = base();
  s.camera.keys[0] = { at: { mark: "start" }, pos: [0, 0.45, 0], look: "eng.chamber" }; // camera at the look point
  assert.ok(codes(run(s)).includes("look_out_of_frame"));
});

test("subject_out_of_frame", () => {
  const s = base({ actions: [{ op: "label", text: "远处", attach: [3, 0.3, 0], at: { mark: "start" } }] });
  const c = run(s);
  assert.ok(codes(c).includes("subject_out_of_frame"), JSON.stringify(c));
  assert.ok(!codes(run(base({ actions: [{ op: "label", text: "燃烧室", attach: "eng.chamber", at: { mark: "start" } }] }))).includes("subject_out_of_frame"));
});

test("subject_occluded", () => {
  // the crank sits inside the solid crankcase box: hidden without a section, visible with one
  const s = base({ actions: [{ op: "highlight", target: "eng.crank", at: { mark: "start" } }] });
  s.camera.keys.forEach((k) => ((k.look = "eng.crank_center"), (k.pos = [0.3, 0.12, 0.9]))); // low, in front
  assert.ok(codes(run(s)).includes("subject_occluded"));
  // section away the front of the crankcase (kept: z <= 0); the crank (z -0.04..-0.02) is then visible
  s.actions!.push({ op: "section", targets: ["eng"], normal: [0, 0, -1], offset: 0, at: { mark: "start" } });
  s.camera.keys.forEach((k) => (k.pos = [0, 0.12, 0.9]));
  assert.ok(!codes(run(s)).includes("subject_occluded"));
});

test("camera_jump and fast_motion", () => {
  const s = base();
  s.camera.keys = [
    { at: { mark: "start" }, pos: [0.6, 0.5, 0.9], look: "eng.chamber" },
    { at: { frame: 40 }, pos: [0.6, 0.5, 0.9], look: "eng.chamber" },
    { at: { frame: 41 }, pos: [-0.9, 0.5, -0.6], look: "eng.chamber", ease: "linear" },
  ];
  assert.ok(codes(run(s)).includes("camera_jump"));
  s.camera.keys[2].cut = true;
  assert.ok(!codes(run(s)).includes("camera_jump"));
  const f: ShotSpec = base();
  f.camera.keys = [
    { at: { mark: "start" }, orbit: { around: "eng.chamber", r: 0.9, az: 0, el: 10 }, look: "eng.chamber" },
    { at: { t: 2 }, orbit: { around: "eng.chamber", r: 0.9, az: 359, el: 10 }, look: "eng.chamber", ease: "linear" },
  ];
  // orbit 180 deg/s: the view direction turns 6 deg per frame (fast, not a jump)
  const r = codes(run(f));
  assert.ok(r.includes("fast_motion") && !r.includes("camera_jump"), r.join());
});

test("axis_crossing", () => {
  const front = compile("s01").shot as RShot; // ends at +z
  const behind = base();
  behind.camera.keys.forEach((k) => (k.pos = [0.3, 0.5, -0.9]));
  const b = compile("s02", withShot(behind)).shot as RShot;
  assert.deepEqual(codes(checkAxis(front, b, metas)), ["axis_crossing"]);
  assert.deepEqual(checkAxis(front, compile("s02").shot as RShot, metas), []);
});

test("cut_too_close", () => {
  const s = base();
  s.camera.keys = [
    { at: { mark: "start" }, pos: [0.6, 0.5, 0.9], look: "eng.chamber" },
    { at: { frame: 40 }, pos: [0.5, 0.5, 0.9], look: "eng.chamber", cut: true },
    { at: { frame: 50 }, pos: [0.6, 0.4, 0.9], look: "eng.chamber", cut: true },
  ];
  assert.ok(codes(run(s)).includes("cut_too_close"));
});

test("dof_conflict, unknown_dof, unknown_target, unknown_point", () => {
  const c = run(
    base({
      actions: [
        { op: "pose", target: "eng", dof: "crank", to: 180, at: { t: 1 }, until: { t: 4 } },
        { op: "spin", target: "eng", dof: "crank", deg_per_s: 90, at: { t: 3 }, until: { t: 5 } },
        { op: "pose", target: "eng", dof: "piston_y", to: 0.1, at: { t: 1 } },
        { op: "highlight", target: "eng.turbo", at: { t: 1 } },
        { op: "spin", target: "car", dof: "crank", deg_per_s: 10, at: { t: 1 } },
        { op: "label", text: "?", attach: "eng.nowhere", at: { t: 1 } },
      ],
    }),
  );
  for (const k of ["dof_conflict", "unknown_dof", "unknown_target", "unknown_point"]) assert.ok(codes(c).includes(k), k);
  assert.equal(c.filter((x) => x.code === "unknown_target").length, 2);
  // touching ranges (until == next at) are not a conflict
  const ok = run(
    base({
      actions: [
        { op: "pose", target: "eng", dof: "crank", to: 180, at: { t: 1 }, until: { t: 4 } },
        { op: "pose", target: "eng", dof: "crank", to: 360, at: { t: 4 }, until: { t: 6 } },
      ],
    }),
  );
  assert.ok(!codes(ok).includes("dof_conflict"));
});

// ---------------------------------------------------------------- testkit + plan drawing

test("testkit GLB + meta for the fixture", () => {
  const dv = new DataView(built.glb.buffer, built.glb.byteOffset);
  assert.equal(dv.getUint32(0, true), 0x46546c67);
  assert.equal(dv.getUint32(8, true), built.glb.byteLength);
  assert.equal(META.parts.length, 13);
  const [w, h, d] = META.size;
  for (const [got, want] of [[w, 0.3], [h, 0.52], [d, 0.22]]) assert.ok(Math.abs(got - want) / want < 0.02, `${got} vs ${want}`);
  near(META.bbox.min[1], 0, 1e-6);
  assert.deepEqual(META.anchor_parts.piston_top, { part: "piston", at: [0, 0.03, 0] });
});

test("plan drawing: boxes, camera path, key numbers, anchors", () => {
  const { shot } = compile("s03");
  const d = plan(shot as RShot, metas);
  assert.ok(d.items.filter((i) => i.t === "rect").length >= 12);
  assert.ok(d.items.some((i) => i.t === "text" && i.s.startsWith("点火")));
  assert.ok(d.items.some((i) => i.t === "text" && i.s === "3"));
  assert.ok(planSvg(d).startsWith("<svg"));
});

console.log(`shot tests: ${n} passed`);
