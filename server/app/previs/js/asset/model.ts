// In-memory model shared by build-asset, normalize-glb and validate-asset: parts with their
// own geometry (part frame), a rig, and the checks that only need geometry + rig
// (docs/PREVIS_CONTRACT.md section 4.5). Every pose goes through core/rig.ts.
import * as THREE from "three";
import { partTransforms } from "../core/rig";
import type { CheckIssue, ModelMeta, V3 } from "../core/types";

export type Rig = ModelMeta["rig"];
export type Box = { min: V3; max: V3 };

export interface ModelPart {
  id: string;
  parent: string | null;
  geom: THREE.BufferGeometry | null; // indexed, part frame; null = group
  slot: string; // palette slot or "#rrggbb"
  opacity: number;
}

export interface Model {
  parts: ModelPart[];
  rig: Rig;
}

export const err = (code: string, message: string, where?: string): CheckIssue =>
  where ? { code, severity: "error", message, where } : { code, severity: "error", message };

export const tris = (m: Model): number =>
  m.parts.reduce((n, p) => n + (p.geom ? (p.geom.index ? p.geom.index.count : p.geom.attributes.position.count) / 3 : 0), 0);

/** Asset-space matrix of every part for one pose (rig.partTransforms, composed down the tree). */
export function worldMatrices(m: Model, pose: Record<string, number> = {}): Map<string, THREE.Matrix4> {
  const local = partTransforms(m.rig, pose);
  const parent = new Map(m.parts.map((p) => [p.id, p.parent]));
  const out = new Map<string, THREE.Matrix4>();
  const q = new THREE.Quaternion();
  const e = new THREE.Euler();
  const one = new THREE.Vector3(1, 1, 1);
  const get = (id: string): THREE.Matrix4 => {
    const done = out.get(id);
    if (done) return done;
    const t = local[id] ?? [0, 0, 0, 0, 0, 0];
    const mtx = new THREE.Matrix4().compose(
      new THREE.Vector3(t[0], t[1], t[2]),
      q.setFromEuler(e.set(t[3], t[4], t[5], "XYZ")),
      one,
    );
    const par = parent.get(id);
    if (par) mtx.premultiply(get(par));
    out.set(id, mtx);
    return mtx;
  };
  for (const p of m.parts) get(p.id);
  return out;
}

const toBox = (b: THREE.Box3): Box => ({ min: b.min.toArray() as V3, max: b.max.toArray() as V3 });

function vertexBox(g: THREE.BufferGeometry, mtx?: THREE.Matrix4): THREE.Box3 {
  const b = new THREE.Box3();
  const p = g.attributes.position;
  const v = new THREE.Vector3();
  for (let i = 0; i < p.count; i++) {
    v.fromBufferAttribute(p, i);
    if (mtx) v.applyMatrix4(mtx);
    b.expandByPoint(v);
  }
  return b;
}

/** Local (part frame) bbox of each part's own geometry. */
export function localBoxes(m: Model): Map<string, THREE.Box3> {
  const out = new Map<string, THREE.Box3>();
  for (const p of m.parts) if (p.geom) out.set(p.id, vertexBox(p.geom));
  return out;
}

/**
 * Exact asset-space boxes for one pose: per part (its own geometry; a group gets the union
 * of its subtree) and the whole model.
 */
export function poseBoxes(m: Model, pose: Record<string, number> = {}) {
  const W = worldMatrices(m, pose);
  const own = new Map<string, THREE.Box3>();
  for (const p of m.parts) own.set(p.id, p.geom ? vertexBox(p.geom, W.get(p.id)) : new THREE.Box3());
  const kids = new Map<string, string[]>();
  for (const p of m.parts) if (p.parent) kids.set(p.parent, [...(kids.get(p.parent) ?? []), p.id]);
  const sub = (id: string): THREE.Box3 => {
    const b = own.get(id)!.clone();
    for (const k of kids.get(id) ?? []) b.union(sub(k));
    return b;
  };
  const all = new THREE.Box3();
  for (const b of own.values()) all.union(b);
  const parts = m.parts.map((p) => ({ id: p.id, parent: p.parent, bbox: toBox(p.geom ? own.get(p.id)! : sub(p.id)) }));
  return { all, parts, world: W };
}

export function boxOf(b: THREE.Box3): Box {
  return toBox(b);
}

function finiteGeom(m: Model): CheckIssue[] {
  const out: CheckIssue[] = [];
  for (const p of m.parts) {
    if (!p.geom) continue;
    const a = p.geom.attributes.position.array as Float32Array;
    for (let i = 0; i < a.length; i++)
      if (!Number.isFinite(a[i])) {
        out.push(err("nan", `部件 ${p.id} 的顶点坐标不是有限数`, `parts.${p.id}`));
        break;
      }
  }
  for (const [id, r] of Object.entries(m.rig.rest))
    if (![...r.at, ...r.rot].every(Number.isFinite)) out.push(err("nan", `部件 ${id} 的静止姿态不是有限数`, `parts.${id}`));
  return out;
}

export interface CheckOpts {
  dims?: { w: number; h: number; d: number; tol: number }; // declared size (parametric)
  maxTris: number;
}

/**
 * Geometry + rig checks: nan, too_many_tris, dims_mismatch, below_ground, floating, dof_range.
 * Returns the issues and the rest-pose boxes (null when the geometry is not finite).
 */
export function checkModel(m: Model, o: CheckOpts) {
  const issues = finiteGeom(m);
  const n = tris(m);
  if (n > o.maxTris) issues.push(err("too_many_tris", `三角面 ${n} 超过上限 ${o.maxTris}`));
  if (issues.some((i) => i.code === "nan")) return { issues, rest: null, tris: n };
  let rest: ReturnType<typeof poseBoxes>;
  try {
    rest = poseBoxes(m);
  } catch (e) {
    issues.push(err("dof_range", `默认姿态下驱动量无法计算：${(e as Error).message}`, "drivers"));
    return { issues, rest: null, tris: n };
  }
  const { all } = rest;
  if (all.isEmpty()) {
    issues.push(err("nan", "模型没有任何几何体"));
    return { issues, rest: null, tris: n };
  }
  const size = all.getSize(new THREE.Vector3());
  if (o.dims) {
    const want: [string, number, number][] = [
      ["w", o.dims.w, size.x],
      ["h", o.dims.h, size.y],
      ["d", o.dims.d, size.z],
    ];
    for (const [k, d, got] of want) {
      if (Math.abs(got - d) > o.dims.tol * Math.abs(d))
        issues.push(err("dims_mismatch", `尺寸 ${k} 声明 ${d} 米，实际 ${got.toFixed(4)} 米，误差超过 ${(o.dims.tol * 100).toFixed(1)}%`, `dims.${k}`));
    }
  }
  if (all.min.y < -0.001) issues.push(err("below_ground", `静止姿态最低点 y = ${all.min.y.toFixed(4)} 米，低于地面`));
  if (all.min.y > 0.001) issues.push(err("floating", `静止姿态最低点 y = ${all.min.y.toFixed(4)} 米，没有落在地面上`));
  issues.push(...dofRange(m, all, o.dims ? new THREE.Vector3(o.dims.w, o.dims.h, o.dims.d) : size));
  return { issues, rest, tris: n };
}

/**
 * 9 sampled poses across every DOF's range (all DOFs at the same fraction): every driver must
 * evaluate, and every part's box must stay inside 3x the declared size around the rest centre.
 */
export function dofRange(m: Model, restAll: THREE.Box3, dims: THREE.Vector3): CheckIssue[] {
  const dofs = Object.entries(m.rig.dofs);
  if (!dofs.length) return [];
  const local = localBoxes(m);
  const c = restAll.getCenter(new THREE.Vector3());
  const lim = new THREE.Box3().setFromCenterAndSize(c, dims.clone().multiplyScalar(3));
  const out: CheckIssue[] = [];
  const flagged = new Set<string>();
  for (let k = 0; k <= 8; k++) {
    const pose: Record<string, number> = {};
    for (const [name, d] of dofs) pose[name] = d.min + ((d.max - d.min) * k) / 8;
    const desc = dofs.map(([name]) => `${name}=${+pose[name].toFixed(4)}`).join(", ");
    let W: Map<string, THREE.Matrix4>;
    try {
      W = worldMatrices(m, pose);
    } catch (e) {
      out.push(err("dof_range", `姿态 ${desc} 下驱动量无法计算：${(e as Error).message}`, "drivers"));
      continue;
    }
    for (const p of m.parts) {
      const lb = local.get(p.id);
      const mtx = W.get(p.id)!;
      const b = lb ? lb.clone().applyMatrix4(mtx) : new THREE.Box3().setFromPoints([new THREE.Vector3().applyMatrix4(mtx)]);
      const bad = ![b.min.x, b.min.y, b.min.z, b.max.x, b.max.y, b.max.z].every(Number.isFinite) || !lim.containsBox(b);
      if (bad && !flagged.has(p.id)) {
        flagged.add(p.id);
        out.push(err("dof_range", `姿态 ${desc} 下部件 ${p.id} 飞出了模型尺寸 3 倍的范围`, `parts.${p.id}`));
      }
    }
  }
  return out;
}
