// 3D previs data types: the JSON files of docs/PREVIS_CONTRACT.md as TypeScript.
// FIXED by P0. Every part imports from here; change only through the integrator.
//
// Units and axes (contract section 3): metres, seconds when authoring, frames once resolved
// (fps 30). Right-handed, +Y up, ground plane y = 0, an asset's front faces +Z (glTF).
// Angles in authoring files are DEGREES; resolved files use radians.

/** A number, or an expression over params / DOFs (core/expr.ts), e.g. "bore/2 + 0.01". */
export type Num = number | string;
export type Vec3 = [Num, Num, Num];
export type V3 = [number, number, number];

// ---------- 4. Parametric asset spec (asset.json) ----------

export interface ParamDef {
  value: number;
  min?: number;
  max?: number;
  label?: string; // Chinese UI label, e.g. "缸径"
  unit?: "m" | "deg" | "";
}

export type ShapeKind =
  | "box" // size [w,h,d]
  | "cylinder" // r, h, (r_top), axis "y" default, segments
  | "tube" // r_outer, r_inner, h (hollow cylinder)
  | "cone" // r, h
  | "sphere" // r
  | "capsule" // r, h (h = straight part)
  | "torus" // r (ring), tube
  | "extrude" // profile [[x,y]...] closed polyline in the XY plane, depth along +Z, (bevel)
  | "lathe" // profile [[r,y]...] revolved around +Y, (angle_deg default 360)
  | "group"; // no geometry, only children

export interface JointDef {
  type: "hinge" | "slide"; // hinge = rotation about axis, slide = translation along axis
  axis: "x" | "y" | "z" | V3; // in the parent's frame
  dof: string; // name of the degree of freedom that drives it (see AssetSpec.dofs)
  scale?: Num; // part value = scale * dof value (default 1), lets one DOF drive gears etc.
}

export interface PartSpec {
  id: string; // unique in the asset; becomes the glTF node name; [a-z][a-z0-9_]*
  shape: ShapeKind;
  parent?: string; // part id; default = asset root
  at?: Vec3; // position in the parent frame (rest pose)
  rot_deg?: Vec3; // XYZ Euler, degrees (rest pose)
  joint?: JointDef; // moves this part relative to its parent
  color?: string; // "#rrggbb" or a palette slot: "clay" | "accent1".."accent4" | "glass"
  opacity?: number; // 0..1, 1 default
  // shape parameters (Num so they can be expressions)
  size?: Vec3;
  r?: Num;
  r_top?: Num;
  r_outer?: Num;
  r_inner?: Num;
  h?: Num;
  tube?: Num;
  depth?: Num;
  bevel?: Num;
  angle_deg?: Num;
  axis?: "x" | "y" | "z"; // cylinder / tube / cone / capsule orientation, default "y"
  segments?: number;
  profile?: [Num, Num][];
  /** Repeat this part: linear (count, step) or radial (count, about axis through parent origin). */
  array?: { kind: "linear"; count: number; step: Vec3 } | { kind: "radial"; count: number; axis: "x" | "y" | "z"; angle_deg?: Num };
}

export interface AssetSpec {
  id: string; // [a-z][a-z0-9_]*, unique in a job/project
  name: string; // Chinese display name
  version: 1;
  units: "m";
  params: Record<string, ParamDef>;
  /** Degrees of freedom the shots may animate. value units: deg for hinge, m for slide. */
  dofs?: Record<string, { min: number; max: number; default: number; label?: string }>;
  /**
   * Derived DOFs: name -> expression over params and other DOFs, evaluated in order.
   * Keeps linkages consistent, e.g. piston height from crank angle:
   * "piston_y": "stroke/2*(1-cos(crank)) + ..." (trig functions take DEGREES).
   */
  drivers?: Record<string, string>;
  /** Declared overall size of the rest pose, metres (checked against the built bbox). */
  dims: { w: Num; h: Num; d: Num; tol?: number }; // tol default 0.02 (2 %)
  parts: PartSpec[];
  /** Named points shots can look at / attach labels to: "part_id" or [x,y,z] in asset space, or {part, at}. */
  anchors?: Record<string, string | Vec3 | { part: string; at: Vec3 }>;
  max_tris?: number; // default 200000
}

// ---------- Model asset meta (data/assets/<id>/meta.json for kind "model") ----------

export interface ModelMeta {
  kind: "model";
  format: "glb";
  units: "m";
  bbox: { min: V3; max: V3 }; // rest pose, asset space, metres
  size: V3; // bbox max - min
  tris: number;
  parts: { id: string; parent: string | null; bbox: { min: V3; max: V3 } }[]; // rest pose
  rig: {
    dofs: Record<string, { min: number; max: number; default: number; label?: string }>;
    drivers: Record<string, string>;
    joints: Record<string, JointDef>; // part id -> joint (axis normalised to a V3 by the builder)
    params: Record<string, number>; // resolved param values (drivers may use them)
    rest: Record<string, { at: V3; rot: V3 }>; // part id -> rest pose, rot in radians (XYZ)
  };
  anchors: Record<string, V3>; // asset space, rest pose; anchors on moving parts also in `anchor_parts`
  anchor_parts: Record<string, { part: string; at: V3 }>; // anchors that follow a part (part-local)
  source:
    | { type: "parametric"; spec: AssetSpec }
    | { type: "image3d"; model: string; image_asset?: string; prompt?: string; seed?: number; target: TargetSize }
    | { type: "upload"; filename: string; target?: TargetSize };
}

/** How a GLB without real units is scaled: one axis (or the longest side) gets this length. */
export interface TargetSize {
  axis: "w" | "h" | "d" | "longest";
  metres: number;
}

// ---------- 6. Set (set.json) ----------

export interface SetObject {
  id: string; // [a-z][a-z0-9_]*, unique in the set
  asset: string; // asset id in the library ("asset_..."), or "@<spec id>" for a spec in the same plan
  at?: Vec3; // metres, set space
  rot_deg?: Vec3;
  scale?: number; // uniform only; default 1; anything else than 1 is a warning (breaks real size)
  color?: string; // overrides every clay-coloured part of this object
  pose?: Record<string, number>; // rest DOF values for this object
}

export interface SetSpec {
  id: string;
  name?: string;
  units: "m";
  background?: string; // default "#e9e6e1"
  ground?: { kind: "grid" | "plain" | "none"; size?: number; color?: string };
  lights?: "studio" | "top" | "dramatic";
  objects: SetObject[];
  /** Named points: [x,y,z] in set space, or "object.anchor" / "object" (object origin). */
  marks?: Record<string, Vec3 | string>;
}

// ---------- 7. Shot (shot.json) ----------

/** When something happens. Exactly one of t / frame / word / mark. */
export interface TimeAnchor {
  t?: number; // seconds from the shot start
  frame?: number; // frames from the shot start
  word?: string; // text in THIS scene's narration, e.g. "做功"
  occurrence?: number; // 1-based, default 1
  edge?: "start" | "end"; // default "start"
  mark?: "start" | "end"; // the shot's first / last frame
  offset?: number; // seconds added after resolving, may be negative
}

/** A point: [x,y,z] set space, a set mark name, "object", "object.anchor". */
export type PointRef = Vec3 | string;

export interface CameraKey {
  at: TimeAnchor;
  pos?: PointRef; // camera position
  orbit?: { around: PointRef; r: number; az: number; el: number }; // degrees; alternative to pos
  look: PointRef; // look-at target
  lens_mm?: number; // 35 mm full-frame equivalent, default 35
  roll?: number; // degrees
  ease?: Ease; // easing INTO this key from the previous one
  cut?: boolean; // hard cut to this key (no interpolation from the previous key)
}

export type Ease = "linear" | "in" | "out" | "inOut" | "inOutCubic" | "hold";

export type Action =
  | { op: "pose"; target: string; dof: string; to: number; at: TimeAnchor; until?: TimeAnchor; ease?: Ease }
  | { op: "spin"; target: string; dof: string; deg_per_s: number; at: TimeAnchor; until?: TimeAnchor }
  | { op: "move"; target: string; to: Vec3; rot_deg?: Vec3; at: TimeAnchor; until?: TimeAnchor; ease?: Ease }
  | { op: "show" | "hide"; target: string; at: TimeAnchor; fade?: number }
  | { op: "highlight"; target: string; color?: string; at: TimeAnchor; until?: TimeAnchor }
  | { op: "label"; text: string; attach: PointRef; at: TimeAnchor; until?: TimeAnchor; side?: "left" | "right" | "top" }
  | { op: "dimension"; from: PointRef; to: PointRef; text?: string; at: TimeAnchor; until?: TimeAnchor }
  | { op: "section"; targets: string[]; normal: V3; offset: number; at: TimeAnchor; until?: TimeAnchor }
  | { op: "explode"; target: string; factor: number; at: TimeAnchor; until?: TimeAnchor; ease?: Ease };

export interface ShotSpec {
  id: string; // = storyboard scene id (one shot per scene in v1), e.g. "s03"
  set: string; // set id
  camera: { keys: CameraKey[]; path?: "catmull" | "linear" };
  actions?: Action[];
  style?: { look?: "clay" | "flat" | "wire"; shadows?: boolean; outline?: boolean; fade_in?: number; fade_out?: number };
}

// ---------- Plan (what the agent writes for one job) ----------

export interface PrevisPlan {
  version: 1;
  assets: (
    | { id: string; kind: "parametric"; spec: AssetSpec }
    | { id: string; kind: "image3d"; prompt: string; target: TargetSize; image_asset?: string }
    | { id: string; kind: "library"; asset: string }
  )[];
  sets: SetSpec[];
  shots: ShotSpec[];
}

// ---------- words.json (captions.py) ----------

export interface WordEntry {
  scene: string; // "s03"
  text: string; // one script character
  start: number; // seconds, FILM time
  end: number;
}

// ---------- Resolved shot (compile output, renderer input) ----------

export interface ResolvedShot {
  version: 1;
  id: string;
  fps: number;
  frames: number; // shot length
  width: number;
  height: number;
  /** Per frame: [px,py,pz, lx,ly,lz, vfov_deg, roll_deg]. Length = frames. */
  camera: number[][];
  cuts: number[]; // frames where a hard cut happens
  objects: {
    id: string;
    asset: string; // asset id
    glb: string; // path relative to the Remotion public dir, e.g. "previs/asset_x.glb"
    color?: string;
    /** Per frame object transform [px,py,pz, rx,ry,rz (rad), visible 0..1]; one entry = constant. */
    xform: number[][];
    /** Per frame part-local transforms from the rig: part id -> per-frame [px,py,pz,rx,ry,rz]; one entry = constant. */
    parts: Record<string, number[][]>;
    highlight?: { from: number; to: number; color: string }[];
    explode?: number[]; // per frame factor, or one entry
  }[];
  overlays: (
    | { kind: "label"; from: number; to: number; text: string; point: V3; side: "left" | "right" | "top"; follow?: string }
    | { kind: "dimension"; from: number; to: number; a: V3; b: V3; text: string }
  )[];
  sections: { from: number; to: number; targets: string[]; normal: V3; offset: number }[];
  set: { background: string; ground: { kind: string; size: number; color: string }; lights: string };
  style: { look: string; shadows: boolean; outline: boolean; fade_in: number; fade_out: number };
  /** Every anchor as resolved, for the review page and the checks. */
  anchors: { where: string; anchor: TimeAnchor; frame: number; word_time?: number }[];
}

// ---------- Check report ----------

export interface CheckIssue {
  code: string; // see contract section 8
  severity: "error" | "warn";
  message: string; // Chinese, one sentence
  frame?: number;
  where?: string; // "camera.keys[2]", "actions[0]", "parts.piston"
}
