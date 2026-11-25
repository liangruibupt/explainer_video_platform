// World-space geometry of a posed object for the compiler and the checks: part world matrices
// from the rig's part-local transforms (core/rig.ts partTransforms, never recomputed here),
// posed part bounding boxes, anchor points.
import * as THREE from "three";
import type { ModelMeta, V3 } from "../core/types";

export type Box = { min: V3; max: V3 };

const m4 = (t: number[]) =>
  new THREE.Matrix4().compose(
    new THREE.Vector3(t[0], t[1], t[2]),
    new THREE.Quaternion().setFromEuler(new THREE.Euler(t[3], t[4], t[5], "XYZ")),
    new THREE.Vector3(1, 1, 1),
  );

/** Object transform row [px,py,pz, rx,ry,rz, visible] (+ uniform scale) as a matrix. */
export function objectMatrix(x: number[], scale = 1): THREE.Matrix4 {
  return m4(x).multiply(new THREE.Matrix4().makeScale(scale, scale, scale));
}

/** Parents before children. */
export function partOrder(meta: ModelMeta): string[] {
  const parent = new Map(meta.parts.map((p) => [p.id, p.parent]));
  const ids = Object.keys(meta.rig.rest);
  const depth = (id: string, n = 0): number => {
    const p = parent.get(id);
    return p && n < 64 ? depth(p, n + 1) : n;
  };
  return ids.sort((a, b) => depth(a) - depth(b));
}

/** Asset-space matrices of every part for one set of part-local transforms. */
export function partMatrices(meta: ModelMeta, local: Record<string, number[]>): Record<string, THREE.Matrix4> {
  const parent = new Map(meta.parts.map((p) => [p.id, p.parent]));
  const out: Record<string, THREE.Matrix4> = {};
  for (const id of partOrder(meta)) {
    const l = m4(local[id] ?? [0, 0, 0, 0, 0, 0]);
    const p = parent.get(id);
    out[id] = p && out[p] ? out[p].clone().multiply(l) : l;
  }
  return out;
}

const restCache = new WeakMap<ModelMeta, Record<string, THREE.Matrix4>>();
export function restMatrices(meta: ModelMeta): Record<string, THREE.Matrix4> {
  let r = restCache.get(meta);
  if (!r) {
    const local: Record<string, number[]> = {};
    for (const [id, x] of Object.entries(meta.rig.rest)) local[id] = [...x.at, ...x.rot];
    r = partMatrices(meta, local);
    restCache.set(meta, r);
  }
  return r;
}

export function boxCenter(b: Box): V3 {
  return [(b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2, (b.min[2] + b.max[2]) / 2];
}

/** AABB of the 8 corners of `b` under matrix m. */
export function transformBox(b: Box, m: THREE.Matrix4): Box {
  const bb = new THREE.Box3(new THREE.Vector3(...b.min), new THREE.Vector3(...b.max)).applyMatrix4(m);
  return { min: bb.min.toArray() as V3, max: bb.max.toArray() as V3 };
}

/** Object-space explode direction per part: (part rest centre - asset centre), for factor 1. */
export function explodeDirs(meta: ModelMeta): Record<string, V3> {
  const c = boxCenter(meta.bbox);
  const out: Record<string, V3> = {};
  for (const p of meta.parts) {
    const pc = boxCenter(p.bbox);
    out[p.id] = [pc[0] - c[0], pc[1] - c[1], pc[2] - c[2]];
  }
  return out;
}

/**
 * World matrices of every part: object matrix * asset-space part matrix, with the explode
 * offset (object space, explodeDirs * factor) applied before the object transform.
 */
export function worldParts(
  meta: ModelMeta,
  local: Record<string, number[]>,
  obj: THREE.Matrix4,
  explode = 0,
): Record<string, THREE.Matrix4> {
  const am = partMatrices(meta, local);
  const dirs = explode ? explodeDirs(meta) : null;
  const out: Record<string, THREE.Matrix4> = {};
  for (const [id, m] of Object.entries(am)) {
    let a = m;
    if (dirs && dirs[id]) {
      const d = dirs[id];
      a = new THREE.Matrix4().makeTranslation(d[0] * explode, d[1] * explode, d[2] * explode).multiply(m);
    }
    out[id] = obj.clone().multiply(a);
  }
  return out;
}

/** World AABB of every part with a non-empty rest box: rest box mapped by posed * rest^-1. */
export function posedBoxes(meta: ModelMeta, world: Record<string, THREE.Matrix4>): Record<string, Box> {
  const rest = restMatrices(meta);
  const out: Record<string, Box> = {};
  for (const p of meta.parts) {
    const w = world[p.id];
    const r = rest[p.id];
    if (!w || !r) continue;
    const s = p.bbox;
    if (s.max[0] - s.min[0] <= 0 && s.max[1] - s.min[1] <= 0 && s.max[2] - s.min[2] <= 0) continue; // group
    out[p.id] = transformBox(s, w.clone().multiply(r.clone().invert()));
  }
  return out;
}

export const apply = (m: THREE.Matrix4, p: V3): V3 => new THREE.Vector3(...p).applyMatrix4(m).toArray() as V3;
