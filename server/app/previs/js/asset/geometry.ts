// Primitive shapes of an AssetSpec part -> three.js BufferGeometry in the part's own frame
// (docs/PREVIS_CONTRACT.md section 4.1). Every shape is centred on the part origin, except
// `extrude` (profile in XY, depth 0..+depth along Z) and `lathe` (profile [r, y] as written).
// Output geometry: indexed, attributes position + normal only (no uv: v1 has no textures).
import * as THREE from "three";
import { mergeGeometries, mergeVertices } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { evaluate } from "../core/expr";
import type { PartSpec } from "../core/types";

export class ShapeError extends Error {}

const AX_ROT: Record<string, (g: THREE.BufferGeometry) => void> = {
  y: () => {},
  x: (g) => g.rotateZ(-Math.PI / 2), // +Y -> +X
  z: (g) => g.rotateX(Math.PI / 2), // +Y -> +Z
};

/** position + normal, non-indexed, so every shape merges with every other. */
function clean(g: THREE.BufferGeometry): THREE.BufferGeometry {
  const n = g.index ? g.toNonIndexed() : g;
  for (const k of Object.keys(n.attributes)) if (k !== "position" && k !== "normal") n.deleteAttribute(k);
  if (!n.attributes.normal) n.computeVertexNormals();
  return n;
}

/** Flip winding and normals (inner wall of a tube faces inwards). */
function flip(g: THREE.BufferGeometry): THREE.BufferGeometry {
  const p = g.attributes.position.array as Float32Array;
  const nm = g.attributes.normal.array as Float32Array;
  for (let t = 0; t < p.length; t += 9) {
    for (let k = 0; k < 3; k++) {
      const a = t + 3 + k;
      const b = t + 6 + k;
      [p[a], p[b]] = [p[b], p[a]];
      [nm[a], nm[b]] = [nm[b], nm[a]];
    }
  }
  for (let i = 0; i < nm.length; i++) nm[i] = -nm[i];
  return g;
}

export interface ShapeCtx {
  env: Record<string, number>;
}

/** Evaluate a shape number; `need` = required. Throws ExprError (from expr) or ShapeError. */
function num(part: PartSpec, key: keyof PartSpec, env: Record<string, number>, need = true, dflt?: number): number {
  const raw = part[key] as unknown;
  if (raw === undefined || raw === null) {
    if (dflt !== undefined) return dflt;
    if (need) throw new ShapeError(`缺少形状参数 ${String(key)}`);
    return NaN;
  }
  if (typeof raw !== "number" && typeof raw !== "string") throw new ShapeError(`形状参数 ${String(key)} 不是数字或表达式`);
  return evaluate(raw, env);
}

function pos(name: string, v: number) {
  if (!(v > 0)) throw new ShapeError(`${name} 必须大于 0（现在是 ${v}）`);
}

function segs(part: PartSpec, dflt: number): number {
  const s = part.segments ?? dflt;
  if (!Number.isInteger(s) || s < 3 || s > 256) throw new ShapeError(`segments 必须是 3..256 的整数（现在是 ${s}）`);
  return s;
}

/** Geometry for one part (before `array`), or null for a group. */
export function shapeGeometry(part: PartSpec, env: Record<string, number>): THREE.BufferGeometry | null {
  const axis = part.axis ?? "y";
  if (!(axis in AX_ROT)) throw new ShapeError(`axis 只能是 x / y / z（现在是 ${String(axis)}）`);
  let g: THREE.BufferGeometry;
  switch (part.shape) {
    case "group":
      return null;
    case "box": {
      if (!Array.isArray(part.size) || part.size.length !== 3) throw new ShapeError("box 需要 size [w,h,d]");
      const [w, h, d] = part.size.map((x) => evaluate(x, env));
      pos("size[0]", w);
      pos("size[1]", h);
      pos("size[2]", d);
      return clean(new THREE.BoxGeometry(w, h, d));
    }
    case "cylinder": {
      const r = num(part, "r", env);
      const h = num(part, "h", env);
      const rt = num(part, "r_top", env, false, r);
      pos("r", r);
      pos("h", h);
      if (!(rt >= 0)) throw new ShapeError(`r_top 不能小于 0（现在是 ${rt}）`);
      g = new THREE.CylinderGeometry(rt, r, h, segs(part, 48));
      break;
    }
    case "cone": {
      const r = num(part, "r", env);
      const h = num(part, "h", env);
      pos("r", r);
      pos("h", h);
      g = new THREE.ConeGeometry(r, h, segs(part, 48));
      break;
    }
    case "tube": {
      const ro = num(part, "r_outer", env);
      const ri = num(part, "r_inner", env);
      const h = num(part, "h", env);
      pos("r_outer", ro);
      pos("r_inner", ri);
      pos("h", h);
      if (ri >= ro) throw new ShapeError(`内径 r_inner（${ri}）必须小于外径 r_outer（${ro}）`);
      const s = segs(part, 48);
      const outer = clean(new THREE.CylinderGeometry(ro, ro, h, s, 1, true));
      const inner = flip(clean(new THREE.CylinderGeometry(ri, ri, h, s, 1, true)));
      const top = clean(new THREE.RingGeometry(ri, ro, s).rotateX(-Math.PI / 2).translate(0, h / 2, 0));
      const bot = clean(new THREE.RingGeometry(ri, ro, s).rotateX(Math.PI / 2).translate(0, -h / 2, 0));
      g = mergeGeometries([outer, inner, top, bot])!;
      break;
    }
    case "sphere": {
      const r = num(part, "r", env);
      pos("r", r);
      const s = segs(part, 32);
      return clean(new THREE.SphereGeometry(r, s, Math.max(3, Math.round(s / 2))));
    }
    case "capsule": {
      const r = num(part, "r", env);
      const h = num(part, "h", env);
      pos("r", r);
      if (!(h >= 0)) throw new ShapeError(`h 不能小于 0（现在是 ${h}）`);
      g = new THREE.CapsuleGeometry(r, h, 8, segs(part, 32));
      break;
    }
    case "torus": {
      // ring of radius r around the part's axis (default y: lying flat), tube = section radius
      const r = num(part, "r", env);
      const t = num(part, "tube", env);
      pos("r", r);
      pos("tube", t);
      if (t >= r) throw new ShapeError(`tube（${t}）必须小于环半径 r（${r}）`);
      g = new THREE.TorusGeometry(r, t, 16, segs(part, 48)).rotateX(Math.PI / 2); // axis z -> y
      break;
    }
    case "extrude": {
      const prof = part.profile;
      if (!Array.isArray(prof) || prof.length < 3) throw new ShapeError("extrude 需要至少 3 个点的 profile");
      const pts = prof.map((p) => {
        if (!Array.isArray(p) || p.length !== 2) throw new ShapeError("profile 的每个点是 [x,y]");
        return new THREE.Vector2(evaluate(p[0], env), evaluate(p[1], env));
      });
      if (Math.abs(THREE.ShapeUtils.area(pts)) < 1e-12) throw new ShapeError("extrude 的 profile 面积为 0");
      const depth = num(part, "depth", env);
      pos("depth", depth);
      const bevel = num(part, "bevel", env, false, 0);
      if (!(bevel >= 0) || 2 * bevel >= depth) throw new ShapeError(`bevel 必须在 0 到 depth/2 之间（现在是 ${bevel}）`);
      // bevel inside the profile and inside 0..depth, so the declared size stays exact
      const ex = new THREE.ExtrudeGeometry(new THREE.Shape(pts), {
        depth: depth - 2 * bevel,
        bevelEnabled: bevel > 0,
        bevelThickness: bevel,
        bevelSize: bevel,
        bevelOffset: -bevel,
        bevelSegments: 2,
        curveSegments: 12,
      });
      return clean(ex.translate(0, 0, bevel));
    }
    case "lathe": {
      const prof = part.profile;
      if (!Array.isArray(prof) || prof.length < 2) throw new ShapeError("lathe 需要至少 2 个点的 profile");
      const pts = prof.map((p) => {
        if (!Array.isArray(p) || p.length !== 2) throw new ShapeError("profile 的每个点是 [r,y]");
        const v = new THREE.Vector2(evaluate(p[0], env), evaluate(p[1], env));
        if (!(v.x >= 0)) throw new ShapeError(`lathe 的半径不能小于 0（现在是 ${v.x}）`);
        return v;
      });
      const ang = num(part, "angle_deg", env, false, 360);
      if (!(ang > 0 && ang <= 360)) throw new ShapeError(`angle_deg 必须在 (0, 360] 之间（现在是 ${ang}）`);
      g = new THREE.LatheGeometry(pts, segs(part, 48), 0, (ang * Math.PI) / 180);
      g.computeVertexNormals();
      return clean(g);
    }
    default:
      throw new ShapeError(`未知的形状 ${String((part as { shape: unknown }).shape)}`);
  }
  AX_ROT[axis](g);
  return clean(g);
}

/** Merge copies (non-indexed geometries), then weld into an indexed geometry. */
export function mergeAndWeld(parts: THREE.BufferGeometry[]): THREE.BufferGeometry {
  const m = parts.length === 1 ? parts[0] : mergeGeometries(parts)!;
  return mergeVertices(m, 1e-7);
}
