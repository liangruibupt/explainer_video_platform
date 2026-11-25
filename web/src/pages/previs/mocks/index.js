// Dev-only mock answers for the previs API (docs/PREVIS_CONTRACT.md §11), built from the
// four_stroke example. Loaded lazily by ../mock.js only when a real route returns 404.
import meta from './four_stroke.meta.json'
import plan from '../../../../../server/app/previs/examples/four_stroke/plan.json'
import scenes from '../../../../../server/app/previs/examples/four_stroke/scenes.json'
import durations from '../../../../../server/app/previs/examples/four_stroke/durations.json'

const u = (p) => new URL(p, import.meta.url).href
const GLB = u('./four_stroke.glb')
const SHOT_MEDIA = {
  s01: { stills: [0, 1, 2, 3, 4].map((n) => u(`./shots/s01_${n}.jpg`)), plan_png: u('./shots/s01.plan.svg'), clip_url: u('./shots/s01.mp4') },
  s02: { stills: [0, 1, 2, 3, 4].map((n) => u(`./shots/s02_${n}.jpg`)), plan_png: u('./shots/s02.plan.svg'), clip_url: u('./shots/s02.mp4') },
  s03: { stills: [0, 1, 2, 3, 4].map((n) => u(`./shots/s03_${n}.jpg`)), plan_png: u('./shots/s03.plan.svg'), clip_url: u('./shots/s03.mp4') },
}
const now = Date.now() / 1000
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const record = (id, name, source, extra = {}) => ({
  ...meta, id, name, source, format: 'glb', mime: 'model/gltf-binary', file_size: 97184,
  created: now - 3600, project_id: null, thumb: true,
  url: GLB, thumb_url: u('./four_stroke.thumb.jpg'), turntable_url: u('./four_stroke.turntable.mp4'), ...extra,
})

const store = [record('asset_mock_four_stroke', '单缸四冲程发动机', meta.source)]

export const models = () => store.slice()

export async function plan_model({ description }) {
  await sleep(900)
  const spec = { ...meta.source.spec, name: description?.slice(0, 20) || meta.source.spec.name }
  return { spec, rounds: 2, issues: [{ code: 'dims_mismatch', severity: 'warn', message: '第 1 轮整体高度 0.55 m 和声明的 0.52 m 差 5.8%，第 2 轮已改正。', where: 'dims.h' }] }
}

export async function build_model({ spec }) {
  await sleep(1200)
  const r = record(`asset_mock_${Date.now().toString(36)}`, spec.name, { type: 'parametric', spec }, { created: Date.now() / 1000 })
  store.unshift(r)
  return r
}

export async function upload_model(file, target) {
  await sleep(800)
  const r = record(`asset_mock_${Date.now().toString(36)}`, `${file.name.replace(/\.glb$/i, '')}（mock：显示示例模型）`,
    { type: 'upload', filename: file.name, target }, { created: Date.now() / 1000 })
  store.unshift(r)
  return r
}

export async function image3d_model() {
  await sleep(500)
  return { task_id: `task_mock_${Date.now().toString(36)}` }
}

export const gpu_status = () => ({ state: 'idle' })

// ---- job + shots -------------------------------------------------------------

export const MOCK_JOB = 'job_mock_previs'

export function job() {
  return {
    id: MOCK_JOB, topic: '四冲程发动机是怎么工作的（mock）', status: 'done', renderer: 'previs3d', ratio: '16:9',
    seconds: 30, captions: true, music: 'synth', duration: +(durations.reduce((a, b) => a + b, 0) / 30).toFixed(1),
    created: now - 7200,
    steps: [
      ['storyboard', '写分镜'], ['previs_plan', '规划资产、布景和镜头'], ['assets', '构建 3D 资产'], ['narrate', '配音'],
      ['captions', '逐字时间'], ['compile_shots', '镜头编译和检查'], ['render', '逐镜头渲染'], ['compose', '合成'],
    ].map(([key, label], i) => ({ key, label, status: 'done', started: now - 7000 + i * 40, ended: now - 6990 + i * 40 })),
    scenes: scenes.map((s, i) => ({ ...s, frames: durations[i] })),
  }
}

const REPORTS = {
  s01: [],
  s02: [
    { code: 'subject_occluded', severity: 'warn', message: '进气门在高亮期间有 58% 的帧被气缸盖挡住。', where: 'actions[2]', frame: 96 },
    { code: 'anchor_clamped', severity: 'warn', message: '「行程」在这一段旁白里的时间超出镜头长度，已夹到最后一帧。', where: 'actions[4].at' },
  ],
  s03: [
    { code: 'camera_jump', severity: 'error', message: '第 3 个关键帧前相机一帧移动了 0.31 m（场景尺寸的 22%），超过 15%；要换角度请写 cut: true。', where: 'camera.keys[2]', frame: 141 },
    { code: 'cut_too_close', severity: 'warn', message: '两次硬切间隔 0.6 秒，小于 1 秒。', where: 'camera.keys[1]', frame: 63 },
  ],
}
const shots = {
  s01: { status: 'done', ...SHOT_MEDIA.s01 },
  s02: { status: 'done', ...SHOT_MEDIA.s02 },
  s03: { status: 'failed', stills: [], plan_png: SHOT_MEDIA.s03.plan_png, clip_url: null },
}

export function previs() {
  return {
    plan,
    shots: plan.shots.map((s) => ({
      scene: s.id, resolved_url: null, report: { issues: REPORTS[s.id] }, ...shots[s.id],
    })),
  }
}

export async function render_shot(_jobId, scene) {
  shots[scene] = { ...shots[scene], status: 'running' }
  setTimeout(() => {
    shots[scene] = { status: 'done', ...SHOT_MEDIA[scene] }
    if (scene === 's03') REPORTS.s03 = REPORTS.s03.filter((x) => x.severity !== 'error')
  }, 5000)
  return { ok: true, scene, status: 'running' }
}
