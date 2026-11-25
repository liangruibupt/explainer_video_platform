// Rig evaluation: DOF values -> part-local transforms (docs/PREVIS_CONTRACT.md section 4.3).
// FIXED by P0. Used by the asset builder (bbox per pose, range checks), the shot compiler
// (per-frame part tracks) and the renderer, so all three agree to the bit.
import * as THREE from "three";
import { evaluate } from "./expr";
import type { ModelMeta, V3 } from "./types";

type Rig = ModelMeta["rig"];

const AXES: Record<string, V3> = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
export const axisVec = (a: "x" | "y" | "z" | V3): V3 => (Array.isArray(a) ? a : AXES[a]);

/**
 * All DOF values for one pose: rig defaults, overridden by `pose` (clamped to [min,max]),
 * then every driver evaluated IN ORDER over params + DOFs + earlier drivers.
 * Unknown DOF names in `pose` throw (the compiler reports them as `unknown_dof`).
 */
export function dofValues(rig: Rig, pose: Record<string, number> = {}): Record<string, number> {
  const v: Record<string, number> = {};
  for (const [k, d] of Object.entries(rig.dofs)) v[k] = d.default;
  for (const [k, x] of Object.entries(pose)) {
    const d = rig.dofs[k];
    if (!d) throw new Error(`unknown dof ${k}`);
    v[k] = Math.min(d.max, Math.max(d.min, x));
  }
  const env: Record<string, number> = { ...rig.params, ...v };
  for (const [k, e] of Object.entries(rig.drivers)) {
    env[k] = evaluate(e, env);
    v[k] = env[k];
  }
  return v;
}

/**
 * Part-local transform [px,py,pz, rx,ry,rz] (radians, XYZ Euler) of every part for one pose.
 * hinge: rest rotation, then a rotation of (dof * scale) DEGREES about the joint axis, the axis
 *        given in the parent's frame (so it is applied on the left of the rest rotation).
 * slide: rest position + axis * (dof * scale) METRES, axis in the parent's frame.
 * Parts without a joint keep their rest pose.
 */
export function partTransforms(rig: Rig, pose: Record<string, number> = {}): Record<string, number[]> {
  const v = dofValues(rig, pose);
  const out: Record<string, number[]> = {};
  const q = new THREE.Quaternion();
  const qj = new THREE.Quaternion();
  const e = new THREE.Euler();
  for (const [id, rest] of Object.entries(rig.rest)) {
    let [px, py, pz] = rest.at;
    q.setFromEuler(e.set(rest.rot[0], rest.rot[1], rest.rot[2], "XYZ"));
    const j = rig.joints[id];
    if (j) {
      const ax = axisVec(j.axis);
      const n = Math.hypot(ax[0], ax[1], ax[2]) || 1;
      const scale = typeof j.scale === "number" ? j.scale : j.scale ? evaluate(j.scale, { ...rig.params, ...v }) : 1;
      const val = (v[j.dof] ?? 0) * scale;
      if (j.type === "hinge") {
        qj.setFromAxisAngle(new THREE.Vector3(ax[0] / n, ax[1] / n, ax[2] / n), (val * Math.PI) / 180);
        q.premultiply(qj);
      } else {
        px += (ax[0] / n) * val;
        py += (ax[1] / n) * val;
        pz += (ax[2] / n) * val;
      }
    }
    e.setFromQuaternion(q, "XYZ");
    out[id] = [px, py, pz, e.x, e.y, e.z].map((x) => x + 0); // + 0 turns -0 into 0 for stable JSON
  }
  return out;
}
