// One set object: the GLB with clay materials, part transforms read from the ResolvedShot for
// the current frame, highlight, explode, visibility and clipping-plane sections with solid
// stencil caps (one cap per cut mesh, in the mesh's colour).
import React, { useLayoutEffect, useMemo } from "react";
import * as THREE from "three";
import type { GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import { capMaterial, clayMaterial, type Palette, stencilMaterials } from "./materials";
import { row, type RObject, type RSection } from "./types";

type Cut = { si: number; mesh: THREE.Mesh; stencil: THREE.Mesh[]; cap: THREE.Mesh };

/** A back-face shell a few mm larger than the mesh: the glow of a highlighted part. */
function halo(m: THREE.Mesh): THREE.Mesh {
  m.geometry.computeBoundingBox();
  const b = m.geometry.boundingBox!;
  const size = b.getSize(new THREE.Vector3());
  const c = b.getCenter(new THREE.Vector3());
  const d = Math.max(0.004, 0.08 * Math.max(size.x, size.y, size.z));
  const s = new THREE.Vector3(...[size.x, size.y, size.z].map((x) => (x > 1e-6 ? (x + 2 * d) / x : 1)));
  const h = new THREE.Mesh(m.geometry, new THREE.MeshBasicMaterial({ side: THREE.BackSide, transparent: true, opacity: 0.6, depthWrite: false }));
  h.scale.copy(s);
  h.position.copy(c).sub(c.clone().multiply(s));
  h.visible = false;
  h.castShadow = false;
  h.renderOrder = 5;
  return h;
}

interface Built {
  root: THREE.Group;
  caps: THREE.Group;
  nodes: Map<string, THREE.Object3D>;
  order: string[]; // part ids, parents first
  parentPart: Map<string, string | null>;
  meshes: THREE.Mesh[];
  partMeshes: (id: string) => THREE.Mesh[];
  planes: THREE.Plane[];
  cuts: Cut[];
  halos: Map<THREE.Mesh, THREE.Mesh>;
}

const Z = new THREE.Vector3(0, 0, 1);

function build(obj: RObject, gltf: GLTF, sections: RSection[], pal: Palette, look: string, shadows: boolean, orderBase: number): Built {
  const scene = gltf.scene.clone(true);
  const root = new THREE.Group();
  root.name = `obj:${obj.id}`;
  root.add(scene);
  const caps = new THREE.Group();
  const nodes = new Map<string, THREE.Object3D>();
  for (const id of Object.keys(obj.parts)) {
    const n = scene.getObjectByName(id);
    if (n) nodes.set(id, n);
  }
  const parentPart = new Map<string, string | null>();
  const depth = new Map<string, number>();
  for (const [id, n] of nodes) {
    let p = n.parent;
    let d = 0;
    while (p && !nodes.has(p.name) && p !== scene) p = p.parent;
    parentPart.set(id, p && nodes.has(p.name) ? p.name : null);
    for (let q: THREE.Object3D | null = n.parent; q && q !== scene; q = q.parent) d++;
    depth.set(id, d);
  }
  const order = [...nodes.keys()].sort((a, b) => depth.get(a)! - depth.get(b)!);
  const meshes: THREE.Mesh[] = [];
  scene.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.isMesh) return;
    const mat = clayMaterial(Array.isArray(m.material) ? m.material[0] : m.material, pal, look, obj.color);
    m.material = mat;
    m.castShadow = shadows && !mat.userData.glass;
    m.receiveShadow = shadows;
    m.geometry.computeBoundingSphere();
    meshes.push(m);
  });
  const halos = new Map<THREE.Mesh, THREE.Mesh>();
  if (obj.highlight?.length)
    for (const m of meshes) {
      const h = halo(m);
      m.add(h);
      halos.set(m, h);
    }
  const meshSet = new Set(meshes); // the part meshes only, not the stencil helpers added below
  const partMeshes = (id: string) => {
    const out: THREE.Mesh[] = [];
    nodes.get(id)?.traverse((o) => meshSet.has(o as THREE.Mesh) && out.push(o as THREE.Mesh));
    return out;
  };
  // sections that cut this object: one stencil pair + one cap per targeted mesh
  const planes = sections.map((s) => new THREE.Plane(new THREE.Vector3(...s.normal), -s.offset));
  const cuts: Cut[] = [];
  let k = 0;
  sections.forEach((s, si) => {
    const targets = new Set<THREE.Mesh>();
    for (const t of s.targets) {
      const [o, p] = t.split(".");
      if (o !== obj.id) continue;
      for (const m of p ? partMeshes(p) : meshes) targets.add(m);
    }
    for (const mesh of targets) {
      const ro = orderBase + 10 + 2 * k++;
      const stencil = stencilMaterials(planes[si]).map((sm) => {
        const x = new THREE.Mesh(mesh.geometry, sm);
        x.renderOrder = ro;
        x.castShadow = false;
        x.receiveShadow = false;
        mesh.add(x);
        return x;
      });
      const cm = capMaterial((mesh.material as THREE.MeshStandardMaterial).userData.color);
      // caps of nested parts are coplanar: later parts (usually the inner ones) win the depth test
      cm.polygonOffset = true;
      cm.polygonOffsetFactor = -1;
      cm.polygonOffsetUnits = -4 * k;
      const cap = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), cm);
      cap.renderOrder = ro + 1;
      cap.onAfterRender = (r) => r.clearStencil();
      cap.quaternion.setFromUnitVectors(Z, planes[si].normal.clone().negate());
      caps.add(cap);
      cuts.push({ si, mesh, stencil, cap });
    }
  });
  return { root, caps, nodes, order, parentPart, meshes, partMeshes, planes, cuts, halos };
}

export const Model: React.FC<{
  obj: RObject;
  gltf: GLTF;
  frame: number;
  sections: RSection[];
  pal: Palette;
  look: string;
  shadows: boolean;
  orderBase: number;
}> = ({ obj, gltf, frame, sections, pal, look, shadows, orderBase }) => {
  const b = useMemo(
    () => build(obj, gltf, sections, pal, look, shadows, orderBase),
    [obj, gltf, sections, pal, look, shadows, orderBase],
  );

  useLayoutEffect(() => {
    const x = row(obj.xform, frame);
    b.root.position.set(x[0], x[1], x[2]);
    b.root.rotation.set(x[3], x[4], x[5], "XYZ");
    b.root.scale.setScalar(obj.scale ?? 1);
    const vis = x[6] ?? 1;
    b.root.visible = vis > 0.001;
    // part-local transforms (core/rig partTransforms, computed by the compiler)
    for (const id of b.order) {
      const n = b.nodes.get(id)!;
      const r = row(obj.parts[id], frame);
      n.position.set(r[0], r[1], r[2]);
      n.rotation.set(r[3], r[4], r[5], "XYZ");
    }
    // explode: every part's asset-space offset = explode_dirs[part] * factor
    const f = obj.explode ? row(obj.explode, frame) : 0;
    if (f && obj.explode_dirs) {
      const q = new Map<string, THREE.Quaternion>(); // asset-space rotation of each part
      for (const id of b.order) {
        const n = b.nodes.get(id)!;
        const p = b.parentPart.get(id) ?? null;
        const qp = p ? q.get(p)! : new THREE.Quaternion();
        q.set(id, qp.clone().multiply(n.quaternion));
        const d = new THREE.Vector3(...(obj.explode_dirs[id] ?? [0, 0, 0])).multiplyScalar(f);
        if (p) d.sub(new THREE.Vector3(...(obj.explode_dirs[p] ?? [0, 0, 0])).multiplyScalar(f));
        n.position.add(d.applyQuaternion(qp.clone().invert()));
      }
    }
    // materials: opacity (show/hide fades), highlight
    const pv = new Map<THREE.Mesh, number>(); // single-part show / hide
    for (const [pid, v] of Object.entries(obj.part_visible ?? {}))
      for (const m of b.partMeshes(pid)) pv.set(m, Math.min(pv.get(m) ?? 1, row(v, frame)));
    const lit = new Map<THREE.Mesh, string>();
    for (const h of obj.highlight ?? [])
      if (frame >= h.from && frame <= h.to) for (const m of h.part ? b.partMeshes(h.part) : b.meshes) lit.set(m, h.color);
    for (const m of b.meshes) {
      const mat = m.material as THREE.MeshStandardMaterial;
      const base = mat.userData.baseOpacity as number;
      const mv = vis * (pv.get(m) ?? 1);
      m.visible = mv > 0.001;
      mat.opacity = base * mv;
      mat.transparent = mat.userData.glass || mv < 1;
      mat.depthWrite = !mat.transparent;
      const c = lit.get(m);
      mat.color.set(mat.userData.color);
      if (c) mat.color.set(c); // highlight: the part takes the highlight colour and glows
      mat.emissive.set(c ?? "#000000");
      mat.emissiveIntensity = c ? 0.45 : 0;
      const h = b.halos.get(m);
      if (h) {
        h.visible = !!c;
        if (c) (h.material as THREE.MeshBasicMaterial).color.set(c);
      }
      mat.clippingPlanes = [];
    }
    // sections
    const active = sections.map((s) => frame >= s.from && frame <= s.to);
    for (const c of b.cuts) {
      const mat = c.mesh.material as THREE.MeshStandardMaterial;
      if (active[c.si]) mat.clippingPlanes = [...(mat.clippingPlanes ?? []), b.planes[c.si]];
      for (const s of c.stencil) s.visible = active[c.si];
    }
    b.root.updateMatrixWorld(true);
    const sphere = new THREE.Sphere();
    for (const c of b.cuts) {
      const pl = b.planes[c.si];
      sphere.copy(c.mesh.geometry.boundingSphere!).applyMatrix4(c.mesh.matrixWorld);
      const dist = pl.distanceToPoint(sphere.center);
      const on = active[c.si] && b.root.visible && c.mesh.visible && Math.abs(dist) < sphere.radius;
      c.cap.visible = on;
      if (!on) continue;
      pl.projectPoint(sphere.center, c.cap.position);
      c.cap.scale.setScalar(sphere.radius * 2.2);
      (c.cap.material as THREE.MeshStandardMaterial).opacity = vis;
    }
  }, [b, obj, frame, sections]);

  return (
    <>
      <primitive object={b.root} />
      <primitive object={b.caps} />
    </>
  );
};
