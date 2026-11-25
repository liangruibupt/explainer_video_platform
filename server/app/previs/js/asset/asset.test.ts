// Tests for part A (asset builder, GLB I/O, normalize, validate). Run: server/app/previs/js/test.sh
import assert from "node:assert/strict";
import { Document, NodeIO } from "@gltf-transform/core";
import * as THREE from "three";
import plan from "../../examples/four_stroke/plan.json";
import type { AssetSpec, ModelMeta } from "../core/types";
import { buildAsset } from "./build";
import { readGlb, writeGlb } from "./glb";
import { mergeAndWeld } from "./geometry";
import { type Model, worldMatrices } from "./model";
import { normalizeGlb, parseTarget } from "./normalize";
import { validateAsset } from "./validate";

let n = 0;
const tests: [string, () => void | Promise<void>][] = [];
const test = (name: string, fn: () => void | Promise<void>) => tests.push([name, fn]);
const near = (a: number, b: number, eps: number, what = "") => assert.ok(Math.abs(a - b) <= eps, `${what} ${a} != ${b} (±${eps})`);

const SPEC = (plan as unknown as { assets: { spec: AssetSpec }[] }).assets[0].spec;
const spec = (): AssetSpec => JSON.parse(JSON.stringify(SPEC));
const part = (s: AssetSpec, id: string) => s.parts.find((p) => p.id === id)!;
const codes = (s: AssetSpec) => buildAsset(s).issues.map((i) => i.code);

test("four_stroke builds: 13 parts, dims within 2 %, on the ground", () => {
  const r = buildAsset(spec());
  assert.deepEqual(r.issues, []);
  const m = r.meta!;
  assert.equal(m.parts.length, 13);
  assert.equal(r.model!.parts.length, 13);
  const d = SPEC.dims as { w: number; h: number; d: number };
  near(m.size[0], d.w, 0.02 * d.w, "w");
  near(m.size[1], d.h, 0.02 * d.h, "h");
  near(m.size[2], d.d, 0.02 * d.d, "d");
  near(m.bbox.min[1], 0, 1e-6, "ground contact");
  assert.ok(m.tris > 100 && m.tris < 200000, `tris ${m.tris}`);
  assert.equal(m.parts.find((p) => p.id === "rod")!.parent, "rod_pivot");
  assert.deepEqual(m.rig.joints.intake_valve.axis, [0, -1, 0]);
  near(m.rig.params.bore, 0.08, 0);
  assert.equal(m.source.type, "parametric");
});

test("four_stroke anchors are resolved", () => {
  const m = buildAsset(spec()).meta!;
  assert.deepEqual(Object.keys(m.anchors).sort(), ["chamber", "crank_center", "exhaust", "intake", "piston_top", "spark"]);
  for (const v of Object.values(m.anchors)) assert.ok(v.length === 3 && v.every(Number.isFinite));
  near(m.anchors.spark[1], 0.5, 1e-9, "spark y");
  near(m.anchors.piston_top[1], 0.2 + 0.045 + 0.16 + 0.03, 1e-9, "piston_top y");
  assert.deepEqual(Object.keys(m.anchor_parts).sort(), ["exhaust", "intake", "piston_top"]);
  assert.equal(m.anchor_parts.piston_top.part, "piston");
});

test("linkage: crank pin stays on the rod's lower end for every crank angle", () => {
  const r = buildAsset(spec());
  for (const crank of [0, 37, 90, 180, 270, 333, 600]) {
    const W = worldMatrices(r.model!, { crank });
    const pin = new THREE.Vector3().applyMatrix4(W.get("crank_pin")!);
    const rodEnd = new THREE.Vector3(0, -0.16 / 2, 0).applyMatrix4(W.get("rod")!);
    near(pin.distanceTo(rodEnd), 0, 1e-9, `crank ${crank}`);
  }
  const bdc = new THREE.Vector3().applyMatrix4(worldMatrices(r.model!, { crank: 180 }).get("piston")!);
  near(bdc.y, 0.2 + 0.16 - 0.045, 1e-9, "piston at BDC");
});

test("GLB: glTF 2.0 binary, node names = part ids, rest pose, evp_slot extras", async () => {
  const r = buildAsset(spec());
  const bytes = await writeGlb(r.model!, "four_stroke");
  assert.equal(Buffer.from(bytes.slice(0, 4)).toString("latin1"), "glTF");
  assert.equal(new DataView(bytes.buffer, bytes.byteOffset).getUint32(4, true), 2);
  const g = await readGlb(bytes);
  assert.deepEqual([...g.nodeNames].sort(), SPEC.parts.map((p) => p.id).sort());
  assert.equal(new Set(g.meshes.map((m) => m.node)).size, 12); // rod_pivot is a group
  const pin = g.nodes.find((x) => x.name === "crank_pin")!;
  assert.equal(pin.parent, "crank");
  const wp = new THREE.Vector3().setFromMatrixPosition(pin.world);
  near(wp.y, 0.2 + 0.045, 1e-6, "crank_pin rest y");
  near(wp.z, 0, 1e-6, "crank_pin rest z");
  const doc: Document = await new NodeIO().readBinary(bytes);
  const slots = new Set(doc.getRoot().listMaterials().map((m) => (m.getExtras() as { evp_slot: string }).evp_slot));
  assert.deepEqual([...slots].sort(), ["accent1", "accent2", "accent3", "accent4", "clay", "glass"]);
  const glass = doc.getRoot().listMaterials().find((m) => (m.getExtras() as { evp_slot: string }).evp_slot === "glass")!;
  assert.equal(glass.getAlphaMode(), "BLEND");
  near(glass.getBaseColorFactor()[3], 0.35, 1e-6, "glass opacity");
  assert.deepEqual(await validateAsset(bytes, r.meta!), []);
});

// Each broken spec must produce its issue code.
const broken: [string, (s: AssetSpec) => void][] = [
  ["expr_error", (s) => (part(s, "cylinder").r_outer = "bore/2 + foo")],
  ["unknown_parent", (s) => (part(s, "piston").parent = "ghost")],
  ["cycle", (s) => (part(s, "crank").parent = "crank_pin")],
  ["duplicate_id", (s) => s.parts.push({ ...part(s, "head") })],
  ["bad_shape_param", (s) => (part(s, "cylinder").r_inner = "bore/2 + 0.03")],
  ["bad_id", (s) => (part(s, "head").id = "Head-1")],
  ["dims_mismatch", (s) => (s.dims.w = 0.4)],
  ["below_ground", (s) => (part(s, "crankcase").at = [0, 0.1, 0])],
  ["floating", (s) => (part(s, "crankcase").at = [0, 0.125, 0])],
  ["dof_range", (s) => (part(s, "spark_plug").joint = { type: "slide", axis: "y", dof: "crank" })],
  ["too_many_tris", (s) => (s.max_tris = 100)],
  ["bad_anchor", (s) => (s.anchors!.oops = "no_such_part")],
  ["nan", (s) => (part(s, "crankcase").size = [1e39, 0.24, 0.22])], // overflows float32 vertices
];
for (const [code, breakIt] of broken)
  test(`broken spec -> ${code}`, () => {
    const s = spec();
    breakIt(s);
    const r = buildAsset(s);
    assert.ok(r.issues.some((i) => i.code === code), `${code} not in ${JSON.stringify(r.issues)}`);
    assert.equal(r.meta, undefined);
    for (const i of r.issues) assert.ok(i.severity === "error" && i.message.length > 0);
  });

test("other shapes build with exact sizes", () => {
  const s: AssetSpec = {
    id: "shapes",
    name: "图元",
    version: 1,
    units: "m",
    params: { k: { value: 0.1 } },
    dims: { w: 3.0, h: 0.45, d: 0.4 },
    parts: [
      { id: "s", shape: "sphere", r: "k*2", at: [0, 0.2, 0] },
      { id: "c", shape: "capsule", r: 0.1, h: 0.2, at: [0.5, 0.2, 0] },
      { id: "t", shape: "torus", r: 0.15, tube: 0.05, at: [1.0, 0.05, 0] },
      { id: "k", shape: "cone", r: 0.1, h: 0.4, axis: "x", at: [-0.5, 0.1, 0] },
      { id: "e", shape: "extrude", profile: [[0, 0], [0.2, 0], [0, 0.3]], depth: 0.1, bevel: 0.01, at: [-0.9, 0, -0.05] },
      { id: "l", shape: "lathe", profile: [[0, 0], [0.1, 0], [0.05, 0.3]], at: [1.0, 0.1, 0], parent: "t" },
      { id: "b", shape: "box", size: [0.05, 0.05, 0.05], at: [0, 0.025, 0.15], array: { kind: "radial", count: 4, axis: "y" } },
    ],
  };
  const r = buildAsset(s);
  assert.deepEqual(r.issues, []);
  const box = (id: string) => r.meta!.parts.find((p) => p.id === id)!.bbox;
  const sz = (id: string) => box(id).max.map((x, i) => x - box(id).min[i]);
  near(sz("s")[1], 0.4, 1e-3, "sphere");
  near(sz("c")[1], 0.4, 1e-3, "capsule");
  near(sz("t")[0], 0.4, 1e-3, "torus w");
  near(sz("t")[1], 0.1, 1e-3, "torus h");
  near(sz("k")[0], 0.4, 1e-3, "cone along x");
  near(sz("e")[2], 0.1, 1e-6, "extrude depth");
  near(sz("e")[0], 0.2, 1e-6, "extrude bevel stays inside the profile");
  near(sz("b")[0], 0.35, 1e-6, "radial array spans 2*0.15 + 0.05");
});

test("normalize-glb: scale to target, ground, centre, decimate, one part", async () => {
  const g = mergeAndWeld([new THREE.SphereGeometry(3, 200, 100).toNonIndexed().deleteAttribute("uv")]);
  g.scale(1, 2, 0.5).translate(5, 7, -2); // 6 x 12 x 3 units, off origin
  const raw: Model = { parts: [{ id: "blob", parent: null, geom: g, slot: "clay", opacity: 1 }], rig: { dofs: {}, drivers: {}, joints: {}, params: {}, rest: { blob: { at: [1, 0, 0], rot: [0, 0, 0] } } } };
  const bytes = await writeGlb(raw, "raw");
  const r = await normalizeGlb(bytes, parseTarget("h:0.3"), { type: "upload", filename: "blob.glb" }, 5000);
  assert.deepEqual(r.issues, []);
  assert.ok(r.inputTris > 30000);
  const m = r.meta!;
  assert.ok(m.tris <= 5000 && m.tris > 1000, `tris ${m.tris}`);
  near(m.size[1], 0.3, 0.3 * 0.02, "h");
  near(m.size[0] / m.size[1], 0.5, 0.02, "aspect kept");
  near(m.bbox.min[1], 0, 1e-6, "ground");
  near((m.bbox.min[0] + m.bbox.max[0]) / 2, 0, 1e-3, "centre x");
  near((m.bbox.min[2] + m.bbox.max[2]) / 2, 0, 1e-3, "centre z");
  assert.deepEqual(m.parts.map((p) => p.id), ["body"]);
  const out = await writeGlb(r.model!, "body");
  assert.deepEqual((await readGlb(out)).nodeNames, ["body"]);
  assert.deepEqual(await validateAsset(out, m as ModelMeta, 60000), []);
  const big = await normalizeGlb(bytes, parseTarget("longest:2"), { type: "upload", filename: "blob.glb" });
  near(Math.max(...big.meta!.size), 2, 0.04, "longest");
  assert.throws(() => parseTarget("x:1"));
  assert.throws(() => parseTarget("h:0"));
});

test("validate-asset catches a meta that does not match the GLB", async () => {
  const r = buildAsset(spec());
  const bytes = await writeGlb(r.model!, "four_stroke");
  const meta = JSON.parse(JSON.stringify(r.meta)) as ModelMeta;
  meta.parts.push({ id: "ghost", parent: "nobody", bbox: meta.bbox });
  meta.anchor_parts.x = { part: "nope", at: [0, 0, 0] };
  const c = (await validateAsset(bytes, meta)).map((i) => i.code);
  for (const code of ["missing_part", "unknown_parent", "bad_anchor"]) assert.ok(c.includes(code), `${code} in ${c}`);
  const lifted = JSON.parse(JSON.stringify(r.meta)) as ModelMeta;
  lifted.rig.rest.crankcase.at = [0, 0.2, 0];
  assert.ok((await validateAsset(bytes, lifted)).some((i) => i.code === "dims_mismatch"));
});

for (const [name, fn] of tests) {
  await fn();
  n++;
  console.log("ok", name);
}
console.log(`${n} asset tests passed`);
