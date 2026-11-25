// GLB 2.0 binary I/O with @gltf-transform/core (plain Node; three's GLTFExporter needs a browser).
// Writing (contract 4.4): one node per part named by part id, node TRS = rest pose, one mesh per
// part with geometry, PBR material with the colour slot in extras.evp_slot.
import { Document, NodeIO, type Node as GNode, Primitive } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { MeshoptDecoder } from "meshoptimizer";
import * as THREE from "three";
import type { Model } from "./model";

// Contract section 9 palette (sRGB); glTF baseColorFactor is linear.
export const PALETTE: Record<string, string> = {
  clay: "#c9c4bb",
  accent1: "#d98c5f",
  accent2: "#6f9fd8",
  accent3: "#8cc084",
  accent4: "#d8c46f",
  glass: "#a9b8c6",
};

function linear(hex: string): [number, number, number] {
  const c = new THREE.Color(hex); // three converts sRGB hex to its linear working space
  return [c.r, c.g, c.b];
}

export async function io(): Promise<NodeIO> {
  await MeshoptDecoder.ready;
  return new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ "meshopt.decoder": MeshoptDecoder });
}

export async function writeGlb(m: Model, name: string): Promise<Uint8Array> {
  const doc = new Document();
  doc.getRoot().getAsset().generator = "evp-previs asset builder";
  const buf = doc.createBuffer();
  const scene = doc.createScene(name);
  const mats = new Map<string, ReturnType<Document["createMaterial"]>>();
  const material = (slot: string, opacity: number) => {
    const key = `${slot}|${opacity}`;
    let mat = mats.get(key);
    if (!mat) {
      mat = doc
        .createMaterial(slot)
        .setBaseColorFactor([...linear(PALETTE[slot] ?? slot), opacity])
        .setRoughnessFactor(0.9)
        .setMetallicFactor(0)
        .setAlphaMode(opacity < 1 ? "BLEND" : "OPAQUE")
        .setDoubleSided(opacity < 1)
        .setExtras({ evp_slot: slot });
      mats.set(key, mat);
    }
    return mat;
  };
  const nodes = new Map<string, GNode>();
  for (const p of m.parts) {
    const r = m.rig.rest[p.id];
    const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(r.rot[0], r.rot[1], r.rot[2], "XYZ"));
    const node = doc
      .createNode(p.id)
      .setTranslation([r.at[0], r.at[1], r.at[2]])
      .setRotation([q.x, q.y, q.z, q.w]);
    if (p.geom) {
      const g = p.geom;
      const pos = doc.createAccessor().setType("VEC3").setArray(new Float32Array(g.attributes.position.array)).setBuffer(buf);
      const nrm = doc.createAccessor().setType("VEC3").setArray(new Float32Array(g.attributes.normal.array)).setBuffer(buf);
      const count = g.attributes.position.count;
      const idx = g.index!.array;
      const ia = count > 65535 ? new Uint32Array(idx) : new Uint16Array(idx);
      const ind = doc.createAccessor().setType("SCALAR").setArray(ia).setBuffer(buf);
      const prim = doc
        .createPrimitive()
        .setMode(Primitive.Mode.TRIANGLES)
        .setAttribute("POSITION", pos)
        .setAttribute("NORMAL", nrm)
        .setIndices(ind)
        .setMaterial(material(p.slot, p.opacity));
      node.setMesh(doc.createMesh(p.id).addPrimitive(prim));
    }
    nodes.set(p.id, node);
  }
  for (const p of m.parts) {
    const n = nodes.get(p.id)!;
    if (p.parent) nodes.get(p.parent)!.addChild(n);
    else scene.addChild(n);
  }
  doc.getRoot().setDefaultScene(scene);
  return (await io()).writeBinary(doc);
}

export interface GlbMesh {
  node: string; // node name
  parentNode: string | null;
  positions: Float32Array; // node-local
  indices: Uint32Array;
  world: THREE.Matrix4; // node world matrix as stored in the file
}

/** Every triangle primitive of the default scene, in its node's frame. Lines/points are skipped. */
export async function readGlb(bytes: Uint8Array): Promise<{ meshes: GlbMesh[]; nodeNames: string[]; nodes: { name: string; parent: string | null; world: THREE.Matrix4 }[] }> {
  const doc = await (await io()).readBinary(bytes);
  const root = doc.getRoot();
  const scene = root.getDefaultScene() ?? root.listScenes()[0];
  if (!scene) throw new Error("GLB 里没有场景");
  const meshes: GlbMesh[] = [];
  const nodes: { name: string; parent: string | null; world: THREE.Matrix4 }[] = [];
  const walk = (n: GNode, parent: GNode | null) => {
    const world = new THREE.Matrix4().fromArray(n.getWorldMatrix() as unknown as number[]);
    nodes.push({ name: n.getName(), parent: parent ? parent.getName() : null, world });
    const mesh = n.getMesh();
    for (const prim of mesh ? mesh.listPrimitives() : []) {
      const mode = prim.getMode();
      if (mode !== Primitive.Mode.TRIANGLES && mode !== Primitive.Mode.TRIANGLE_STRIP && mode !== Primitive.Mode.TRIANGLE_FAN) continue;
      const pa = prim.getAttribute("POSITION");
      if (!pa) continue;
      const cnt = pa.getCount();
      const positions = new Float32Array(cnt * 3);
      const el: number[] = [];
      for (let i = 0; i < cnt; i++) {
        pa.getElement(i, el); // denormalises quantized attributes
        positions.set([el[0], el[1], el[2]], i * 3);
      }
      const ia = prim.getIndices();
      let seq: Uint32Array = ia ? Uint32Array.from(ia.getArray()!) : Uint32Array.from({ length: cnt }, (_, i) => i);
      if (mode !== Primitive.Mode.TRIANGLES) {
        const t: number[] = [];
        for (let i = 2; i < seq.length; i++) {
          if (mode === Primitive.Mode.TRIANGLE_STRIP) t.push(...(i % 2 ? [seq[i - 1], seq[i - 2], seq[i]] : [seq[i - 2], seq[i - 1], seq[i]]));
          else t.push(seq[0], seq[i - 1], seq[i]);
        }
        seq = Uint32Array.from(t);
      }
      meshes.push({ node: n.getName(), parentNode: parent ? parent.getName() : null, positions, indices: seq, world });
    }
    for (const c of n.listChildren()) walk(c, n);
  };
  for (const n of scene.listChildren()) walk(n, null);
  return { meshes, nodeNames: nodes.map((n) => n.name), nodes };
}
