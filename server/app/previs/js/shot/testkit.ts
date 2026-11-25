// TEST-ONLY asset builder: AssetSpec -> GLB bytes + ModelMeta, enough for the four_stroke
// fixture (box / cylinder / tube / cone / sphere / group). Part A owns the real builder
// (server/app/previs/js/asset/**); the integrator swaps this out once A's PR lands. Same output
// rules as contract section 4.4: node name = part id, parents nested, rest pose in the node
// transforms and in meta.rig.rest, material extras.evp_slot, one mesh per part.
import * as THREE from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { evaluate } from "../core/expr";
import { axisVec } from "../core/rig";
import type { AssetSpec, ModelMeta, Num, PartSpec, V3 } from "../core/types";

export const PALETTE: Record<string, string> = {
  clay: "#c9c4bb",
  accent1: "#d98c5f",
  accent2: "#6f9fd8",
  accent3: "#8cc084",
  accent4: "#d8c46f",
  glass: "#a9b8c6",
};

function orient(g: THREE.BufferGeometry, axis: string | undefined) {
  if (axis === "z") g.rotateX(Math.PI / 2);
  else if (axis === "x") g.rotateZ(-Math.PI / 2);
  return g;
}

function flip(g: THREE.BufferGeometry) {
  const idx = g.getIndex()!;
  for (let i = 0; i < idx.count; i += 3) {
    const a = idx.getX(i + 1);
    idx.setX(i + 1, idx.getX(i + 2));
    idx.setX(i + 2, a);
  }
  const n = g.getAttribute("normal");
  for (let i = 0; i < n.count; i++) n.setXYZ(i, -n.getX(i), -n.getY(i), -n.getZ(i));
  return g;
}

const strip = (g: THREE.BufferGeometry) => {
  for (const k of Object.keys(g.attributes)) if (k !== "position" && k !== "normal") g.deleteAttribute(k);
  return g;
};

function shapeGeometry(p: PartSpec, n: (x: Num | undefined, d?: number) => number): THREE.BufferGeometry | null {
  const seg = p.segments ?? 32;
  switch (p.shape) {
    case "group":
      return null;
    case "box": {
      const s = p.size!;
      return new THREE.BoxGeometry(n(s[0]), n(s[1]), n(s[2]));
    }
    case "cylinder":
      return orient(new THREE.CylinderGeometry(n(p.r_top, n(p.r)), n(p.r), n(p.h), seg), p.axis);
    case "cone":
      return orient(new THREE.ConeGeometry(n(p.r), n(p.h), seg), p.axis);
    case "sphere":
      return new THREE.SphereGeometry(n(p.r), seg, Math.max(8, seg / 2));
    case "tube": {
      const ro = n(p.r_outer);
      const ri = n(p.r_inner);
      const h = n(p.h);
      if (!(ri > 0 && ri < ro)) throw new Error(`bad tube ${p.id}`);
      const outer = new THREE.CylinderGeometry(ro, ro, h, seg, 1, true);
      const inner = flip(new THREE.CylinderGeometry(ri, ri, h, seg, 1, true));
      const top = new THREE.RingGeometry(ri, ro, seg).rotateX(-Math.PI / 2).translate(0, h / 2, 0);
      const bot = new THREE.RingGeometry(ri, ro, seg).rotateX(Math.PI / 2).translate(0, -h / 2, 0);
      return orient(mergeGeometries([outer, inner, top, bot].map(strip))!, p.axis);
    }
    default:
      throw new Error(`testkit: shape ${p.shape} not supported`);
  }
}

export interface Built {
  glb: Uint8Array;
  meta: ModelMeta;
}

export function buildTestAsset(spec: AssetSpec): Built {
  const params: Record<string, number> = {};
  for (const [k, d] of Object.entries(spec.params)) params[k] = d.value;
  const n = (x: Num | undefined, d = 0) => (x === undefined ? d : evaluate(x, params));
  const v3 = (v: [Num, Num, Num] | undefined): V3 => (v ? [n(v[0]), n(v[1]), n(v[2])] : [0, 0, 0]);

  const rest: ModelMeta["rig"]["rest"] = {};
  const joints: ModelMeta["rig"]["joints"] = {};
  const geos: Record<string, THREE.BufferGeometry | null> = {};
  const world: Record<string, THREE.Matrix4> = {};
  const byId = new Map(spec.parts.map((p) => [p.id, p]));
  for (const p of spec.parts) {
    const at = v3(p.at);
    const rot = v3(p.rot_deg).map((x) => (x * Math.PI) / 180) as V3;
    rest[p.id] = { at, rot };
    if (p.joint) joints[p.id] = { ...p.joint, axis: axisVec(p.joint.axis) };
    geos[p.id] = shapeGeometry(p, n);
  }
  const wm = (id: string): THREE.Matrix4 => {
    if (world[id]) return world[id];
    const r = rest[id];
    const l = new THREE.Matrix4().compose(
      new THREE.Vector3(...r.at),
      new THREE.Quaternion().setFromEuler(new THREE.Euler(r.rot[0], r.rot[1], r.rot[2], "XYZ")),
      new THREE.Vector3(1, 1, 1),
    );
    const par = byId.get(id)!.parent;
    world[id] = par ? wm(par).clone().multiply(l) : l;
    return world[id];
  };
  const parts: (ModelMeta["parts"][number] & { local: { min: V3; max: V3 } })[] = [];
  const localBox: Record<string, { min: V3; max: V3 }> = {};
  const all = new THREE.Box3();
  let tris = 0;
  for (const p of spec.parts) {
    const g = geos[p.id];
    const w = wm(p.id);
    let box: { min: V3; max: V3 };
    let local: { min: V3; max: V3 } = { min: [0, 0, 0], max: [0, 0, 0] };
    if (g) {
      g.computeBoundingBox();
      local = { min: g.boundingBox!.min.toArray() as V3, max: g.boundingBox!.max.toArray() as V3 };
      const b = g.boundingBox!.clone().applyMatrix4(w);
      all.union(b);
      box = { min: b.min.toArray() as V3, max: b.max.toArray() as V3 };
      tris += (g.getIndex()?.count ?? g.getAttribute("position").count) / 3;
    } else {
      const o = new THREE.Vector3().setFromMatrixPosition(w).toArray() as V3;
      box = { min: o, max: [...o] as V3 };
    }
    parts.push({ id: p.id, parent: p.parent ?? null, bbox: box, local });
    localBox[p.id] = local;
  }
  const anchors: Record<string, V3> = {};
  const anchor_parts: ModelMeta["anchor_parts"] = {};
  for (const [k, a] of Object.entries(spec.anchors ?? {})) {
    if (typeof a === "string") {
      // a part id: the centre of that part's box (contract 15.5)
      const lb = localBox[a];
      const c: V3 = lb ? [(lb.min[0] + lb.max[0]) / 2, (lb.min[1] + lb.max[1]) / 2, (lb.min[2] + lb.max[2]) / 2] : [0, 0, 0];
      anchor_parts[k] = { part: a, at: c };
      anchors[k] = new THREE.Vector3(...c).applyMatrix4(wm(a)).toArray() as V3;
    } else if (Array.isArray(a)) anchors[k] = v3(a);
    else {
      const at = v3(a.at);
      anchor_parts[k] = { part: a.part, at };
      anchors[k] = new THREE.Vector3(...at).applyMatrix4(wm(a.part)).toArray() as V3;
    }
  }
  const meta: ModelMeta = {
    kind: "model",
    format: "glb",
    units: "m",
    bbox: { min: all.min.toArray() as V3, max: all.max.toArray() as V3 },
    size: all.max.clone().sub(all.min).toArray() as V3,
    tris,
    parts,
    rig: { dofs: spec.dofs ?? {}, drivers: spec.drivers ?? {}, joints, params, rest },
    anchors,
    anchor_parts,
    source: { type: "parametric", spec },
  };
  return { glb: writeGlb(spec, rest, geos), meta };
}

/** Minimal glTF 2.0 binary writer: nodes (TRS), one mesh per geometry, PBR materials. */
function writeGlb(spec: AssetSpec, rest: ModelMeta["rig"]["rest"], geos: Record<string, THREE.BufferGeometry | null>): Uint8Array {
  const bufs: Uint8Array[] = [];
  let off = 0;
  const views: object[] = [];
  const accessors: object[] = [];
  const push = (arr: Float32Array | Uint32Array, target: number) => {
    const b = new Uint8Array(arr.buffer, arr.byteOffset, arr.byteLength);
    views.push({ buffer: 0, byteOffset: off, byteLength: b.byteLength, target });
    bufs.push(b);
    off += b.byteLength;
    const pad = (4 - (off % 4)) % 4;
    if (pad) {
      bufs.push(new Uint8Array(pad));
      off += pad;
    }
    return views.length - 1;
  };
  const materials: object[] = [];
  const meshes: object[] = [];
  const nodes: { name: string; translation: number[]; rotation: number[]; mesh?: number; children?: number[] }[] = [];
  const index = new Map<string, number>();
  spec.parts.forEach((p, i) => index.set(p.id, i));
  for (const p of spec.parts) {
    const r = rest[p.id];
    const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(r.rot[0], r.rot[1], r.rot[2], "XYZ"));
    const node: (typeof nodes)[number] = { name: p.id, translation: [...r.at], rotation: [q.x, q.y, q.z, q.w] };
    const g = geos[p.id];
    if (g) {
      const pos = new Float32Array(g.getAttribute("position").array);
      const nor = new Float32Array(g.getAttribute("normal").array);
      const idx = new Uint32Array(g.getIndex()!.array);
      g.computeBoundingBox();
      const bb = g.boundingBox!;
      accessors.push({ bufferView: push(pos, 34962), componentType: 5126, count: pos.length / 3, type: "VEC3", min: bb.min.toArray(), max: bb.max.toArray() });
      accessors.push({ bufferView: push(nor, 34962), componentType: 5126, count: nor.length / 3, type: "VEC3" });
      accessors.push({ bufferView: push(idx, 34963), componentType: 5125, count: idx.length, type: "SCALAR" });
      const slot = p.color && PALETTE[p.color] ? p.color : p.color ? "custom" : "clay";
      const c = new THREE.Color(PALETTE[slot] ?? p.color);
      const op = p.opacity ?? (slot === "glass" ? 0.35 : 1);
      materials.push({
        name: `${p.id}_mat`,
        pbrMetallicRoughness: { baseColorFactor: [c.r, c.g, c.b, op], metallicFactor: 0, roughnessFactor: 0.85 },
        ...(op < 1 ? { alphaMode: "BLEND" } : {}),
        extras: { evp_slot: slot },
      });
      meshes.push({ name: p.id, primitives: [{ attributes: { POSITION: accessors.length - 3, NORMAL: accessors.length - 2 }, indices: accessors.length - 1, material: materials.length - 1 }] });
      node.mesh = meshes.length - 1;
    }
    nodes.push(node);
  }
  const roots: number[] = [];
  for (const p of spec.parts) {
    const i = index.get(p.id)!;
    if (p.parent) (nodes[index.get(p.parent)!].children ??= []).push(i);
    else roots.push(i);
  }
  const json = {
    asset: { version: "2.0", generator: "evp previs testkit" },
    scene: 0,
    scenes: [{ name: spec.id, nodes: roots }],
    nodes,
    meshes,
    materials,
    accessors,
    bufferViews: views,
    buffers: [{ byteLength: off }],
  };
  let js = new TextEncoder().encode(JSON.stringify(json));
  const jpad = (4 - (js.length % 4)) % 4;
  if (jpad) {
    const t = new Uint8Array(js.length + jpad).fill(0x20);
    t.set(js);
    js = t;
  }
  const total = 12 + 8 + js.length + 8 + off;
  const out = new Uint8Array(total);
  const dv = new DataView(out.buffer);
  dv.setUint32(0, 0x46546c67, true); // "glTF"
  dv.setUint32(4, 2, true);
  dv.setUint32(8, total, true);
  dv.setUint32(12, js.length, true);
  dv.setUint32(16, 0x4e4f534a, true); // JSON
  out.set(js, 20);
  let o = 20 + js.length;
  dv.setUint32(o, off, true);
  dv.setUint32(o + 4, 0x004e4942, true); // BIN
  o += 8;
  for (const b of bufs) {
    out.set(b, o);
    o += b.byteLength;
  }
  return out;
}
