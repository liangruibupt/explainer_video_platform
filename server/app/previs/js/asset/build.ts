// build-asset: AssetSpec -> Model + ModelMeta + issues (docs/PREVIS_CONTRACT.md 4.4 / 4.5).
// Expressions only through core/expr.ts, poses only through core/rig.ts.
import * as THREE from "three";
import { ExprError, evaluate } from "../core/expr";
import { axisVec } from "../core/rig";
import type { AssetSpec, CheckIssue, JointDef, ModelMeta, PartSpec, V3 } from "../core/types";
import { ShapeError, mergeAndWeld, shapeGeometry } from "./geometry";
import { type Model, type ModelPart, type Rig, boxOf, checkModel, err, localBoxes } from "./model";

export const ID_RE = /^[a-z][a-z0-9_]*$/;
const SLOTS = new Set(["clay", "accent1", "accent2", "accent3", "accent4", "glass"]);
export const DEFAULT_MAX_TRIS = 200000;

export interface BuildResult {
  issues: CheckIssue[];
  model?: Model;
  meta?: ModelMeta;
}

const D2R = Math.PI / 180;
const msg = (e: unknown) => (e as Error).message;

/** Apply a part's `array` to its geometry (copies in the parent frame, merged into the part). */
function applyArray(part: PartSpec, g: THREE.BufferGeometry, localMtx: THREE.Matrix4, env: Record<string, number>): THREE.BufferGeometry[] {
  const a = part.array;
  if (!a) return [g];
  if (!Number.isInteger(a.count) || a.count < 1 || a.count > 1000) throw new ShapeError(`array.count 必须是 1..1000 的整数`);
  const inv = localMtx.clone().invert();
  const copies: THREE.BufferGeometry[] = [];
  for (let i = 0; i < a.count; i++) {
    let step: THREE.Matrix4;
    if (a.kind === "linear") {
      if (!Array.isArray(a.step) || a.step.length !== 3) throw new ShapeError("linear array 需要 step [x,y,z]");
      const s = a.step.map((x) => evaluate(x, env));
      step = new THREE.Matrix4().makeTranslation(s[0] * i, s[1] * i, s[2] * i);
    } else if (a.kind === "radial") {
      const total = a.angle_deg === undefined ? 360 : evaluate(a.angle_deg, env);
      const per = Math.abs(total) >= 360 ? total / a.count : a.count > 1 ? total / (a.count - 1) : 0;
      const ax = new THREE.Vector3(...axisVec(a.axis ?? "y"));
      step = new THREE.Matrix4().makeRotationAxis(ax, per * i * D2R);
    } else {
      throw new ShapeError(`未知的 array.kind ${String((a as { kind: unknown }).kind)}`);
    }
    // copy i in the part frame = local^-1 * step_i * local (step is about the parent origin)
    copies.push(g.clone().applyMatrix4(inv.clone().multiply(step).multiply(localMtx)));
  }
  return copies;
}

function vec(v: unknown, env: Record<string, number>, what: string): V3 {
  if (!Array.isArray(v) || v.length !== 3) throw new ShapeError(`${what} 必须是 [x,y,z]`);
  return v.map((x) => evaluate(x as string | number, env)) as V3;
}

/** Structural checks: ids, parents, cycles. Returns parts in parent-before-child order. */
function structure(spec: AssetSpec, issues: CheckIssue[]): PartSpec[] {
  const byId = new Map<string, PartSpec>();
  for (const [i, p] of (spec.parts ?? []).entries()) {
    if (!p || typeof p.id !== "string" || !ID_RE.test(p.id)) {
      issues.push(err("bad_id", `第 ${i + 1} 个部件的 id ${JSON.stringify(p?.id)} 不合法（要求 [a-z][a-z0-9_]*）`, `parts[${i}]`));
      continue;
    }
    if (byId.has(p.id)) {
      issues.push(err("duplicate_id", `部件 id ${p.id} 重复`, `parts.${p.id}`));
      continue;
    }
    byId.set(p.id, p);
  }
  for (const p of byId.values())
    if (p.parent !== undefined && p.parent !== null && !byId.has(p.parent))
      issues.push(err("unknown_parent", `部件 ${p.id} 的父部件 ${p.parent} 不存在`, `parts.${p.id}`));
  // cycles: walk up from every part
  const inCycle = new Set<string>();
  for (const p of byId.values()) {
    const seen = new Set<string>();
    let cur: PartSpec | undefined = p;
    while (cur && cur.parent && !seen.has(cur.id)) {
      seen.add(cur.id);
      cur = byId.get(cur.parent);
    }
    if (cur && seen.has(cur.id) && !inCycle.has(cur.id)) {
      const ring: string[] = [];
      let c: PartSpec = cur;
      do {
        ring.push(c.id);
        inCycle.add(c.id);
        c = byId.get(c.parent!)!;
      } while (c.id !== cur.id);
      issues.push(err("cycle", `部件的父子关系成环：${ring.join(" → ")} → ${cur.id}`, `parts.${cur.id}`));
    }
  }
  // order parent before child
  const out: PartSpec[] = [];
  const done = new Set<string>();
  const visit = (p: PartSpec, depth: number) => {
    if (done.has(p.id) || depth > byId.size) return;
    const par = p.parent ? byId.get(p.parent) : undefined;
    if (par) visit(par, depth + 1);
    done.add(p.id);
    out.push(p);
  };
  for (const p of byId.values()) visit(p, 0);
  return out;
}

export function buildAsset(spec: AssetSpec): BuildResult {
  const issues: CheckIssue[] = [];
  if (!spec || typeof spec !== "object") return { issues: [err("bad_shape_param", "规格不是 JSON 对象")] };
  if (typeof spec.id !== "string" || !ID_RE.test(spec.id)) issues.push(err("bad_id", `资产 id ${JSON.stringify(spec.id)} 不合法（要求 [a-z][a-z0-9_]*）`, "id"));
  if (!Array.isArray(spec.parts) || !spec.parts.length) return { issues: [...issues, err("bad_shape_param", "parts 为空")] };

  // params
  const params: Record<string, number> = {};
  for (const [k, p] of Object.entries(spec.params ?? {})) {
    if (!p || typeof p.value !== "number" || !Number.isFinite(p.value)) issues.push(err("expr_error", `参数 ${k} 的 value 不是有限数`, `params.${k}`));
    else params[k] = p.value;
  }
  // dofs / drivers (the rig evaluates drivers; here only shape and range sanity)
  const dofs: Rig["dofs"] = {};
  for (const [k, d] of Object.entries(spec.dofs ?? {})) {
    if (!d || ![d.min, d.max, d.default].every((x) => typeof x === "number" && Number.isFinite(x)) || d.min > d.max || d.default < d.min || d.default > d.max)
      issues.push(err("dof_range", `自由度 ${k} 的 min/max/default 不合法（要求 min ≤ default ≤ max）`, `dofs.${k}`));
    else dofs[k] = { min: d.min, max: d.max, default: d.default, ...(d.label ? { label: d.label } : {}) };
  }
  const drivers: Record<string, string> = {};
  for (const [k, e] of Object.entries(spec.drivers ?? {})) {
    if (typeof e !== "string") issues.push(err("expr_error", `驱动量 ${k} 不是表达式字符串`, `drivers.${k}`));
    else drivers[k] = e;
  }
  // default-pose driver values: catches unknown names / bad syntax as expr_error
  const env: Record<string, number> = { ...params };
  const dofEnv: Record<string, number> = { ...params };
  for (const [k, d] of Object.entries(dofs)) dofEnv[k] = d.default;
  for (const [k, e] of Object.entries(drivers)) {
    try {
      dofEnv[k] = evaluate(e, dofEnv);
    } catch (x) {
      issues.push(err("expr_error", `驱动量 ${k}：${msg(x)}`, `drivers.${k}`));
      dofEnv[k] = 0;
    }
  }

  const ordered = structure(spec, issues);
  const structural = issues.some((i) => ["unknown_parent", "cycle", "duplicate_id"].includes(i.code));

  // parts: rest pose, joints, geometry
  const rest: Rig["rest"] = {};
  const joints: Rig["joints"] = {};
  const mparts: ModelPart[] = [];
  for (const p of ordered) {
    const where = `parts.${p.id}`;
    let at: V3 = [0, 0, 0];
    let rot: V3 = [0, 0, 0];
    try {
      if (p.at !== undefined) at = vec(p.at, env, "at");
      if (p.rot_deg !== undefined) rot = vec(p.rot_deg, env, "rot_deg").map((x) => x * D2R) as V3;
    } catch (x) {
      issues.push(err(x instanceof ExprError ? "expr_error" : "bad_shape_param", `部件 ${p.id}：${msg(x)}`, where));
    }
    rest[p.id] = { at, rot };
    if (p.joint) {
      const j = p.joint as JointDef;
      const jw = `${where}.joint`;
      if (j.type !== "hinge" && j.type !== "slide") issues.push(err("bad_shape_param", `部件 ${p.id} 的关节类型只能是 hinge / slide`, jw));
      let axis: V3 | null = null;
      try {
        const a = axisVec(j.axis);
        if (!a || !Array.isArray(a) || a.length !== 3 || !a.every((x) => typeof x === "number" && Number.isFinite(x)) || Math.hypot(...a) < 1e-9)
          throw new ShapeError(`关节轴 ${JSON.stringify(j.axis)} 不合法`);
        const n = Math.hypot(...a);
        axis = a.map((x) => x / n) as V3;
      } catch (x) {
        issues.push(err("bad_shape_param", `部件 ${p.id}：${msg(x)}`, jw));
      }
      if (!(j.dof in dofs) && !(j.dof in drivers))
        issues.push(err("expr_error", `部件 ${p.id} 的关节用了不存在的自由度或驱动量 ${j.dof}`, jw));
      let scale: number | string = 1;
      if (j.scale !== undefined) {
        try {
          scale = evaluate(j.scale, env); // params only -> a number
        } catch {
          try {
            evaluate(j.scale, dofEnv); // depends on DOFs: kept as an expression, the rig evaluates it
            scale = j.scale as string;
          } catch (x) {
            issues.push(err("expr_error", `部件 ${p.id} 的关节 scale：${msg(x)}`, jw));
          }
        }
      }
      if (axis) joints[p.id] = { type: j.type, axis, dof: j.dof, ...(scale !== 1 ? { scale } : {}) };
    }
    let slot = "clay";
    if (p.color !== undefined) {
      if (typeof p.color === "string" && (SLOTS.has(p.color) || /^#[0-9a-fA-F]{6}$/.test(p.color))) slot = p.color.toLowerCase();
      else issues.push(err("bad_shape_param", `部件 ${p.id} 的颜色 ${JSON.stringify(p.color)} 不是调色板槽位或 #rrggbb`, where));
    }
    const opacity = p.opacity ?? (slot === "glass" ? 0.35 : 1);
    if (typeof opacity !== "number" || !(opacity >= 0 && opacity <= 1)) issues.push(err("bad_shape_param", `部件 ${p.id} 的 opacity 必须在 0..1`, where));
    let geom: THREE.BufferGeometry | null = null;
    try {
      const g = shapeGeometry(p, env);
      if (g) {
        const local = new THREE.Matrix4().compose(new THREE.Vector3(...at), new THREE.Quaternion().setFromEuler(new THREE.Euler(...rot, "XYZ")), new THREE.Vector3(1, 1, 1));
        geom = mergeAndWeld(applyArray(p, g, local, env));
      } else if (p.array) {
        issues.push(err("bad_shape_param", `group 部件 ${p.id} 不能有 array`, where));
      }
    } catch (x) {
      issues.push(err(x instanceof ExprError ? "expr_error" : "bad_shape_param", `部件 ${p.id}：${msg(x)}`, where));
    }
    mparts.push({ id: p.id, parent: p.parent ?? null, geom, slot, opacity: typeof opacity === "number" ? opacity : 1 });
  }

  // dims
  let dims: { w: number; h: number; d: number; tol: number } | undefined;
  try {
    if (!spec.dims) throw new ShapeError("缺少 dims");
    const tol = spec.dims.tol ?? 0.02;
    if (typeof tol !== "number" || !(tol > 0 && tol < 1)) throw new ShapeError("dims.tol 必须在 (0, 1) 之间");
    dims = { w: evaluate(spec.dims.w, env), h: evaluate(spec.dims.h, env), d: evaluate(spec.dims.d, env), tol };
    if (!(dims.w > 0 && dims.h > 0 && dims.d > 0)) throw new ShapeError("dims 的 w/h/d 必须大于 0");
  } catch (x) {
    issues.push(err(x instanceof ExprError ? "expr_error" : "bad_shape_param", `dims：${msg(x)}`, "dims"));
    dims = undefined;
  }
  const maxTris = spec.max_tris ?? DEFAULT_MAX_TRIS;

  const rig: Rig = { dofs, drivers, joints, params, rest };
  const model: Model = { parts: mparts, rig };
  if (structural || issues.some((i) => i.code === "expr_error" || i.code === "bad_shape_param" || i.code === "bad_id")) {
    // geometry checks on a half-built model would only add noise
    return { issues };
  }
  const chk = checkModel(model, { dims, maxTris });
  issues.push(...chk.issues);
  if (!chk.rest) return { issues };

  // anchors
  const anchors: Record<string, V3> = {};
  const anchorParts: ModelMeta["anchor_parts"] = {};
  const moving = (id: string): boolean => {
    for (let c: string | null = id; c; c = mparts.find((p) => p.id === c)!.parent) if (joints[c]) return true;
    return false;
  };
  const lb = localBoxes(model);
  for (const [name, a] of Object.entries(spec.anchors ?? {})) {
    const where = `anchors.${name}`;
    try {
      if (typeof a === "string") {
        if (!rest[a]) throw new ShapeError(`锚点 ${name} 指向不存在的部件 ${a}`);
        const c = lb.get(a)?.getCenter(new THREE.Vector3()) ?? new THREE.Vector3();
        anchors[name] = c.clone().applyMatrix4(chk.rest.world.get(a)!).toArray() as V3;
        if (moving(a)) anchorParts[name] = { part: a, at: c.toArray() as V3 };
      } else if (Array.isArray(a)) {
        anchors[name] = vec(a, env, "锚点坐标");
      } else if (a && typeof a === "object" && typeof a.part === "string") {
        if (!rest[a.part]) throw new ShapeError(`锚点 ${name} 指向不存在的部件 ${a.part}`);
        const at = vec(a.at ?? [0, 0, 0], env, "锚点 at");
        anchors[name] = new THREE.Vector3(...at).applyMatrix4(chk.rest.world.get(a.part)!).toArray() as V3;
        anchorParts[name] = { part: a.part, at };
      } else {
        throw new ShapeError(`锚点 ${name} 的格式不对（部件 id、[x,y,z] 或 {part, at}）`);
      }
    } catch (x) {
      issues.push(err("bad_anchor", `锚点 ${name}：${msg(x)}`, where));
    }
  }
  if (issues.length) return { issues, model };

  const all = chk.rest.all;
  const size = all.getSize(new THREE.Vector3()).toArray() as V3;
  const meta: ModelMeta & { parts: (ModelMeta["parts"][number] & { local: { min: V3; max: V3 } | null })[] } = {
    kind: "model",
    format: "glb",
    units: "m",
    bbox: boxOf(all),
    size,
    tris: chk.tris,
    parts: chk.rest.parts.map((p) => ({ ...p, local: lb.has(p.id) ? boxOf(lb.get(p.id)!) : null })),
    rig,
    anchors,
    anchor_parts: anchorParts,
    source: { type: "parametric", spec },
  };
  return { issues, model, meta };
}
