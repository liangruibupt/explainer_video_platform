import { useEffect, useMemo, useRef, useState } from 'react'
import ModelViewer, { fmtLen } from '../../components/ModelViewer.jsx'
import { Link } from 'react-router-dom'
import { papi, withMock, ApiError, viewerMeta } from './mock.js'
import { PCSS, Issues, issuesOf, sourceLabel } from './ui.jsx'

export { sourceLabel }

// Model assets in the library (docs/PREVIS_CONTRACT.md §12): the viewer dialog and the
// 「新建 3D 资产」 dialog with its three entries (§11 HTTP API).

export function ModelModal({ asset, onClose }) {
  const meta = useMemo(() => viewerMeta(asset), [asset])
  useEffect(() => {
    const k = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k)
  }, [onClose])
  return (
    <div className="pv-mask" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <style>{PCSS}</style>
      <div className="pv-dlg" role="dialog" aria-label={asset.name}>
        <div className="pv-head">
          <h3>{asset.name}</h3>
          {sourceLabel(asset) && <span className="pill">{sourceLabel(asset)}</span>}
          <button className="x" onClick={onClose} aria-label="关闭">×</button>
        </div>
        <ModelViewer src={asset.url} meta={meta} height={460} />
        <p className="muted" style={{ margin: '8px 0 0' }}>素材 id <code>{asset.id}</code>。在预演规划里用 <code>library</code> 引用它，镜头可以驱动上面的自由度。</p>
      </div>
    </div>
  )
}

const AXES = [['h', '高'], ['w', '宽'], ['d', '深'], ['longest', '最长边']]
function TargetInput({ axis, metres, onAxis, onMetres }) {
  return (
    <label className="opt">目标尺寸
      <select value={axis} onChange={(e) => onAxis(e.target.value)}>
        {AXES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
      <input type="number" min={0.001} max={1000} step={0.01} value={metres} onChange={(e) => onMetres(e.target.value)} aria-label="目标尺寸（米）" />米
    </label>
  )
}
const validMetres = (m) => Number.isFinite(+m) && +m > 0 && +m <= 1000

// ---- 一句话（参数化） --------------------------------------------------------
function TextEntry({ onCreated }) {
  const [desc, setDesc] = useState('')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState(null)
  const [planned, setPlanned] = useState(null)
  const [issues, setIssues] = useState([])
  const [built, setBuilt] = useState(null)

  const plan = async () => {
    if (desc.trim().length < 2) { setErr('先写一句要做的模型'); return }
    setBusy('plan'); setErr(null); setPlanned(null); setBuilt(null); setIssues([])
    try {
      const r = await withMock(() => papi('/assets/model/plan', { method: 'POST', body: JSON.stringify({ description: desc.trim() }) }), 'plan_model', { description: desc.trim() })
      setPlanned(r); setIssues(r.issues || [])
    } catch (e) { setErr(e.message) }
    setBusy('')
  }
  const build = async () => {
    setBusy('build'); setErr(null)
    try {
      const r = await withMock(() => papi('/assets/model/build', { method: 'POST', body: JSON.stringify({ spec: planned.spec }) }), 'build_model', { spec: planned.spec })
      setBuilt(r); onCreated(r)
    } catch (e) {
      if (e instanceof ApiError && e.status === 422 && e.body) setIssues(issuesOf(e.body.detail?.issues ? e.body.detail : e.body))
      setErr(e instanceof ApiError && e.status === 422 ? '规格没有通过校验，见下面的问题' : e.message)
    }
    setBusy('')
  }
  const s = planned?.spec
  return (
    <div className="pv-form">
      <textarea value={desc} onChange={(e) => setDesc(e.target.value)}
                placeholder="一句话描述要建的模型和尺寸，例如：单缸四冲程发动机，缸径 8 厘米，能转曲轴看活塞和气门" />
      <div className="pv-row">
        <span className="muted">Opus 写参数化规格（图元 + 参数 + 自由度），不入库；构建后才进素材库。适合机械、建筑、示意结构。</span>
        <button className="primary" disabled={!!busy} onClick={plan}>{busy === 'plan' ? '规划中…' : planned ? '重新规划' : '写规格'}</button>
      </div>
      {err && <div className="error">{err}</div>}
      {s && (
        <>
          <div className="pv-spec">
            <b>{s.name}</b>
            <span>声明尺寸 {['w', 'h', 'd'].map((k) => (typeof s.dims[k] === 'number' ? fmtLen(s.dims[k]) : s.dims[k])).join(' × ')}</span>
            <span>{s.parts.length} 个部件</span>
            <span>自由度：{Object.entries(s.dofs || {}).map(([k, d]) => d.label || k).join('、') || '无'}</span>
            {planned.rounds && <span>{planned.rounds} 轮</span>}
          </div>
          <details className="pv-json"><summary className="muted">规格 JSON</summary><pre>{JSON.stringify(s, null, 1)}</pre></details>
        </>
      )}
      <Issues issues={issues} />
      {s && !built && (
        <div className="pv-row">
          <span className="muted">在服务器上构建 GLB、校验尺寸，生成转台预览，再在这里预览。</span>
          <button className="primary" disabled={!!busy} onClick={build}>{busy === 'build' ? '构建中…' : '构建并预览'}</button>
        </div>
      )}
      {built && <><div className="pv-ok">已入库：{built.name}（{built.id}）</div><ModelViewer src={built.url} meta={viewerMeta(built)} height={340} /></>}
    </div>
  )
}

// Same states as components/GpuPill.jsx; local so a missing /api/mesh/status (part E not
// merged yet) answers from the dev mock instead of staying on 「读取…」.
function GpuStatus({ module, what, cold }) {
  const [st, setSt] = useState(null)
  useEffect(() => {
    const load = () => withMock(() => papi(`/${module}/status`), 'gpu_status').then(setSt).catch(() => setSt({ state: 'unavailable' }))
    load(); const t = setInterval(load, 15000); return () => clearInterval(t)
  }, [module])
  const STATE = {
    ready: [`${what}已就绪${st?.instance_type ? `（${st.instance_type}）` : ''}`, 'done'],
    starting: [`${what}启动中（从 0 冷启动约 ${cold} 分钟）`, 'running'],
    idle: [`${what}未运行，提交后自动启动（约 ${cold} 分钟）`, ''],
    not_deployed: [`${what}未部署`, 'failed'],
    failed: [`${what}故障`, 'failed'],
    unavailable: [`无法读取${what}状态`, 'failed'],
  }
  const [label, cls] = STATE[st?.state] || [`读取${what}状态…`, '']
  return <span className={`pill ${cls}`}>{label}</span>
}

// ---- 图生 3D -----------------------------------------------------------------
// Measured 2026-11-25 (docs/MESH.md section 5, g7.2xlarge): mesh worker from 0 to ready ~6 min
// (357 / 359 s to the first GLB); warm concept image 51 s; warm mesh step (2mini job ~30 s +
// normalize and turntable on this box ~22 s) under 1 min. Image module cold start 433-443 s.
const ETA = { cold: 6, concept: 1, mesh: 1, imageCold: 7 }

function ImageEntry() {
  const [mode, setMode] = useState('prompt')
  const [prompt, setPrompt] = useState('')
  const [images, setImages] = useState(null)
  const [pick, setPick] = useState('')
  const [axis, setAxis] = useState('h')
  const [metres, setMetres] = useState('0.3')
  const [model, setModel] = useState('')
  const [seed, setSeed] = useState(42)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [task, setTask] = useState(null)
  useEffect(() => {
    if (mode === 'image' && !images) papi('/assets?kind=image').then(setImages).catch((e) => { setImages([]); setErr(e.message) })
  }, [mode, images])

  const submit = async () => {
    if (mode === 'prompt' && prompt.trim().length < 2) { setErr('先写提示词'); return }
    if (mode === 'image' && !pick) { setErr('先选一张图'); return }
    if (!validMetres(metres)) { setErr('目标尺寸要是 0 到 1000 米之间的数'); return }
    setBusy(true); setErr(null)
    const body = { target: { axis, metres: +metres }, seed, ...(model ? { model } : {}),
                   ...(mode === 'prompt' ? { prompt: prompt.trim() } : { image_asset: pick }) }
    try { setTask(await withMock(() => papi('/assets/model/image3d', { method: 'POST', body: JSON.stringify(body) }), 'image3d_model', body)) } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  const eta = ETA.mesh + (mode === 'prompt' ? ETA.concept : 0)
  return (
    <div className="pv-form">
      <div className="pv-row">
        <button className={`chip ${mode === 'prompt' ? 'on' : ''}`} onClick={() => setMode('prompt')}>用提示词出概念图</button>
        <button className={`chip ${mode === 'image' ? 'on' : ''}`} onClick={() => setMode('image')}>选素材库里的图</button>
        <span style={{ flex: 1 }} />
        <GpuStatus module="mesh" what="网格 GPU" cold={ETA.cold} />
        {mode === 'prompt' && <GpuStatus module="image" what="概念图 GPU" cold={ETA.imageCold} />}
      </div>
      {mode === 'prompt'
        ? <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="要生成的物体，例如：一只坐着的橘猫。会自动加上「单个物体，纯白背景，三分之二侧视角，完整不裁切」" />
        : <div className="pv-imgs">
            {images == null && <span className="muted">加载图片…</span>}
            {images?.length === 0 && <span className="muted">素材库里还没有图片。</span>}
            {images?.map((a) => (
              <button key={a.id} className={pick === a.id ? 'on' : ''} title={a.name} onClick={() => setPick(a.id)}>
                <img src={a.thumb_url || a.url} alt={a.name} loading="lazy" />
              </button>
            ))}
          </div>}
      <div className="pv-row">
        <TargetInput axis={axis} metres={metres} onAxis={setAxis} onMetres={setMetres} />
        <label className="opt">模型
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">默认</option>
            <option value="hunyuan3d-2mini">Hunyuan3D-2 mini</option>
            <option value="hunyuan3d-2">Hunyuan3D-2</option>
          </select>
        </label>
        <label className="opt">seed
          <input type="number" min={0} value={seed} onChange={(e) => setSeed(+e.target.value || 0)} />
        </label>
        <button className="primary" disabled={busy} onClick={submit}>{busy ? '提交中…' : '生成'} <span aria-hidden>→</span></button>
      </div>
      <p className="muted" style={{ margin: 0 }}>
        预计：GPU 已就绪时约 {eta} 分钟（{mode === 'prompt' ? `概念图约 ${ETA.concept} 分钟 + ` : ''}网格约 {ETA.mesh} 分钟）；GPU 从 0 启动再加约 {mode === 'prompt' ? `${ETA.imageCold + ETA.cold} 分钟（概念图 GPU 约 ${ETA.imageCold} 分钟，网格 GPU 在概念图出来后再启动，约 ${ETA.cold} 分钟）` : `${ETA.cold} 分钟`}。
        只出白模几何，按目标尺寸等比缩放、落地、减面到 6 万面以内。适合动物、雕塑、植物这类有机形体。
      </p>
      {err && <div className="error">{err}</div>}
      {task && <div className="pv-ok">已提交（任务 {task.task_id}），在 <Link to="/tasks">任务中心</Link> 看进度，完成后出现在素材库。</div>}
    </div>
  )
}

// ---- 上传 GLB ----------------------------------------------------------------
function UploadEntry({ onCreated }) {
  const [file, setFile] = useState(null)
  const [axis, setAxis] = useState('longest')
  const [metres, setMetres] = useState('1')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [done, setDone] = useState(null)
  const input = useRef(null)
  const submit = async () => {
    if (!file) { setErr('先选一个 .glb 文件'); return }
    if (file.size > 200 * 1024 * 1024) { setErr('文件超过 200 MB'); return }
    if (!validMetres(metres)) { setErr('目标尺寸要是 0 到 1000 米之间的数'); return }
    setBusy(true); setErr(null)
    const fd = new FormData()
    fd.append('file', file); fd.append('target_axis', axis); fd.append('target_metres', String(+metres))
    try {
      const r = await withMock(() => papi('/assets/model/upload', { method: 'POST', body: fd }), 'upload_model', file, { axis, metres: +metres })
      setDone(r); onCreated(r)
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  return (
    <div className="pv-form">
      <div className="al-drop" onClick={() => input.current?.click()}>
        {file ? `${file.name}（${(file.size / 1024 / 1024).toFixed(1)} MB）` : '点击选择 .glb 文件'}
        <small>glTF 2.0 二进制，单个不超过 200 MB。没有真实单位也可以：按下面的目标尺寸等比缩放，底面落到地面，减面到 6 万面以内</small>
        <input ref={input} type="file" accept=".glb,model/gltf-binary" hidden onChange={(e) => { setFile(e.target.files[0] || null); setDone(null); e.target.value = '' }} />
      </div>
      <div className="pv-row">
        <TargetInput axis={axis} metres={metres} onAxis={setAxis} onMetres={setMetres} />
        <button className="primary" disabled={busy} onClick={submit}>{busy ? '上传中…' : '上传并规范化'}</button>
      </div>
      {err && <div className="error">{err}</div>}
      {done && <><div className="pv-ok">已入库：{done.name}</div><ModelViewer src={done.url} meta={viewerMeta(done)} height={320} /></>}
    </div>
  )
}

const ENTRIES = [['text', '一句话'], ['image', '图生 3D'], ['upload', '上传 GLB']]

export function NewModelDialog({ onClose, onCreated }) {
  const [tab, setTab] = useState('text')
  return (
    <div className="pv-mask" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <style>{PCSS}</style>
      <div className="pv-dlg" role="dialog" aria-label="新建 3D 资产">
        <div className="pv-head">
          <h3>新建 3D 资产</h3>
          <span className="muted">三种来源都输出带真实尺寸（米）的 GLB</span>
          <button className="x" onClick={onClose} aria-label="关闭">×</button>
        </div>
        <div className="pv-tabs">
          {ENTRIES.map(([k, l]) => <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>)}
        </div>
        {tab === 'text' && <TextEntry onCreated={onCreated} />}
        {tab === 'image' && <ImageEntry />}
        {tab === 'upload' && <UploadEntry onCreated={onCreated} />}
      </div>
    </div>
  )
}
