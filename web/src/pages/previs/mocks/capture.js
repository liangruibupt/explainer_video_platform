// Dev-only capture page, see capture.html. Query: w, h, cam=px,py,pz,lx,ly,lz,vfov, pose (JSON).
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { partTransforms } from '@previs-core/rig.ts'
import meta from './four_stroke.meta.json'

const PALETTE = { clay: '#c9c4bb', accent1: '#d98c5f', accent2: '#6f9fd8', accent3: '#8cc084', accent4: '#d8c46f', glass: '#a9b8c6' }
const q = new URLSearchParams(location.search)
const w = +q.get('w') || 640
const h = +q.get('h') || 360
const [px, py, pz, lx, ly, lz, vfov] = (q.get('cam') || '0.9,0.55,1.1,0,0.28,0,30').split(',').map(Number)
const pose = JSON.parse(q.get('pose') || '{}')

const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true })
renderer.setSize(w, h)
renderer.shadowMap.enabled = true
document.body.appendChild(renderer.domElement)
const scene = new THREE.Scene()
scene.background = new THREE.Color('#e9e6e1')
scene.add(new THREE.HemisphereLight('#ffffff', '#b8b0a4', 1.1))
const key = new THREE.DirectionalLight('#ffffff', 2.2)
key.position.set(0.8, 1.6, 1.0)
key.castShadow = true
key.shadow.mapSize.set(2048, 2048)
Object.assign(key.shadow.camera, { left: -0.8, right: 0.8, top: 0.8, bottom: -0.8 })
scene.add(key)
const grid = new THREE.GridHelper(4, 40, '#b9b0a5', '#d8d2c9')
scene.add(grid)
const ground = new THREE.Mesh(new THREE.PlaneGeometry(4, 4), new THREE.ShadowMaterial({ opacity: 0.18 }))
ground.rotation.x = -Math.PI / 2
ground.receiveShadow = true
scene.add(ground)
const camera = new THREE.PerspectiveCamera(vfov, w / h, 0.01, 100)
camera.position.set(px, py, pz)
camera.lookAt(lx, ly, lz)

new GLTFLoader().load(new URL('./four_stroke.glb', import.meta.url).href, (gltf) => {
  const nodes = {}
  gltf.scene.traverse((o) => {
    nodes[o.name] = o
    if (o.isMesh) {
      const slot = o.material.userData?.evp_slot || 'clay'
      const glass = slot === 'glass'
      o.material = new THREE.MeshStandardMaterial({ color: PALETTE[slot], roughness: 0.85, transparent: glass, opacity: glass ? 0.35 : 1, depthWrite: !glass })
      o.castShadow = !glass
      o.receiveShadow = true
    }
  })
  for (const [id, t] of Object.entries(partTransforms(meta.rig, pose))) {
    nodes[id]?.position.set(t[0], t[1], t[2])
    nodes[id]?.rotation.set(t[3], t[4], t[5], 'XYZ')
  }
  scene.add(gltf.scene)
  renderer.render(scene, camera)
  window.ready = true
})
