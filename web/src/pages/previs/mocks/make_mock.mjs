// Dev-only: build the mock four_stroke GLB + ModelMeta that the previs UI mocks use.
// shortcut: a tiny subset of part A's builder (box / cylinder / tube / group, no arrays,
// no bevels); replace the fixture with A's real `build-asset` output once it lands.
//
// Run from web/:  node --experimental-strip-types src/pages/previs/mocks/make_mock.mjs
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import * as THREE from 'three'
import { GLTFExporter } from 'three/examples/jsm/exporters/GLTFExporter.js'
import { evaluate } from '../../../../../server/app/previs/js/core/expr.ts'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '../../../../..')
const plan = JSON.parse(fs.readFileSync(path.join(root, 'server/app/previs/examples/four_stroke/plan.json'), 'utf8'))
const spec = plan.assets.find((a) => a.id === 'four_stroke').spec

// GLTFExporter (binary) reads Blobs with FileReader, which Node does not have.
globalThis.FileReader = class {
  readAsArrayBuffer(b) { b.arrayBuffer().then((r) => { this.result = r; this.onloadend?.() }) }
  readAsDataURL(b) { b.arrayBuffer().then((r) => { this.result = `data:${b.type};base64,${Buffer.from(r).toString('base64')}`; this.onloadend?.() }) }
}

const PALETTE = { clay: '#c9c4bb', accent1: '#d98c5f', accent2: '#6f9fd8', accent3: '#8cc084', accent4: '#d8c46f', glass: '#a9b8c6' }
const params = Object.fromEntries(Object.entries(spec.params).map(([k, p]) => [k, p.value]))
const ev = (x) => evaluate(x, params)
const vec = (v) => (v || [0, 0, 0]).map(ev)
const AX = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] }

function geometry(p) {
  let g
  if (p.shape === 'box') { const [w, h, d] = vec(p.size); g = new THREE.BoxGeometry(w, h, d) }
  else if (p.shape === 'cylinder') g = new THREE.CylinderGeometry(ev(p.r), ev(p.r), ev(p.h), p.segments || 40)
  else if (p.shape === 'tube') {
    const ro = ev(p.r_outer), ri = ev(p.r_inner), h = ev(p.h)
    const pts = [[ri, -h / 2], [ro, -h / 2], [ro, h / 2], [ri, h / 2], [ri, -h / 2]].map(([x, y]) => new THREE.Vector2(x, y))
    g = new THREE.LatheGeometry(pts, p.segments || 48)
  } else if (p.shape === 'group') return null
  else throw new Error(`mock builder: shape ${p.shape} not supported`)
  if (p.axis === 'z') g.rotateX(Math.PI / 2)
  if (p.axis === 'x') g.rotateZ(Math.PI / 2)
  return g
}

const scene = new THREE.Scene()
const nodes = {}
const rest = {}
const joints = {}
let tris = 0
for (const p of spec.parts) {
  const g = geometry(p)
  let node
  if (g) {
    const slot = PALETTE[p.color] ? p.color : 'clay'
    const m = new THREE.MeshStandardMaterial({ color: PALETTE[p.color] || p.color || PALETTE.clay, roughness: 0.9 })
    if (p.opacity != null && p.opacity < 1) { m.transparent = true; m.opacity = p.opacity }
    m.name = slot
    m.userData = { evp_slot: slot }
    node = new THREE.Mesh(g, m)
    tris += g.index ? g.index.count / 3 : g.attributes.position.count / 3
  } else node = new THREE.Group()
  node.name = p.id
  const at = vec(p.at)
  const rot = vec(p.rot_deg).map((d) => (d * Math.PI) / 180)
  node.position.set(...at)
  node.rotation.set(rot[0], rot[1], rot[2], 'XYZ')
  rest[p.id] = { at, rot }
  if (p.joint) joints[p.id] = { ...p.joint, axis: Array.isArray(p.joint.axis) ? p.joint.axis : AX[p.joint.axis] }
  ;(p.parent ? nodes[p.parent] : scene).add(node)
  nodes[p.id] = node
}
scene.updateMatrixWorld(true)

const box3 = (o) => { const b = new THREE.Box3().setFromObject(o); return { min: b.min.toArray(), max: b.max.toArray() } }
const bbox = box3(scene)
const r4 = (x) => Math.round(x * 1e4) / 1e4
const anchors = {}
const anchorParts = {}
for (const [k, a] of Object.entries(spec.anchors || {})) {
  if (typeof a === 'string') anchors[k] = nodes[a].getWorldPosition(new THREE.Vector3()).toArray()
  else if (Array.isArray(a)) anchors[k] = vec(a)
  else {
    const at = vec(a.at)
    anchors[k] = nodes[a.part].localToWorld(new THREE.Vector3(...at)).toArray()
    anchorParts[k] = { part: a.part, at }
  }
}

const meta = {
  kind: 'model', format: 'glb', units: 'm',
  bbox, size: bbox.max.map((x, i) => r4(x - bbox.min[i])), tris,
  parts: spec.parts.map((p) => ({ id: p.id, parent: p.parent || null, bbox: box3(nodes[p.id]) })),
  rig: { dofs: spec.dofs || {}, drivers: spec.drivers || {}, joints, params, rest },
  anchors, anchor_parts: anchorParts,
  source: { type: 'parametric', spec },
}

const glb = await new GLTFExporter().parseAsync(scene, { binary: true })
fs.writeFileSync(path.join(here, 'four_stroke.glb'), Buffer.from(glb))
fs.writeFileSync(path.join(here, 'four_stroke.meta.json'), JSON.stringify(meta, null, 1) + '\n')
console.log(`four_stroke.glb ${glb.byteLength} bytes, ${tris} tris, size ${meta.size.join(' x ')} m`)
