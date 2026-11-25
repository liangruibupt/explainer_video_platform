// Per-frame shot checks (docs/PREVIS_CONTRACT.md section 8) on a ResolvedShot, using posed part
// bounding boxes (meta.parts bbox mapped by the rig's part transforms), and the top-down plan
// drawing (previs/sNN.plan.png is rasterised from plan() by server/app/previs/shots.py).
import * as THREE from "three";
import type { CheckIssue, ModelMeta, ResolvedShot, V3 } from "../core/types";
import { type Box, boxCenter, objectMatrix, posedBoxes, worldParts, apply } from "./pose";
import type { RObject } from "./compile";

/** What an action or overlay is about, for subject_out_of_frame / subject_occluded (compile.ts fills it). */
export type Subject = { where: string; from: number; to: number; obj?: string; part?: string; parts?: string[]; point?: number[][] };
export type RShot = ResolvedShot & { subjects?: Subject[] };

const D2R = Math.PI / 180;
const at = <T>(rows: T[], f: number): T => rows[Math.min(f, rows.length - 1)];

export const SAFE = 0.05; // safe frame: 5 % of each side
export const INSIDE_PAD = 0.02; // camera_inside margin, metres

/** The renderer's camera for one camera row (PrevisShot.tsx builds the same). */
export function cameraAt(row: number[], aspect: number): THREE.PerspectiveCamera {
  const c = new THREE.PerspectiveCamera(row[6], aspect, 0.01, 200);
  c.position.set(row[0], row[1], row[2]);
  c.up.set(0, 1, 0);
  c.lookAt(row[3], row[4], row[5]);
  if (row[7]) c.rotateZ(row[7] * D2R);
  c.updateMatrixWorld(true);
  c.updateProjectionMatrix();
  return c;
}

/** NDC [x,y] and whether the point is in front of the camera beyond the near plane. */
export function project(c: THREE.PerspectiveCamera, p: V3): { x: number; y: number; front: boolean } {
  const v = new THREE.Vector3(...p);
  const local = v.clone().applyMatrix4(c.matrixWorldInverse);
  const front = -local.z > c.near;
  v.project(c);
  return { x: v.x, y: v.y, front };
}

export interface ObjState {
  obj: RObject;
  meta: ModelMeta;
  visible: number;
  world: Record<string, THREE.Matrix4>;
  boxes: Record<string, Box>;
}

/** Every object's posed world matrices and part boxes at frame f. */
export function frameState(shot: ResolvedShot, metas: Record<string, ModelMeta>, f: number): ObjState[] {
  const out: ObjState[] = [];
  for (const o of shot.objects as RObject[]) {
    const meta = metas[o.id];
    if (!meta) continue;
    const x = at(o.xform, f);
    const local: Record<string, number[]> = {};
    for (const [id, rows] of Object.entries(o.parts)) local[id] = at(rows, f);
    const world = worldParts(meta, local, objectMatrix(x, o.scale ?? 1), o.explode ? at(o.explode, f) : 0);
    out.push({ obj: o, meta, visible: x[6] ?? 1, world, boxes: posedBoxes(meta, world) });
  }
  return out;
}

/** Shrink an AABB to the kept side of an axis-aligned section plane (other normals: unchanged). */
function clipBox(b: Box, n: V3, off: number): Box | null {
  const k = n.findIndex((x) => Math.abs(x) > 0.999);
  if (k < 0) return b;
  const s = Math.sign(n[k]);
  const min = [...b.min] as V3;
  const max = [...b.max] as V3;
  // kept: s * p[k] >= off
  if (s > 0) min[k] = Math.max(min[k], off);
  else max[k] = Math.min(max[k], -off);
  return min[k] <= max[k] ? { min, max } : null;
}

function rayHit(o: V3, d: V3, b: Box): number | null {
  let t0 = -Infinity;
  let t1 = Infinity;
  for (let k = 0; k < 3; k++) {
    if (Math.abs(d[k]) < 1e-12) {
      if (o[k] < b.min[k] || o[k] > b.max[k]) return null;
      continue;
    }
    let a = (b.min[k] - o[k]) / d[k];
    let c = (b.max[k] - o[k]) / d[k];
    if (a > c) [a, c] = [c, a];
    t0 = Math.max(t0, a);
    t1 = Math.min(t1, c);
    if (t0 > t1) return null;
  }
  return t1 < 0 ? null : Math.max(0, t0);
}

const inBox = (p: V3, b: Box, pad: number) => [0, 1, 2].every((k) => p[k] >= b.min[k] - pad && p[k] <= b.max[k] + pad);

/** Transparent (glass / opacity < 0.6) parts do not occlude. Known from a parametric source spec. */
function seeThrough(meta: ModelMeta): Set<string> {
  const s = new Set<string>();
  if (meta.source?.type === "parametric")
    for (const p of meta.source.spec.parts) if (p.color === "glass" || (p.opacity ?? 1) < 0.6) s.add(p.id);
  return s;
}

function descendants(meta: ModelMeta, id: string): Set<string> {
  const out = new Set([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const p of meta.parts) if (p.parent && out.has(p.parent) && !out.has(p.id)) (out.add(p.id), (grew = true));
  }
  return out;
}

/** Scene size: diagonal of every visible part box at frame 0. */
export function sceneSize(states: ObjState[]): number {
  const b = new THREE.Box3();
  for (const s of states) for (const x of Object.values(s.boxes)) b.union(new THREE.Box3(new THREE.Vector3(...x.min), new THREE.Vector3(...x.max)));
  return b.isEmpty() ? 1 : b.max.distanceTo(b.min);
}

/** Sample points of a subject at frame f: a point, or a box centre plus its 6 face centres pulled 20 % in. */
function subjectSamples(s: Subject, f: number, states: ObjState[]): V3[] | null {
  if (s.point) return [at(s.point, f - s.from) as V3];
  const st = states.find((x) => x.obj.id === s.obj);
  if (!st) return null;
  const ids = s.parts ?? (s.part ? [s.part] : Object.keys(st.boxes));
  const all = new THREE.Box3();
  for (const id of ids) {
    const x = st.boxes[id];
    if (x) all.union(new THREE.Box3(new THREE.Vector3(...x.min), new THREE.Vector3(...x.max)));
  }
  if (all.isEmpty()) {
    const m = s.part ? st.world[s.part] : null;
    return m ? [apply(m, [0, 0, 0])] : null;
  }
  const c = all.getCenter(new THREE.Vector3());
  const h = all.getSize(new THREE.Vector3()).multiplyScalar(0.4);
  const out: V3[] = [c.toArray() as V3];
  for (let k = 0; k < 3; k++)
    for (const sg of [-1, 1]) {
      const p = c.clone();
      p.setComponent(k, p.getComponent(k) + sg * h.getComponent(k));
      out.push(p.toArray() as V3);
    }
  return out;
}

export function checkShot(shot: RShot, metas: Record<string, ModelMeta>): CheckIssue[] {
  const issues: CheckIssue[] = [];
  const N = shot.frames;
  const aspect = shot.width / shot.height;
  const cuts = new Set(shot.cuts);
  const once = new Map<string, { first: number; n: number; issue: Omit<CheckIssue, "message"> & { msg: (n: number, first: number) => string } }>();
  const hit = (key: string, f: number, base: Omit<CheckIssue, "message">, msg: (n: number, first: number) => string) => {
    const e = once.get(key);
    if (e) e.n++;
    else once.set(key, { first: f, n: 1, issue: { ...base, msg } });
  };
  const subj = (shot.subjects ?? []).map((s) => ({ s, out: 0, occ: 0, n: 0 }));
  const states0 = frameState(shot, metas, 0);
  const size = sceneSize(states0);
  const glassOf = new Map(Object.entries(metas).map(([k, m]) => [k, seeThrough(m)]));

  let prevDir: THREE.Vector3 | null = null;
  let prevPos: THREE.Vector3 | null = null;
  for (let f = 0; f < N; f++) {
    const row = shot.camera[Math.min(f, shot.camera.length - 1)];
    const cam = cameraAt(row, aspect);
    const pos: V3 = [row[0], row[1], row[2]];
    const states = f === 0 ? states0 : frameState(shot, metas, f);
    const secs = shot.sections.filter((s) => f >= s.from && f <= s.to);
    const boxesOf = (st: ObjState) => {
      const out: [string, Box][] = [];
      for (const [id, b] of Object.entries(st.boxes)) {
        let c: Box | null = b;
        for (const s of secs)
          if (c && s.targets.some((t) => t === st.obj.id || t === `${st.obj.id}.${id}`)) c = clipBox(c, s.normal, s.offset);
        if (c) out.push([id, c]);
      }
      return out;
    };
    // camera_inside
    for (const st of states) {
      if (st.visible < 0.01) continue;
      for (const [id, b] of boxesOf(st))
        if (inBox(pos, b, INSIDE_PAD))
          hit(`inside:${st.obj.id}.${id}`, f, { code: "camera_inside", severity: "error", frame: f, where: `parts.${id}` }, (n, a) => `相机从第 ${a} 帧起有 ${n} 帧在部件 ${st.obj.id}.${id} 里面`);
    }
    // look_out_of_frame
    const lp = project(cam, [row[3], row[4], row[5]]);
    if (!lp.front || Math.abs(lp.x) > 1 || Math.abs(lp.y) > 1)
      hit("look", f, { code: "look_out_of_frame", severity: "error", frame: f, where: "camera" }, (n, a) => `看向点从第 ${a} 帧起有 ${n} 帧不在画面里（或和相机重合）`);
    // camera_jump / fast_motion
    const dir = new THREE.Vector3(row[3] - row[0], row[4] - row[1], row[5] - row[2]).normalize();
    const p = new THREE.Vector3(...pos);
    if (prevDir && prevPos && !cuts.has(f)) {
      const turn = (prevDir.angleTo(dir) * 180) / Math.PI;
      const move = prevPos.distanceTo(p);
      if (move > 0.15 * size || turn > 25)
        hit("jump", f, { code: "camera_jump", severity: "error", frame: f, where: "camera" }, (n, a) => `相机在第 ${a} 帧跳变（移动 ${move.toFixed(2)} m / 转向 ${turn.toFixed(0)}°），不是硬切；共 ${n} 帧`);
      else if (turn * shot.fps > 90)
        hit("fast", f, { code: "fast_motion", severity: "warn", frame: f, where: "camera" }, (n, a) => `相机从第 ${a} 帧起有 ${n} 帧转得太快（超过 90°/秒）`);
    }
    prevDir = dir;
    prevPos = p;
    // subjects
    const safe = 1 - 2 * SAFE;
    for (const x of subj) {
      const s = x.s;
      if (f < s.from || f > s.to) continue;
      const samples = subjectSamples(s, f, states);
      if (!samples) continue;
      const c = samples[0];
      x.n++;
      const q = project(cam, c);
      if (!q.front || Math.abs(q.x) > safe || Math.abs(q.y) > safe) x.out++;
      // occluded when the rays from the camera to every sample point first hit a box of
      // another (opaque, visible) part; the subject's own parts and their children do not count
      const own = new Set<string>();
      const st0 = states.find((y) => y.obj.id === s.obj);
      if (st0) for (const id of s.parts ?? (s.part ? [s.part] : Object.keys(st0.boxes))) for (const d of descendants(st0.meta, id)) own.add(d);
      const blockedRay = (c: V3) => {
        const d: V3 = [c[0] - pos[0], c[1] - pos[1], c[2] - pos[2]];
        const dist = Math.hypot(d[0], d[1], d[2]);
        const tol = dist > 0 ? 1 - 0.02 / dist : 1;
        for (const st of states) {
          if (st.visible < 0.5) continue;
          const mine = st.obj.id === s.obj;
          const glass = glassOf.get(st.obj.id) ?? new Set<string>();
          for (const [id, b] of boxesOf(st)) {
            if ((mine && own.has(id)) || glass.has(id) || inBox(pos, b, 0)) continue;
            const t = rayHit(pos, d, b);
            if (t !== null && t < tol) return true;
          }
        }
        return false;
      };
      if (samples.every(blockedRay)) x.occ++;
    }
  }
  for (const [, e] of once) {
    const { msg, ...rest } = e.issue;
    issues.push({ ...rest, frame: e.first, message: msg(e.n, e.first) });
  }
  for (const x of subj) {
    if (!x.n) continue;
    if (x.out / x.n > 0.3)
      issues.push({ code: "subject_out_of_frame", severity: "warn", where: x.s.where, frame: x.s.from, message: `${x.s.where} 的目标在生效期间有 ${Math.round((100 * x.out) / x.n)}% 的帧不在安全框里` });
    if (x.occ / x.n > 0.5)
      issues.push({ code: "subject_occluded", severity: "warn", where: x.s.where, frame: x.s.from, message: `${x.s.where} 的目标在生效期间有 ${Math.round((100 * x.occ) / x.n)}% 的帧被别的部件挡住` });
  }
  const cs = [...shot.cuts].sort((a, b) => a - b);
  for (let i = 1; i < cs.length; i++)
    if (cs[i] - cs[i - 1] < shot.fps)
      issues.push({ code: "cut_too_close", severity: "warn", frame: cs[i], where: "camera.cuts", message: `第 ${cs[i - 1]} 帧和第 ${cs[i]} 帧两次硬切间隔不到 1 秒` });
  return issues;
}

/**
 * axis_crossing between two consecutive shots of the same set: the camera's side of the subject
 * line (top view) at the end of `prev` and the start of `cur`. Subject line: centres of the
 * first two objects; one object: its local X axis through its centre (front faces +Z).
 */
export function checkAxis(prev: RShot, cur: RShot, metas: Record<string, ModelMeta>): CheckIssue[] {
  const sa = frameState(prev, metas, prev.frames - 1);
  const sb = frameState(cur, metas, 0);
  const side = (st: ObjState[], row: number[]) => {
    if (!st.length) return 0;
    const c = (s: ObjState) => {
      const b = new THREE.Box3();
      for (const x of Object.values(s.boxes)) b.union(new THREE.Box3(new THREE.Vector3(...x.min), new THREE.Vector3(...x.max)));
      return b.getCenter(new THREE.Vector3());
    };
    const a = c(st[0]);
    let dir: THREE.Vector3;
    if (st.length > 1) dir = c(st[1]).sub(a);
    else {
      const x = at(st[0].obj.xform, 0);
      dir = new THREE.Vector3(1, 0, 0).applyEuler(new THREE.Euler(x[3], x[4], x[5], "XYZ"));
    }
    const v = new THREE.Vector3(row[0] - a.x, 0, row[2] - a.z);
    const cross = dir.x * v.z - dir.z * v.x;
    return Math.abs(cross) < 1e-6 ? 0 : Math.sign(cross);
  };
  const a = side(sa, prev.camera[prev.camera.length - 1]);
  const b = side(sb, cur.camera[0]);
  if (a && b && a !== b)
    return [{ code: "axis_crossing", severity: "warn", frame: 0, where: "camera.keys[0]", message: `和上一个镜头 ${prev.id} 相比，相机跑到了主体连线的另一侧（越轴）` }];
  return [];
}

// ---------- top-down plan drawing ----------

export type PlanItem =
  | { t: "rect"; x: number; y: number; w: number; h: number; fill: string; stroke: string }
  | { t: "line"; pts: number[]; color: string; width: number }
  | { t: "circle"; x: number; y: number; r: number; fill: string; stroke: string }
  | { t: "text"; x: number; y: number; s: string; size: number; color: string };

export interface PlanDrawing {
  width: number;
  height: number;
  background: string;
  items: PlanItem[];
}

const hue = (u: number) => {
  const c = new THREE.Color().setHSL(0.62 - 0.62 * u, 0.75, 0.45);
  return `#${c.getHexString()}`;
};

/** Top view (x right, +z down): part boxes at frame 0, camera path coloured by time, key numbers, anchors. */
export function plan(shot: RShot, metas: Record<string, ModelMeta>, W = 900, H = 900): PlanDrawing {
  const st = frameState(shot, metas, 0);
  const xs: number[] = [];
  const zs: number[] = [];
  for (const s of st) for (const b of Object.values(s.boxes)) xs.push(b.min[0], b.max[0]), zs.push(b.min[2], b.max[2]);
  for (const r of shot.camera) xs.push(r[0], r[3]), zs.push(r[2], r[5]);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minZ = Math.min(...zs);
  const maxZ = Math.max(...zs);
  const top = 70;
  const pad = 50;
  const span = Math.max(maxX - minX, maxZ - minZ, 0.1);
  const k = Math.min(W - 2 * pad, H - top - pad - 40) / span;
  const ox = W / 2 - ((minX + maxX) / 2) * k;
  const oz = top + (H - top - 40) / 2 - ((minZ + maxZ) / 2) * k;
  const X = (x: number) => Math.round((ox + x * k) * 10) / 10;
  const Z = (z: number) => Math.round((oz + z * k) * 10) / 10;
  const items: PlanItem[] = [];
  // 10 cm grid
  const step = span > 4 ? 1 : span > 1.5 ? 0.25 : 0.1;
  for (let g = Math.floor(minX / step) * step; g <= maxX + step; g += step) items.push({ t: "line", pts: [X(g), Z(minZ - step), X(g), Z(maxZ + step)], color: "#e2ddd5", width: 1 });
  for (let g = Math.floor(minZ / step) * step; g <= maxZ + step; g += step) items.push({ t: "line", pts: [X(minX - step), Z(g), X(maxX + step), Z(g)], color: "#e2ddd5", width: 1 });
  for (const s of st)
    for (const b of Object.values(s.boxes))
      items.push({ t: "rect", x: X(b.min[0]), y: Z(b.min[2]), w: Math.max(1, (b.max[0] - b.min[0]) * k), h: Math.max(1, (b.max[2] - b.min[2]) * k), fill: "#cfc9bf", stroke: "#8f887e" });
  const N = shot.camera.length;
  const cuts = new Set(shot.cuts);
  for (let f = 1; f < N; f++) {
    if (cuts.has(f)) continue;
    const a = shot.camera[f - 1];
    const b = shot.camera[f];
    items.push({ t: "line", pts: [X(a[0]), Z(a[2]), X(b[0]), Z(b[2])], color: hue(f / Math.max(1, N - 1)), width: 3 });
  }
  for (let f = 0; f < N; f += 30) {
    const r = shot.camera[f];
    items.push({ t: "line", pts: [X(r[0]), Z(r[2]), X(r[0] + (r[3] - r[0]) * 0.5), Z(r[2] + (r[5] - r[2]) * 0.5)], color: "#9a948b", width: 1 });
  }
  let keyNo = 0;
  for (const a of shot.anchors) {
    const r = shot.camera[Math.min(a.frame, N - 1)];
    if (/^camera\.keys\[\d+\]\.at$/.test(a.where)) {
      keyNo++;
      items.push({ t: "circle", x: X(r[0]), y: Z(r[2]), r: 11, fill: "#ffffff", stroke: hue(a.frame / Math.max(1, N - 1)) });
      items.push({ t: "text", x: X(r[0]) - 4, y: Z(r[2]) + 5, s: String(keyNo), size: 13, color: "#333333" });
    } else {
      items.push({ t: "circle", x: X(r[0]), y: Z(r[2]), r: 4, fill: hue(a.frame / Math.max(1, N - 1)), stroke: "#333333" });
      const label = a.anchor.word ?? a.anchor.mark ?? (a.anchor.t !== undefined ? `${a.anchor.t}s` : `f${a.anchor.frame}`);
      items.push({ t: "text", x: X(r[0]) + 8, y: Z(r[2]) - 6, s: `${label} f${a.frame}`, size: 13, color: "#444444" });
    }
  }
  items.push({ t: "text", x: 20, y: 30, s: `${shot.id} 俯视图：相机路径（蓝→红 = 时间）、关键帧编号、锚点`, size: 18, color: "#222222" });
  items.push({ t: "text", x: 20, y: 54, s: `${N} 帧 · 网格 ${step * 100} cm · +X 向右，+Z 向下`, size: 14, color: "#555555" });
  for (let i = 0; i < 20; i++) items.push({ t: "rect", x: 20 + i * 12, y: H - 30, w: 12, h: 10, fill: hue(i / 19), stroke: hue(i / 19) });
  return { width: W, height: H, background: "#f6f4f0", items };
}

export function planSvg(d: PlanDrawing): string {
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const body = d.items
    .map((i) => {
      if (i.t === "rect") return `<rect x="${i.x}" y="${i.y}" width="${i.w}" height="${i.h}" fill="${i.fill}" stroke="${i.stroke}"/>`;
      if (i.t === "line") return `<line x1="${i.pts[0]}" y1="${i.pts[1]}" x2="${i.pts[2]}" y2="${i.pts[3]}" stroke="${i.color}" stroke-width="${i.width}" stroke-linecap="round"/>`;
      if (i.t === "circle") return `<circle cx="${i.x}" cy="${i.y}" r="${i.r}" fill="${i.fill}" stroke="${i.stroke}" stroke-width="2"/>`;
      return `<text x="${i.x}" y="${i.y}" font-size="${i.size}" fill="${i.color}" font-family="Noto Sans CJK SC, sans-serif">${esc(i.s)}</text>`;
    })
    .join("\n");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${d.width}" height="${d.height}"><rect width="100%" height="100%" fill="${d.background}"/>\n${body}\n</svg>\n`;
}
