// Shot compiler (docs/PREVIS_CONTRACT.md sections 7-8): PrevisPlan + shot id + words.json +
// durations.json + asset metas -> ResolvedShot. Every pose goes through core/rig.ts
// partTransforms; the renderer only reads the per-frame arrays this writes.
//
// Conventions this file fixes where the contract is silent (listed in the PR):
// - Default ease is "inOut" for camera keys, pose, move and explode.
// - pose / move / explode without `until` jump at `at`; spin, highlight, label, dimension and
//   section without `until` last to the shot's end.
// - A target that is first touched by `show` starts hidden.
// - style.fade_in / fade_out are seconds in the shot spec and FRAMES in the ResolvedShot.
// - Targets (contract 15.10): show / hide / highlight / section take "obj" or "obj.part";
//   pose / spin / move / explode only "obj". Points take coordinates, marks, "obj", "obj.anchor".
// - Extra fields on the ResolvedShot (the renderer reads them, the core type does not have them):
//   objects[].part_visible (show / hide of single parts),
//   objects[].scale, objects[].explode_dirs (object-space offset per part for factor 1),
//   highlight[].part (highlight one part instead of the whole object),
//   label overlays' `track` (per-frame world points from `from` to `to`, one entry = constant).
// - section: three.js Plane(normal, -offset): kept where dot(normal, p) >= offset.
import { evaluate } from "../core/expr";
import { partTransforms } from "../core/rig";
import type {
  Action,
  CameraKey,
  CheckIssue,
  Ease,
  ModelMeta,
  PointRef,
  PrevisPlan,
  ResolvedShot,
  SetObject,
  SetSpec,
  ShotSpec,
  TimeAnchor,
  V3,
  Vec3,
  WordEntry,
} from "../core/types";
import { catmull, ease, lensToVfov, lerp, lerp3 } from "./ease";
import { apply, explodeDirs, objectMatrix, worldParts } from "./pose";
import type * as THREE from "three";
import type { Subject } from "./check";

export const FPS = 30;
/** The punctuation captions.py strips (skills/explainer-video-pipeline/scripts/captions.py PUNCT). */
export const PUNCT = /[\s，。、！？；：,.!?;:“”"'（）()…—-]/g;
export const stripPunct = (s: string) => s.replace(PUNCT, "");

export interface AssetInfo {
  meta: ModelMeta;
  /** GLB path relative to the Remotion public dir, e.g. "previs/asset_x.glb". */
  glb: string;
  /** Library asset id written into the ResolvedShot (default: the set object's `asset`). */
  id?: string;
}

export interface CompileInput {
  plan: PrevisPlan;
  shot: string;
  /** Keyed by the set object's `asset` string ("asset_..." or "@spec_id"). */
  assets: Record<string, AssetInfo>;
  words: WordEntry[];
  durations: number[];
  /** Scene ids in film order (durations[i] belongs to order[i]); default: plan.shots order. */
  order?: string[];
  width: number;
  height: number;
  fps?: number;
}

export interface Compiled {
  shot: ResolvedShot & { subjects: Subject[] };
  issues: CheckIssue[];
}

/** Extra per-object data that is not in the core type. */
export type RObject = ResolvedShot["objects"][number] & {
  scale?: number;
  explode_dirs?: Record<string, V3>;
  /** show / hide of single parts: part id -> per-frame visibility 0..1 (one entry = constant). */
  part_visible?: Record<string, number[]>;
  highlight?: { from: number; to: number; color: string; part?: string }[];
};

const r6 = (x: number) => Math.round(x * 1e6) / 1e6 + 0;
const r6a = (a: number[]) => a.map(r6);
const D2R = Math.PI / 180;

/** One entry when every row is equal (contract section 8: constant arrays store one element). */
export function compact(rows: number[][]): number[][] {
  if (!rows.length) return rows;
  const a = rows[0];
  for (const r of rows) if (r.length !== a.length || r.some((x, i) => x !== a[i])) return rows;
  return [a];
}

const num = (x: unknown): number => (typeof x === "number" ? x : evaluate(x as string, {}));
const vec = (v: Vec3 | undefined, d: V3 = [0, 0, 0]): V3 => (v ? [num(v[0]), num(v[1]), num(v[2])] : d);

type Seg = { a: number; b: number; kind: "pose" | "spin"; to: number; rate: number; ease?: Ease; where: string };

export function compileShot(inp: CompileInput): Compiled {
  const fps = inp.fps ?? FPS;
  const issues: CheckIssue[] = [];
  const err = (code: string, message: string, where?: string, frame?: number) =>
    issues.push({ code, severity: "error", message, where, ...(frame !== undefined ? { frame } : {}) });
  const warn = (code: string, message: string, where?: string, frame?: number) =>
    issues.push({ code, severity: "warn", message, where, ...(frame !== undefined ? { frame } : {}) });

  const spec: ShotSpec | undefined = inp.plan.shots.find((s) => s.id === inp.shot);
  if (!spec) throw new Error(`no shot ${inp.shot} in plan`);
  const order = inp.order ?? inp.plan.shots.map((s) => s.id);
  const idx = order.indexOf(spec.id);
  if (idx < 0 || idx >= inp.durations.length) throw new Error(`no duration for shot ${spec.id}`);
  const N = Math.max(1, Math.round(inp.durations[idx]));
  const start = inp.durations.slice(0, idx).reduce((a, b) => a + b, 0) / fps;
  const last = N - 1;

  const set: SetSpec | undefined = inp.plan.sets.find((s) => s.id === spec.set);
  if (!set) err("unknown_target", `镜头引用的布景 ${spec.set} 不存在`, "set");
  const objs: SetObject[] = set?.objects ?? [];
  const objById = new Map<string, SetObject>();
  const metaOf = new Map<string, ModelMeta>();
  objs.forEach((o, i) => {
    const a = inp.assets[o.asset];
    if (!a) {
      err("unknown_target", `物体 ${o.id} 的资产 ${o.asset} 没有找到`, `set.objects[${i}]`);
      return;
    }
    objById.set(o.id, o);
    metaOf.set(o.id, a.meta);
  });

  // ---------- time anchors ----------
  const anchors: ResolvedShot["anchors"] = [];
  const chars: { c: string; w: WordEntry }[] = [];
  for (const w of inp.words) if (w.scene === spec.id) for (const c of stripPunct(w.text)) chars.push({ c, w });
  const text = chars.map((x) => x.c).join("");

  const anchor = (a: TimeAnchor | undefined, where: string, dflt: number): number => {
    if (!a) return dflt;
    const kinds = (["t", "frame", "word", "mark"] as const).filter((k) => a[k] !== undefined);
    if (kinds.length !== 1) {
      err("anchor_not_found", `时间锚点必须只写 t / frame / word / mark 中的一种（写了 ${kinds.length} 种）`, where);
      return dflt;
    }
    const off = a.offset ?? 0;
    let f: number;
    let wordTime: number | undefined;
    if (a.t !== undefined) f = Math.round((a.t + off) * fps);
    else if (a.frame !== undefined) f = Math.round(a.frame + off * fps);
    else if (a.mark !== undefined) f = (a.mark === "end" ? last : 0) + Math.round(off * fps);
    else {
      const want = stripPunct(a.word!);
      const occ = Math.max(1, a.occurrence ?? 1);
      let at = -1;
      for (let k = 0, from = 0; k < occ; k++) {
        at = want ? text.indexOf(want, from) : -1;
        if (at < 0) break;
        from = at + 1;
      }
      if (at < 0) {
        err("anchor_not_found", `旁白里找不到第 ${occ} 处「${a.word}」`, where);
        anchors.push({ where, anchor: a, frame: dflt });
        return dflt;
      }
      wordTime = a.edge === "end" ? chars[at + want.length - 1].w.end : chars[at].w.start;
      f = Math.round((wordTime - start + off) * fps);
    }
    const c = Math.min(last, Math.max(0, f));
    if (c !== f) warn("anchor_clamped", `时间锚点落在镜头外（第 ${f} 帧），已夹到第 ${c} 帧`, where, c);
    anchors.push({ where, anchor: a, frame: c, ...(wordTime !== undefined ? { word_time: wordTime } : {}) });
    return c;
  };

  // ---------- targets ----------
  /** "obj" or "obj.part" -> [object, part|undefined]; null (and an issue) when unknown. */
  const target = (t: string, where: string, allowPart: boolean): [string, string | undefined] | null => {
    const [o, p, ...rest] = String(t).split(".");
    const meta = metaOf.get(o);
    if (!objById.has(o) || !meta || rest.length || (p !== undefined && !allowPart)) {
      err("unknown_target", `目标 ${t} 不存在${p !== undefined && !allowPart ? "（这个动作只能作用于整个物体）" : ""}`, where);
      return null;
    }
    if (p !== undefined && !meta.rig.rest[p]) {
      err("unknown_target", `物体 ${o} 没有部件 ${p}`, where);
      return null;
    }
    return [o, p];
  };

  // ---------- actions -> per object tracks ----------
  const actions: Action[] = spec.actions ?? [];
  const dofSegs = new Map<string, Map<string, Seg[]>>(); // obj -> dof -> segments
  type MoveSeg = { a: number; b: number; to: V3; rot?: V3; ease?: Ease };
  const moveSegs = new Map<string, MoveSeg[]>();
  const visSegs = new Map<string, { a: number; fade: number; on: boolean }[]>();
  const explSegs = new Map<string, { a: number; b: number; to: number; ease?: Ease }[]>();
  const highlights = new Map<string, { from: number; to: number; color: string; part?: string }[]>();
  const overlays: ResolvedShot["overlays"] = [];
  const labelRefs: { o: number; ref: PointRef; where: string }[] = [];
  const dimRefs: { o: number; from: PointRef; to: PointRef; where: string }[] = [];
  const sections: ResolvedShot["sections"] = [];
  const subjects: Subject[] = [];
  /** pose / spin subjects: the parts that move during the action, filled in after the tracks. */
  const motion: { s: Subject }[] = [];

  actions.forEach((act, i) => {
    const w = `actions[${i}]`;
    const a = anchor(act.at, `${w}.at`, 0);
    const u = "until" in act ? act.until : undefined;
    const b = Math.max(a, anchor(u, `${w}.until`, act.op === "pose" || act.op === "move" || act.op === "explode" ? a : last));
    switch (act.op) {
      case "pose":
      case "spin": {
        const t = target(act.target, w, false);
        if (!t) return;
        const rig = metaOf.get(t[0])!.rig;
        if (!rig.dofs[act.dof]) {
          err("unknown_dof", `物体 ${t[0]} 没有自由度 ${act.dof}`, w);
          return;
        }
        motion.push({ s: { where: w, from: a, to: b, obj: t[0] } });
        const m = dofSegs.get(t[0]) ?? new Map<string, Seg[]>();
        dofSegs.set(t[0], m);
        const l = m.get(act.dof) ?? [];
        m.set(act.dof, l);
        l.push(
          act.op === "pose"
            ? { a, b, kind: "pose", to: act.to, rate: 0, ease: act.ease, where: w }
            : { a, b, kind: "spin", to: 0, rate: act.deg_per_s, where: w },
        );
        break;
      }
      case "move": {
        const t = target(act.target, w, false);
        if (!t) return;
        subjects.push({ where: w, from: a, to: b, obj: t[0] });
        const l = moveSegs.get(t[0]) ?? [];
        moveSegs.set(t[0], l);
        l.push({ a, b, to: vec(act.to), rot: act.rot_deg ? (vec(act.rot_deg).map((x) => x * D2R) as V3) : undefined, ease: act.ease });
        break;
      }
      case "show":
      case "hide": {
        const t = target(act.target, w, true);
        if (!t) return;
        const key = t[1] ? `${t[0]}.${t[1]}` : t[0];
        const l = visSegs.get(key) ?? [];
        visSegs.set(key, l);
        l.push({ a, fade: Math.max(0, Math.round((act.fade ?? 0) * fps)), on: act.op === "show" });
        break;
      }
      case "highlight": {
        const t = target(act.target, w, true);
        if (!t) return;
        subjects.push({ where: w, from: a, to: b, obj: t[0], ...(t[1] ? { part: t[1] } : {}) });
        const l = highlights.get(t[0]) ?? [];
        highlights.set(t[0], l);
        l.push({ from: a, to: b, color: act.color ?? "#ffd24a", ...(t[1] ? { part: t[1] } : {}) });
        break;
      }
      case "label":
        labelRefs.push({ o: overlays.length, ref: act.attach, where: w });
        overlays.push({ kind: "label", from: a, to: b, text: act.text, point: [0, 0, 0], side: act.side ?? "right" });
        break;
      case "dimension":
        dimRefs.push({ o: overlays.length, from: act.from, to: act.to, where: w });
        overlays.push({ kind: "dimension", from: a, to: b, a: [0, 0, 0], b: [0, 0, 0], text: act.text ?? "" });
        break;
      case "section": {
        const ok = (act.targets ?? []).filter((t) => target(t, w, true));
        if (!ok.length) return;
        const n = act.normal;
        const len = Math.hypot(n[0], n[1], n[2]) || 1;
        sections.push({ from: a, to: b, targets: ok, normal: [n[0] / len, n[1] / len, n[2] / len], offset: act.offset ?? 0 });
        break;
      }
      case "explode": {
        const t = target(act.target, w, false);
        if (!t) return;
        subjects.push({ where: w, from: a, to: b, obj: t[0] });
        const l = explSegs.get(t[0]) ?? [];
        explSegs.set(t[0], l);
        l.push({ a, b, to: act.factor, ease: act.ease });
        break;
      }
      default:
        err("unknown_target", `未知动作 ${(act as { op: string }).op}`, w);
    }
  });

  // dof_conflict: two actions drive the same DOF of the same object on a common frame range.
  for (const [o, m] of dofSegs)
    for (const [dof, segs] of m)
      for (let x = 0; x < segs.length; x++)
        for (let y = x + 1; y < segs.length; y++) {
          const s = segs[x];
          const t = segs[y];
          const lo = Math.max(s.a, t.a);
          const hi = Math.min(s.b, t.b);
          const inside = (p: number, q: Seg) => q.a < p && p < q.b;
          if (lo < hi || (lo === hi && ((s.a === s.b && inside(s.a, t)) || (t.a === t.b && inside(t.a, s)))))
            err("dof_conflict", `${s.where} 和 ${t.where} 在第 ${lo} 帧同时控制 ${o}.${dof}`, t.where, lo);
        }

  /** Per-frame values from a base and segments applied in `at` order (later ones win from their `at`). */
  const chain = (base: number, segs: { a: number; b: number; ease?: Ease; value: (v0: number, f: number) => number }[]) => {
    const v = new Array<number>(N).fill(base);
    for (const s of [...segs].sort((p, q) => p.a - q.a)) {
      const v0 = v[s.a];
      for (let f = s.a; f < N; f++) v[f] = s.value(v0, f);
    }
    return v;
  };
  const ramp = (a: number, b: number, e: Ease | undefined, f: number) => (b <= a ? 1 : ease(e ?? "inOut", (Math.min(f, b) - a) / (b - a)));

  // ---------- per object: dofs -> part tracks, xform, explode ----------
  const rObjects: RObject[] = [];
  const worldCache = new Map<string, Record<string, THREE.Matrix4>[]>();
  const objMat: Record<string, THREE.Matrix4[]> = {};
  for (const o of objs) {
    const meta = metaOf.get(o.id);
    const ai = inp.assets[o.asset];
    if (!meta || !ai) continue;
    // DOF values per frame
    const base: Record<string, number> = {};
    for (const [k, d] of Object.entries(meta.rig.dofs)) base[k] = o.pose?.[k] ?? d.default;
    for (const k of Object.keys(o.pose ?? {}))
      if (!meta.rig.dofs[k]) err("unknown_dof", `布景里物体 ${o.id} 的初始姿态写了不存在的自由度 ${k}`, `set.objects.${o.id}.pose`);
    const tracks: Record<string, number[]> = {};
    for (const [dof, segs] of dofSegs.get(o.id) ?? [])
      tracks[dof] = chain(
        base[dof],
        segs.map((s) => ({
          a: s.a,
          b: s.b,
          value:
            s.kind === "pose"
              ? (v0: number, f: number) => lerp(v0, s.to, ramp(s.a, s.b, s.ease, f))
              : (v0: number, f: number) => v0 + (s.rate * (Math.min(f, s.b) - s.a)) / fps,
        })),
      );
    const partRows: Record<string, number[][]> = {};
    for (const id of Object.keys(meta.rig.rest)) partRows[id] = [];
    const locals: Record<string, number[]>[] = [];
    let prevKey = "";
    let prev: Record<string, number[]> = {};
    for (let f = 0; f < N; f++) {
      const pose: Record<string, number> = { ...base };
      for (const [k, t] of Object.entries(tracks)) pose[k] = t[f];
      const key = JSON.stringify(pose);
      if (key !== prevKey) {
        prev = partTransforms(meta.rig, pose);
        prevKey = key;
      }
      locals.push(prev);
      for (const id of Object.keys(partRows)) partRows[id].push(r6a(prev[id] ?? [0, 0, 0, 0, 0, 0]));
    }
    // object transform per frame
    const p0 = vec(o.at);
    const q0 = vec(o.rot_deg).map((x) => x * D2R) as V3;
    const six = [0, 1, 2, 3, 4, 5].map((k) =>
      chain(
        k < 3 ? p0[k] : q0[k - 3],
        (moveSegs.get(o.id) ?? []).map((s) => ({
          a: s.a,
          b: s.b,
          value: (v0: number, f: number) => {
            const to = k < 3 ? s.to[k] : s.rot ? s.rot[k - 3] : v0;
            return lerp(v0, to, ramp(s.a, s.b, s.ease, f));
          },
        })),
      ),
    );
    const visTrack = (vis: { a: number; fade: number; on: boolean }[]) =>
      chain(
        vis.length && [...vis].sort((p, q) => p.a - q.a)[0].on ? 0 : 1,
        vis.map((s) => ({ a: s.a, b: s.a + s.fade, value: (v0: number, f: number) => lerp(v0, s.on ? 1 : 0, ramp(s.a, s.a + s.fade, "linear", f)) })),
      );
    const visible = visTrack(visSegs.get(o.id) ?? []);
    const partVisible: Record<string, number[]> = {};
    for (const [k, segs] of visSegs)
      if (k.startsWith(`${o.id}.`)) partVisible[k.slice(o.id.length + 1)] = compact(visTrack(segs).map((x) => [r6(x)])).map((x) => x[0]);
    const xform = Array.from({ length: N }, (_, f) => r6a([...six.map((t) => t[f]), visible[f]]));
    const ex = explSegs.get(o.id);
    const explode = ex ? chain(0, ex.map((s) => ({ a: s.a, b: s.b, value: (v0: number, f: number) => lerp(v0, s.to, ramp(s.a, s.b, s.ease, f)) }))).map(r6) : null;
    const scale = o.scale ?? 1;
    objMat[o.id] = xform.map((x) => objectMatrix(x, scale));
    worldCache.set(
      o.id,
      locals.map((l, f) => worldParts(meta, l, objMat[o.id][f], explode ? explode[f] : 0)),
    );
    const parts: Record<string, number[][]> = {};
    for (const [id, rows] of Object.entries(partRows)) parts[id] = compact(rows);
    const ro: RObject = {
      id: o.id,
      asset: ai.id ?? o.asset,
      glb: ai.glb,
      ...(o.color ? { color: o.color } : {}),
      xform: compact(xform),
      parts,
      ...(highlights.get(o.id) ? { highlight: highlights.get(o.id) } : {}),
      ...(explode ? { explode: compact(explode.map((x) => [x])).map((x) => x[0]), explode_dirs: explodeDirs(meta) } : {}),
      ...(scale !== 1 ? { scale } : {}),
      ...(Object.keys(partVisible).length ? { part_visible: partVisible } : {}),
    };
    rObjects.push(ro);
  }

  // pose / spin: the subject is the set of parts whose transform changes during the action
  for (const { s } of motion) {
    const ro = rObjects.find((o) => o.id === s.obj);
    if (!ro || s.to <= s.from) continue;
    const moving = Object.entries(ro.parts)
      .filter(([, rows]) => rows.length > 1 && rows.slice(s.from, s.to + 1).some((r) => r.some((x, k) => x !== rows[s.from][k])))
      .map(([id]) => id);
    if (moving.length) subjects.push({ ...s, parts: moving });
  }

  // ---------- points ----------
  const marks = set?.marks ?? {};
  /** Resolve a PointRef at frame f; returns world point and, when it follows a part, [obj, part, local]. */
  const point = (ref: PointRef, f: number, where: string, depth = 0): { p: V3; follow?: [string, string, V3] } | null => {
    if (Array.isArray(ref)) {
      try {
        return { p: vec(ref as Vec3) };
      } catch {
        err("unknown_point", `点 ${JSON.stringify(ref)} 不是数字坐标`, where);
        return null;
      }
    }
    const s = String(ref);
    if (marks[s] !== undefined && depth < 8) return point(marks[s] as PointRef, f, where, depth + 1);
    const [o, k, ...rest] = s.split(".");
    const meta = metaOf.get(o);
    if (!meta || rest.length) {
      err("unknown_point", `点 ${s} 不存在（不是标记点、物体或物体.锚点）`, where);
      return null;
    }
    const om = objMat[o][Math.min(f, objMat[o].length - 1)];
    if (k === undefined) return { p: apply(om, [0, 0, 0]) };
    const world = worldCache.get(o)![f];
    const ap = meta.anchor_parts?.[k];
    if (ap && world[ap.part]) return { p: apply(world[ap.part], ap.at), follow: [o, ap.part, ap.at] };
    if (meta.anchors?.[k]) return { p: apply(om, meta.anchors[k]) };
    // contract section 15.10: points name anchors only, never part ids
    err("unknown_point", `物体 ${o} 没有锚点 ${k}${meta.rig.rest[k] ? "（这是部件 id；点只能写锚点名）" : ""}`, where);
    return null;
  };

  // ---------- camera ----------
  const keys = (spec.camera?.keys ?? []).map((k, i) => ({ k, i, f: anchor(k.at, `camera.keys[${i}].at`, 0) }));
  keys.sort((a, b) => a.f - b.f || a.i - b.i);
  if (!keys.length) err("unknown_point", "镜头没有相机关键帧", "camera.keys");
  const pathKind = spec.camera?.path ?? "catmull";
  const failed = new Set<string>();
  const kpt = (ref: PointRef, f: number, where: string): V3 => {
    if (failed.has(where)) return [0, 0, 0];
    const r = point(ref, f, where);
    if (!r) failed.add(where);
    return r ? r.p : [0, 0, 0];
  };
  const keyPos = (key: CameraKey, i: number, f: number): V3 => {
    if (key.orbit) {
      const c = kpt(key.orbit.around, f, `camera.keys[${i}].orbit.around`);
      const az = key.orbit.az * D2R;
      const el = key.orbit.el * D2R;
      return [c[0] + key.orbit.r * Math.cos(el) * Math.sin(az), c[1] + key.orbit.r * Math.sin(el), c[2] + key.orbit.r * Math.cos(el) * Math.cos(az)];
    }
    if (key.pos === undefined) {
      if (!failed.has(`camera.keys[${i}]`)) err("unknown_point", "相机关键帧要写 pos 或 orbit", `camera.keys[${i}]`);
      failed.add(`camera.keys[${i}]`);
      return [0, 0, 1];
    }
    return kpt(key.pos, f, `camera.keys[${i}].pos`);
  };
  const camera: number[][] = [];
  const cuts = keys.filter((k, j) => j > 0 && k.k.cut && k.f > 0).map((k) => k.f);
  for (let f = 0; f < N; f++) {
    let j = -1;
    for (let q = 0; q < keys.length; q++) if (keys[q].f <= f) j = q;
    let pos: V3;
    let look: V3;
    let lens: number;
    let roll: number;
    const val = (q: number) => keys[q].k;
    if (!keys.length) {
      camera.push([0, 1, 3, 0, 0, 0, lensToVfov(35), 0]);
      continue;
    }
    const at = (q: number) => ({
      pos: keyPos(val(q), keys[q].i, f),
      look: kpt(val(q).look, f, `camera.keys[${keys[q].i}].look`),
      lens: val(q).lens_mm ?? 35,
      roll: val(q).roll ?? 0,
    });
    if (j < 0 || j === keys.length - 1 || val(j + 1).cut) {
      const k = at(Math.max(0, j));
      ({ pos, look, lens, roll } = k);
    } else {
      const A = at(j);
      const B = at(j + 1);
      const span = keys[j + 1].f - keys[j].f;
      const e = ease(val(j + 1).ease ?? "inOut", span > 0 ? (f - keys[j].f) / span : 1);
      const oa = val(j).orbit;
      const ob = val(j + 1).orbit;
      if (oa && ob && JSON.stringify(oa.around) === JSON.stringify(ob.around)) {
        // orbit to orbit around the same point: interpolate r / az / el (a true arc)
        const c = kpt(oa.around, f, `camera.keys[${keys[j].i}].orbit.around`);
        const r = lerp(oa.r, ob.r, e);
        const az = lerp(oa.az, ob.az, e) * D2R;
        const el = lerp(oa.el, ob.el, e) * D2R;
        pos = [c[0] + r * Math.cos(el) * Math.sin(az), c[1] + r * Math.sin(el), c[2] + r * Math.cos(el) * Math.cos(az)];
      } else if (pathKind === "linear") {
        pos = lerp3(A.pos, B.pos, e);
      } else {
        const p0 = j > 0 && !val(j).cut ? keyPos(val(j - 1), keys[j - 1].i, f) : null;
        const p3 = j + 2 < keys.length && !val(j + 2).cut ? keyPos(val(j + 2), keys[j + 2].i, f) : null;
        pos = catmull(p0, A.pos, B.pos, p3, e);
      }
      look = lerp3(A.look, B.look, e);
      lens = lerp(A.lens, B.lens, e);
      roll = lerp(A.roll, B.roll, e);
    }
    camera.push(r6a([...pos, ...look, lensToVfov(lens), roll]));
  }

  // ---------- overlays ----------
  for (const l of labelRefs) {
    const ov = overlays[l.o] as Extract<ResolvedShot["overlays"][number], { kind: "label" }> & { track?: number[][] };
    const first = point(l.ref, ov.from, `${l.where}.attach`);
    if (!first) continue;
    ov.point = r6a(first.p) as V3;
    if (first.follow) {
      ov.follow = `${first.follow[0]}.${first.follow[1]}`;
      const tr: number[][] = [];
      for (let f = ov.from; f <= ov.to; f++) tr.push(r6a(apply(worldCache.get(first.follow[0])![f][first.follow[1]], first.follow[2])));
      ov.track = compact(tr);
    } else ov.track = [ov.point];
    subjects.push({ where: l.where, from: ov.from, to: ov.to, point: ov.track });
  }
  for (const d of dimRefs) {
    const ov = overlays[d.o] as Extract<ResolvedShot["overlays"][number], { kind: "dimension" }>;
    const a = point(d.from, ov.from, `${d.where}.from`);
    const b = point(d.to, ov.from, `${d.where}.to`);
    if (a) ov.a = r6a(a.p) as V3;
    if (b) ov.b = r6a(b.p) as V3;
    if (a && b) subjects.push({ where: d.where, from: ov.from, to: ov.to, point: [ov.a.map((x, k) => (x + ov.b[k]) / 2)] });
  }

  const st = spec.style ?? {};
  const g = set?.ground ?? { kind: "grid" };
  const shot: ResolvedShot & { subjects: Subject[] } = {
    version: 1,
    id: spec.id,
    fps,
    frames: N,
    width: inp.width,
    height: inp.height,
    camera,
    cuts,
    objects: rObjects,
    overlays,
    sections,
    set: {
      background: set?.background ?? "#e9e6e1",
      ground: { kind: g.kind ?? "grid", size: g.size ?? 4, color: g.color ?? "#dedad3" },
      lights: set?.lights ?? "studio",
    },
    style: {
      look: st.look ?? "clay",
      shadows: st.shadows ?? true,
      outline: st.outline ?? false,
      fade_in: Math.round((st.fade_in ?? 0) * fps),
      fade_out: Math.round((st.fade_out ?? 0) * fps),
    },
    anchors,
    subjects,
  };
  return { shot, issues };
}
