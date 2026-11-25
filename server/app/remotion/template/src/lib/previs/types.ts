// The ResolvedShot the renderer reads (docs/PREVIS_CONTRACT.md section 8). Mirrors
// server/app/previs/js/core/types.ts ResolvedShot plus the extra fields the shot compiler
// (server/app/previs/js/shot/compile.ts) writes. The renderer does no time or pose maths:
// it only reads row `frame` of each array (an array of one entry is constant).
export type V3 = [number, number, number];

export interface RHighlight {
  from: number;
  to: number;
  color: string;
  part?: string; // one part (and its children) instead of the whole object
}

export interface RObject {
  id: string;
  asset: string;
  glb: string; // relative to public/, e.g. "previs/asset_x.glb"
  color?: string; // replaces the clay slot of this object
  scale?: number;
  xform: number[][]; // [px,py,pz, rx,ry,rz, visible]
  parts: Record<string, number[][]>; // part-local [px,py,pz, rx,ry,rz]
  highlight?: RHighlight[];
  explode?: number[];
  explode_dirs?: Record<string, V3>; // object-space offset per part for factor 1
  part_visible?: Record<string, number[]>; // show / hide of single parts (and their children)
}

export type ROverlay =
  | { kind: "label"; from: number; to: number; text: string; point: V3; side: "left" | "right" | "top"; follow?: string; track?: number[][] }
  | { kind: "dimension"; from: number; to: number; a: V3; b: V3; text: string };

export interface RSection {
  from: number;
  to: number;
  targets: string[]; // "obj" or "obj.part"
  normal: V3;
  offset: number; // kept side: dot(normal, p) >= offset
}

export interface ResolvedShot {
  version: 1;
  id: string;
  fps: number;
  frames: number;
  width: number;
  height: number;
  camera: number[][]; // [px,py,pz, lx,ly,lz, vfov_deg, roll_deg]
  cuts: number[];
  objects: RObject[];
  overlays: ROverlay[];
  sections: RSection[];
  set: { background: string; ground: { kind: string; size: number; color: string }; lights: string };
  style: { look: string; shadows: boolean; outline: boolean; fade_in: number; fade_out: number };
  anchors: unknown[];
}

export const row = <T,>(rows: T[], f: number): T => rows[Math.max(0, Math.min(f, rows.length - 1))];
