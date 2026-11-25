// normalize-glb: any GLB (image-to-3D, upload) -> one part "body" in metres, uniformly scaled
// to the target size, resting on y = 0, centred in XZ, simplified to max_tris
// (docs/PREVIS_CONTRACT.md 4.5). Textures and materials are dropped (v1 is clay only).
import { MeshoptSimplifier } from "meshoptimizer";
import * as THREE from "three";
import { mergeVertices } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import type { CheckIssue, ModelMeta, TargetSize, V3 } from "../core/types";
import { readGlb } from "./glb";
import { type Model, boxOf, checkModel, err } from "./model";

export const DEFAULT_NORMALIZE_TRIS = 60000;
const AXIS_INDEX: Record<string, number> = { w: 0, h: 1, d: 2 };

export function parseTarget(s: string): TargetSize {
  const m = /^(w|h|d|longest):([0-9]*\.?[0-9]+(?:e[-+]?\d+)?)$/i.exec(s.trim());
  if (!m) throw new Error(`--target 格式是 <w|h|d|longest>:<米>，收到 ${s}`);
  const metres = parseFloat(m[2]);
  if (!(metres > 0 && metres <= 1000)) throw new Error(`目标尺寸必须在 (0, 1000] 米之间，收到 ${metres}`);
  return { axis: m[1].toLowerCase() as TargetSize["axis"], metres };
}

/** Simplify an indexed triangle mesh to at most maxTris triangles with meshoptimizer. */
export async function simplify(index: Uint32Array, pos: Float32Array, maxTris: number): Promise<Uint32Array> {
  if (index.length / 3 <= maxTris) return index;
  await MeshoptSimplifier.ready;
  const target = Math.floor(maxTris) * 3;
  let [out] = MeshoptSimplifier.simplify(index, pos, 3, target, 1, []);
  if (out.length > target) [out] = MeshoptSimplifier.simplifySloppy(index, pos, 3, null, target, 1);
  return out;
}

export interface NormalizeResult {
  issues: CheckIssue[];
  model?: Model;
  meta?: ModelMeta;
  inputTris: number;
}

export async function normalizeGlb(bytes: Uint8Array, target: TargetSize, source: ModelMeta["source"], maxTris = DEFAULT_NORMALIZE_TRIS): Promise<NormalizeResult> {
  const { meshes } = await readGlb(bytes);
  // flatten every primitive into asset space
  let nv = 0;
  let ni = 0;
  for (const m of meshes) {
    nv += m.positions.length / 3;
    ni += m.indices.length;
  }
  if (!ni) return { issues: [err("nan", "GLB 里没有三角网格")], inputTris: 0 };
  const pos = new Float32Array(nv * 3);
  const idx = new Uint32Array(ni);
  let vo = 0;
  let io = 0;
  const v = new THREE.Vector3();
  for (const m of meshes) {
    const n = m.positions.length / 3;
    for (let i = 0; i < n; i++) {
      v.fromArray(m.positions, i * 3).applyMatrix4(m.world);
      v.toArray(pos, (vo + i) * 3);
    }
    for (let i = 0; i < m.indices.length; i++) idx[io + i] = m.indices[i] + vo;
    vo += n;
    io += m.indices.length;
  }
  const inputTris = ni / 3;
  for (let i = 0; i < pos.length; i++) if (!Number.isFinite(pos[i])) return { issues: [err("nan", "GLB 的顶点坐标不是有限数")], inputTris };
  // scale, ground, centre
  const box = new THREE.Box3().setFromArray(pos);
  const size = box.getSize(new THREE.Vector3()).toArray();
  const ref = target.axis === "longest" ? Math.max(...size) : size[AXIS_INDEX[target.axis]];
  if (!(ref > 0)) return { issues: [err("bad_shape_param", `模型在 ${target.axis} 方向的尺寸为 0，无法缩放`)], inputTris };
  const s = target.metres / ref;
  const c = box.getCenter(new THREE.Vector3());
  for (let i = 0; i < pos.length; i += 3) {
    pos[i] = (pos[i] - c.x) * s;
    pos[i + 1] = (pos[i + 1] - box.min.y) * s;
    pos[i + 2] = (pos[i + 2] - c.z) * s;
  }
  // weld by position (normals are recomputed), simplify, drop unused vertices
  let g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  g = mergeVertices(g, 1e-6 * target.metres);
  const wp = g.attributes.position.array as Float32Array;
  const simp = await simplify(Uint32Array.from(g.index!.array), wp, maxTris);
  const remap = new Int32Array(wp.length / 3).fill(-1);
  const outPos: number[] = [];
  const outIdx = new Uint32Array(simp.length);
  for (let i = 0; i < simp.length; i++) {
    const o = simp[i];
    if (remap[o] < 0) {
      remap[o] = outPos.length / 3;
      outPos.push(wp[o * 3], wp[o * 3 + 1], wp[o * 3 + 2]);
    }
    outIdx[i] = remap[o];
  }
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(outPos), 3));
  // simplification may drop extreme vertices: ground and centre again (size change is ~0.1 %)
  geom.computeBoundingBox();
  const sb = geom.boundingBox!;
  geom.translate(-(sb.min.x + sb.max.x) / 2, -sb.min.y, -(sb.min.z + sb.max.z) / 2);
  geom.setIndex(new THREE.BufferAttribute(outIdx, 1));
  geom.computeVertexNormals();

  const rig: ModelMeta["rig"] = { dofs: {}, drivers: {}, joints: {}, params: {}, rest: { body: { at: [0, 0, 0], rot: [0, 0, 0] } } };
  const model: Model = { parts: [{ id: "body", parent: null, geom, slot: "clay", opacity: 1 }], rig };
  const chk = checkModel(model, { maxTris });
  if (!chk.rest || chk.issues.length) return { issues: chk.issues, inputTris };
  const all = chk.rest.all;
  const ctr = all.getCenter(new THREE.Vector3()).toArray() as V3;
  const meta: ModelMeta = {
    kind: "model",
    format: "glb",
    units: "m",
    bbox: boxOf(all),
    size: all.getSize(new THREE.Vector3()).toArray() as V3,
    tris: chk.tris,
    parts: chk.rest.parts.map((p) => ({ ...p, local: p.bbox })),
    rig,
    anchors: { center: ctr, top: [ctr[0], all.max.y, ctr[2]] },
    anchor_parts: {},
    source,
  };
  return { issues: [], model, meta, inputTris };
}
