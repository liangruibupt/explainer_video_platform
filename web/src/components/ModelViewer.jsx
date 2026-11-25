import { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { CSS2DRenderer, CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js'
import { dofValues, partTransforms } from '@previs-core/rig.ts'

// three.js viewer for a model asset (docs/PREVIS_CONTRACT.md §4.4, §12): orbit, zoom, grid
// ground, size labels from meta.size and one slider per meta.rig DOF. Posing goes through
// core/rig.ts, the same code the builder, the shot compiler and the renderer use.

// Clay palette, same slots and colours as the renderer (§9).
const PALETTE = { clay: '#c9c4bb', accent1: '#d98c5f', accent2: '#6f9fd8', accent3: '#8cc084', accent4: '#d8c46f', glass: '#a9b8c6' }
const BG = '#e9e6e1'
const STEPS = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50]

const CSS = `
.mv3 { display: flex; flex-direction: column; gap: 10px; }
.mv3-stage { position: relative; border-radius: 12px; overflow: hidden; border: 1px solid var(--line); background: ${BG}; }
.mv3-stage canvas { display: block; }
.mv3-msg { position: absolute; inset: 0; display: grid; place-items: center; color: var(--ink2); font-size: 13px; }
.mv3-lab { font: 12px var(--sans); color: var(--ink); background: rgba(251,248,243,.92); border: 1px solid var(--line2);
  border-radius: 6px; padding: 0 6px; white-space: nowrap; pointer-events: none; }
.mv3-hint { position: absolute; left: 10px; bottom: 8px; font-size: 11px; color: var(--ink3); pointer-events: none; }
.mv3-info { font-size: 12.5px; color: var(--ink2); display: flex; flex-wrap: wrap; gap: 4px 14px; }
.mv3-dofs { display: grid; gap: 8px; }
.mv3-dof { display: grid; grid-template-columns: minmax(84px, max-content) 1fr 92px auto; gap: 10px; align-items: center; font-size: 13px; }
.mv3-dof input[type=range] { width: 100%; accent-color: var(--accent); }
.mv3-dof input[type=number] { width: 100%; font: 12px var(--mono); border: 1px solid var(--line2); border-radius: 6px; padding: 2px 4px; background: #fff; }
.mv3-dof .btn { padding: 2px 8px; font-size: 12px; }
.mv3-drv { font: 11.5px var(--mono); color: var(--ink3); }
`

export const fmtLen = (m) => {
  const a = Math.abs(m)
  if (a < 0.1) return `${(m * 1000).toFixed(0)} mm`
  if (a < 1) return `${(m * 100).toFixed(1).replace(/\.0$/, '')} cm`
  return `${m.toFixed(2)} m`
}
const fmtVal = (x) => (Math.abs(x) >= 10 ? x.toFixed(0) : Math.abs(x) >= 1 ? x.toFixed(2) : x.toFixed(4))

function clayMaterial(src) {
  const slot = src?.userData?.evp_slot
  const color = PALETTE[slot] || (src?.color ? `#${src.color.getHexString()}` : PALETTE.clay)
  const glass = slot === 'glass' || (src && src.opacity < 1)
  return new THREE.MeshStandardMaterial({
    color, roughness: 0.85, metalness: 0,
    transparent: glass, opacity: glass ? Math.min(src?.opacity ?? 0.35, 0.45) : 1, depthWrite: !glass,
  })
}

function sizeLabel(text) {
  const el = document.createElement('div')
  el.className = 'mv3-lab'
  el.textContent = text
  return new CSS2DObject(el)
}

// Three dimension lines on the rest bounding box: width (x) and depth (z) on the ground,
// height (y) on the front-right edge.
function dimensionLines(bbox, size) {
  const g = new THREE.Group()
  const [x0, y0, z0] = bbox.min
  const [x1, y1, z1] = bbox.max
  const off = Math.max(...size) * 0.08
  const mat = new THREE.LineBasicMaterial({ color: '#8a7f74' })
  const seg = (a, b, text, at) => {
    g.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...a), new THREE.Vector3(...b)]), mat))
    const l = sizeLabel(text)
    l.position.set(...at)
    g.add(l)
  }
  const zf = z1 + off
  const xr = x1 + off
  seg([x0, 0.001, zf], [x1, 0.001, zf], `宽 ${fmtLen(size[0])}`, [(x0 + x1) / 2, 0.001, zf])
  seg([xr, 0.001, z0], [xr, 0.001, z1], `深 ${fmtLen(size[2])}`, [xr, 0.001, (z0 + z1) / 2])
  seg([xr, y0, zf], [xr, y1, zf], `高 ${fmtLen(size[1])}`, [xr, (y0 + y1) / 2, zf])
  return g
}

/**
 * Props: src (GLB url), meta (ModelMeta, i.e. the library record), height (px, default 420).
 * Optional controlled pose: pose + onPose; otherwise the viewer keeps its own.
 */
export default function ModelViewer({ src, meta, height = 420, pose: poseIn, onPose }) {
  const host = useRef(null)
  const three = useRef(null)
  const [err, setErr] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const rig = meta?.rig
  const dofs = useMemo(() => Object.entries(rig?.dofs || {}), [rig])
  const defaults = useMemo(() => Object.fromEntries(dofs.map(([k, d]) => [k, d.default])), [dofs])
  const [ownPose, setOwnPose] = useState(defaults)
  const pose = poseIn || ownPose
  const setPose = (p) => { if (onPose) onPose(p); else setOwnPose(p) }

  let drivers = {}
  try { drivers = rig ? dofValues(rig, pose) : {} } catch { /* shown as no readout */ }

  // Scene: built once per GLB.
  useEffect(() => {
    const el = host.current
    if (!el || !src) return undefined
    setErr(null); setLoaded(false)
    const w = el.clientWidth || 640
    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.setSize(w, height)
    renderer.shadowMap.enabled = true
    renderer.shadowMap.type = THREE.PCFSoftShadowMap
    el.prepend(renderer.domElement)
    const labels = new CSS2DRenderer()
    labels.setSize(w, height)
    Object.assign(labels.domElement.style, { position: 'absolute', inset: '0', pointerEvents: 'none' })
    el.appendChild(labels.domElement)

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(BG)
    scene.add(new THREE.HemisphereLight('#ffffff', '#b8b0a4', 1.1))
    const key = new THREE.DirectionalLight('#ffffff', 2.2)
    key.castShadow = true
    key.shadow.mapSize.set(2048, 2048)
    scene.add(key, key.target)
    const fill = new THREE.DirectionalLight('#ffffff', 0.6)
    scene.add(fill)

    const camera = new THREE.PerspectiveCamera(35, w / height, 0.01, 1000)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true

    const size = meta?.size || [1, 1, 1]
    const bbox = meta?.bbox || { min: [-0.5, 0, -0.5], max: [0.5, 1, 0.5] }
    const big = Math.max(...size, 0.01)
    const center = new THREE.Vector3(...bbox.min.map((v, i) => (v + bbox.max[i]) / 2))
    const cell = STEPS.find((s) => s >= big / 6) || 100
    const span = Math.ceil((big * 4) / cell) * cell
    const grid = new THREE.GridHelper(span, Math.round(span / cell), '#b9b0a5', '#d8d2c9')
    grid.position.set(center.x, 0, center.z)
    scene.add(grid)
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(span, span), new THREE.ShadowMaterial({ opacity: 0.18 }))
    ground.rotation.x = -Math.PI / 2
    ground.position.set(center.x, -0.0005, center.z)
    ground.receiveShadow = true
    scene.add(ground)
    if (meta?.size) scene.add(dimensionLines(bbox, size))

    key.position.set(center.x + big * 1.5, big * 3, center.z + big * 2)
    key.target.position.copy(center)
    const sc = key.shadow.camera
    sc.left = sc.bottom = -big * 1.5; sc.right = sc.top = big * 1.5; sc.near = 0.01; sc.far = big * 10
    fill.position.set(center.x - big * 2, big, center.z - big)

    const dist = big * 2.6
    const az = (35 * Math.PI) / 180, el2 = (22 * Math.PI) / 180
    camera.position.set(center.x + dist * Math.cos(el2) * Math.sin(az), center.y + dist * Math.sin(el2), center.z + dist * Math.cos(el2) * Math.cos(az))
    controls.target.copy(center)
    controls.minDistance = big * 0.3
    controls.maxDistance = big * 12
    controls.update()

    const nodes = {}
    const state = { renderer, labels, scene, camera, controls, nodes, model: null }
    three.current = state
    let alive = true
    new GLTFLoader().load(src, (gltf) => {
      if (!alive) return
      const model = gltf.scene
      model.traverse((o) => {
        if (o.name) nodes[o.name] = nodes[o.name] || o
        if (o.isMesh) {
          const old = o.material
          o.material = clayMaterial(old)
          old?.dispose?.()
          o.castShadow = !o.material.transparent
          o.receiveShadow = true
        }
      })
      scene.add(model)
      state.model = model
      setLoaded(true)
    }, undefined, (e) => { if (alive) setErr(`模型加载失败：${e?.message || e}`) })

    const loop = () => { controls.update(); renderer.render(scene, camera); labels.render(scene, camera) }
    renderer.setAnimationLoop(loop)
    const onResize = () => {
      const ww = el.clientWidth || w
      renderer.setSize(ww, height); labels.setSize(ww, height)
      camera.aspect = ww / height; camera.updateProjectionMatrix()
    }
    const ro = new ResizeObserver(onResize)
    ro.observe(el)
    return () => {
      alive = false
      ro.disconnect()
      renderer.setAnimationLoop(null)
      controls.dispose()
      scene.traverse((o) => { o.geometry?.dispose?.(); o.material?.dispose?.() })
      renderer.dispose()
      renderer.domElement.remove()
      labels.domElement.remove()
      three.current = null
    }
  }, [src, meta, height])

  // Pose: part-local transforms from core/rig.ts onto the GLB nodes (named by part id).
  useEffect(() => {
    const t = three.current
    if (!t || !loaded || !rig?.rest) return
    let xf
    try { xf = partTransforms(rig, pose) } catch (e) { setErr(`姿态计算失败：${e.message}`); return }
    for (const [id, [px, py, pz, rx, ry, rz]] of Object.entries(xf)) {
      const n = t.nodes[id]
      if (!n) continue
      n.position.set(px, py, pz)
      n.rotation.set(rx, ry, rz, 'XYZ')
    }
  }, [pose, loaded, rig])

  const size = meta?.size
  const driverNames = Object.keys(rig?.drivers || {})
  return (
    <div className="mv3">
      <style>{CSS}</style>
      <div className="mv3-stage" ref={host} style={{ height }} data-loaded={loaded ? '1' : '0'}>
        {!loaded && !err && <div className="mv3-msg">加载模型…</div>}
        {err && <div className="mv3-msg error">{err}</div>}
        {loaded && <div className="mv3-hint">拖动旋转 · 滚轮缩放 · 右键平移 · 格子 {fmtLen(STEPS.find((s) => s >= Math.max(...(size || [1])) / 6) || 100)}</div>}
      </div>
      {size && (
        <div className="mv3-info">
          <span>尺寸（宽×高×深）：{size.map(fmtLen).join(' × ')}</span>
          {meta.tris != null && <span>{meta.tris.toLocaleString()} 个三角面</span>}
          {meta.parts && <span>{meta.parts.length} 个部件</span>}
        </div>
      )}
      {dofs.length > 0 && (
        <div className="mv3-dofs">
          {dofs.map(([k, d]) => (
            <div className="mv3-dof" key={k}>
              <span title={k}>{d.label || k}</span>
              <input type="range" min={d.min} max={d.max} step={(d.max - d.min) / 720 || 0.001} value={pose[k] ?? d.default}
                     aria-label={d.label || k}
                     onChange={(e) => setPose({ ...pose, [k]: +e.target.value })} />
              <input type="number" min={d.min} max={d.max} value={+(pose[k] ?? d.default).toFixed(3)}
                     onChange={(e) => setPose({ ...pose, [k]: Math.min(d.max, Math.max(d.min, +e.target.value || 0)) })} />
              <button className="btn" onClick={() => setPose({ ...pose, [k]: d.default })}>复位</button>
            </div>
          ))}
          {driverNames.length > 0 && (
            <div className="mv3-drv">
              {driverNames.map((n) => `${n} = ${drivers[n] != null ? fmtVal(drivers[n]) : '—'}`).join(' · ')}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
