// validate-asset: re-check a stored GLB + meta.json without rebuilding (docs/PREVIS_CONTRACT.md 4.5).
// Geometry comes from the GLB (part-local, by node name); poses from meta.rig via core/rig.ts.
import * as THREE from "three";
import { evaluate } from "../core/expr";
import type { CheckIssue, ModelMeta } from "../core/types";
import { DEFAULT_MAX_TRIS } from "./build";
import { readGlb } from "./glb";
import { type Model, type ModelPart, checkModel, err } from "./model";

export async function validateAsset(bytes: Uint8Array, meta: ModelMeta, maxTris = DEFAULT_MAX_TRIS): Promise<CheckIssue[]> {
  const issues: CheckIssue[] = [];
  let glb: Awaited<ReturnType<typeof readGlb>>;
  try {
    glb = await readGlb(bytes);
  } catch (e) {
    return [err("nan", `GLB 无法读取：${(e as Error).message}`)];
  }
  const rig = meta?.rig;
  if (!rig || typeof rig !== "object" || !rig.rest) return [err("nan", "meta.json 缺少 rig")];
  // structure: duplicate node names, parents, cycles (meta.parts)
  const seen = new Set<string>();
  for (const n of glb.nodeNames) {
    if (seen.has(n)) issues.push(err("duplicate_id", `GLB 里有两个节点叫 ${n}`, `parts.${n}`));
    seen.add(n);
  }
  const ids = new Set((meta.parts ?? []).map((p) => p.id));
  const parentOf = new Map((meta.parts ?? []).map((p) => [p.id, p.parent]));
  for (const p of meta.parts ?? []) {
    if (p.parent && !ids.has(p.parent)) issues.push(err("unknown_parent", `部件 ${p.id} 的父部件 ${p.parent} 不存在`, `parts.${p.id}`));
    if (!seen.has(p.id)) issues.push(err("missing_part", `meta 里的部件 ${p.id} 在 GLB 里没有同名节点`, `parts.${p.id}`));
    const path = new Set<string>();
    for (let c: string | null | undefined = p.id; c; c = parentOf.get(c)) {
      if (path.has(c)) {
        issues.push(err("cycle", `部件 ${p.id} 的父子关系成环`, `parts.${p.id}`));
        break;
      }
      path.add(c);
    }
  }
  for (const id of Object.keys(rig.rest)) if (!ids.has(id)) issues.push(err("missing_part", `rig.rest 里的部件 ${id} 不在 meta.parts 里`, `rig.rest.${id}`));
  // drivers evaluate at the default pose
  const env: Record<string, number> = { ...rig.params };
  for (const [k, d] of Object.entries(rig.dofs ?? {})) env[k] = d.default;
  for (const [k, e] of Object.entries(rig.drivers ?? {})) {
    try {
      env[k] = evaluate(e, env);
    } catch (x) {
      issues.push(err("expr_error", `驱动量 ${k}：${(x as Error).message}`, `drivers.${k}`));
    }
  }
  for (const [name, a] of Object.entries(meta.anchor_parts ?? {}))
    if (!ids.has(a.part)) issues.push(err("bad_anchor", `锚点 ${name} 指向不存在的部件 ${a.part}`, `anchors.${name}`));
  for (const [name, a] of Object.entries(meta.anchors ?? {}))
    if (!Array.isArray(a) || a.length !== 3 || !a.every(Number.isFinite)) issues.push(err("bad_anchor", `锚点 ${name} 不是有限的 [x,y,z]`, `anchors.${name}`));
  if (issues.length) return issues;

  // part geometry from the GLB, in the node's own frame (meshes of one node merged)
  const parts: ModelPart[] = (meta.parts ?? []).map((p) => {
    const ms = glb.meshes.filter((m) => m.node === p.id);
    let geom: THREE.BufferGeometry | null = null;
    if (ms.length) {
      const n = ms.reduce((s, m) => s + m.positions.length, 0);
      const pos = new Float32Array(n);
      const idx: number[] = [];
      let o = 0;
      for (const m of ms) {
        pos.set(m.positions, o);
        for (const i of m.indices) idx.push(i + o / 3);
        o += m.positions.length;
      }
      geom = new THREE.BufferGeometry();
      geom.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      geom.setIndex(idx);
    }
    return { id: p.id, parent: p.parent, geom, slot: "clay", opacity: 1 };
  });
  const model: Model = { parts, rig: { dofs: rig.dofs ?? {}, drivers: rig.drivers ?? {}, joints: rig.joints ?? {}, params: rig.params ?? {}, rest: rig.rest } };
  let dims: { w: number; h: number; d: number; tol: number } | undefined;
  const src = meta.source;
  if (src?.type === "parametric") {
    const sd = src.spec.dims;
    dims = { w: evaluate(sd.w, rig.params), h: evaluate(sd.h, rig.params), d: evaluate(sd.d, rig.params), tol: sd.tol ?? 0.02 };
  } else if (Array.isArray(meta.size)) {
    dims = { w: meta.size[0], h: meta.size[1], d: meta.size[2], tol: 0.02 };
  }
  const chk = checkModel(model, { dims, maxTris });
  return chk.issues;
}
