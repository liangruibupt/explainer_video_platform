import { useEffect, useState } from 'react'
import { api, usePoll, ago } from '../api.js'

const STATE = {
  ready: ['GPU 已就绪', 'done'],
  starting: ['GPU 启动中（从 0 冷启动约 8 分钟）', 'running'],
  idle: ['GPU 已缩到 0，提交后自动启动（约 8 分钟）', ''],
  not_deployed: ['端点未部署', 'failed'],
  failed: ['端点故障', 'failed'],
  unavailable: ['无法读取端点状态', 'failed'],
}
const QUICK = [
  '北宋山水长卷风格，远山层叠，江面开阔，一叶渔舟，淡墨设色，绢本质感，画面中没有任何文字',
  '数据中心机房，一排排服务器机柜，蓝色指示灯，冷色调，景深，画面中没有任何文字',
  '扁平插画风格，一颗发光的 GPU 芯片，周围是流动的数据线条，深色背景，画面中没有任何文字',
]

function Result({ it }) {
  const live = it.status === 'queued' || it.status === 'running'
  return (
    <div className="card">
      {it.files?.length
        ? it.files.map((f) => (
          <a key={f} href={`/api/images/${it.id}/${f}`} target="_blank" rel="noreferrer">
            <img className="thumb" src={`/api/images/${it.id}/${f}`} alt="" />
          </a>))
        : <div className="thumb ph">{live ? `生成中${it.waited ? `（已等 ${Math.floor(it.waited / 60)} 分钟）` : '…'}` : '失败'}</div>}
      <div className="card-body">
        <div className="muted">{it.ratio} · {it.mode === 'rgba' ? '透明背景' : '普通'} · seed {it.seed} · {ago(it.created)}
          {it.seconds ? ` · ${it.seconds} 秒` : ''}</div>
        <div className="clamp">{it.prompt}</div>
        {it.error && <div className="error">{it.error}</div>}
      </div>
    </div>
  )
}

export default function ImagePage() {
  const [prompt, setPrompt] = useState('')
  const [ratio, setRatio] = useState('16:9')
  const [mode, setMode] = useState('t2i')
  const [seed, setSeed] = useState(42)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [status, setStatus] = useState(null)
  const [tick, setTick] = useState(0)
  const [items] = usePoll(() => api('/images'), 5000,
    (xs) => xs.some((x) => x.status === 'queued' || x.status === 'running'), [tick])

  useEffect(() => {
    const load = () => api('/image/status').then(setStatus).catch(() => {})
    load(); const t = setInterval(load, 15000); return () => clearInterval(t)
  }, [])

  const go = async () => {
    if (prompt.trim().length < 2) { setErr('先写提示词'); return }
    setBusy(true); setErr(null)
    try {
      await api('/images', { method: 'POST', body: JSON.stringify({ prompt, ratio, mode, seed, n: 1 }) })
      setTick((t) => t + 1)
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }

  const [label, cls] = STATE[status?.state] || ['读取端点状态…', '']
  return (
    <div className="page">
      <h1 className="h1-sm">图像 <span className={`pill ${cls}`}>{label}</span></h1>
      <p className="sub">Qwen-Image 2.1，SageMaker 异步端点，闲置 15 分钟缩到 0。输出 1080p（1:1 为 1440×1440）。
        生成的图也可以在工作台打开「AI 插图」后自动用进讲解视频。</p>
      <div className="composer">
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)}
                  placeholder="描述画面：主体、风格、构图、光线。不想要文字就写「画面中没有任何文字」" />
        <div className="row">
          <label className="opt">比例
            <select value={ratio} onChange={(e) => setRatio(e.target.value)}>
              <option value="16:9">16:9</option><option value="9:16">9:16</option><option value="1:1">1:1</option>
            </select>
          </label>
          <label className="opt">类型
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="t2i">普通</option><option value="rgba">透明背景 PNG</option>
            </select>
          </label>
          <label className="opt">seed
            <input type="number" value={seed} min={0} onChange={(e) => setSeed(+e.target.value || 0)}
                   style={{ width: 80, border: 0, background: 'transparent' }} />
          </label>
          <button className="primary" disabled={busy} onClick={go}>{busy ? '提交中…' : '生成'} <span aria-hidden>→</span></button>
        </div>
      </div>
      {err && <div className="error">{err}</div>}
      <div className="quick"><span>示例</span>
        {QUICK.map((q) => <button key={q} className="chip" onClick={() => setPrompt(q)}>{q.slice(0, 18)}…</button>)}
      </div>
      <div className="section-head"><h2>生成记录</h2></div>
      {(items || []).length === 0
        ? <div className="empty">还没有生成过图像。</div>
        : <div className="grid">{items.map((it) => <Result key={it.id} it={it} />)}</div>}
    </div>
  )
}
