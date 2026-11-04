import { useState } from 'react'
import { api, usePoll, ago } from '../api.js'
import GpuPill from '../components/GpuPill.jsx'

const QUICK = [
  'Early morning at West Lake in Hangzhou, thin mist over calm water, a stone arch bridge and willows on the shore, slow dolly forward, soft golden light, no text',
  'Inside a data center, long rows of server racks with blinking blue lights, slow tracking shot along the aisle, cool light, shallow depth of field, no text',
  'A modern wind farm on green hills at sunset, turbine blades turning slowly, aerial drone shot moving sideways, warm light, no text',
]
const live = (x) => x.status === 'queued' || x.status === 'running'

function Result({ it }) {
  return (
    <div className="card">
      {it.files?.length
        ? <video className="thumb" src={`/api/videos/${it.id}/0.mp4`} controls preload="metadata" />
        : <div className="thumb ph">{live(it) ? `生成中${it.waited ? `（已等 ${Math.floor(it.waited / 60)} 分钟）` : '…'}` : '失败'}</div>}
      <div className="card-body">
        <div className="muted">{it.aspect} · 5 秒 · seed {it.seed} · {ago(it.created)}
          {it.seconds ? ` · 用时 ${it.seconds} 秒` : ''}</div>
        <div className="clamp">{it.prompt}</div>
        {it.error && <div className="error">{it.error}</div>}
      </div>
    </div>
  )
}

export default function VideoPage() {
  const [prompt, setPrompt] = useState('')
  const [aspect, setAspect] = useState('16:9')
  const [seed, setSeed] = useState(7)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [tick, setTick] = useState(0)
  const [items] = usePoll(() => api('/videos'), 5000, (xs) => xs.some(live), [tick])

  const go = async () => {
    if (prompt.trim().length < 2) { setErr('先写镜头描述'); return }
    setBusy(true); setErr(null)
    try {
      await api('/videos', { method: 'POST', body: JSON.stringify({ prompt, aspect, seed }) })
      setTick((t) => t + 1)
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }

  return (
    <div className="page">
      <h1 className="h1-sm">AI 视频 <GpuPill module="video" cold={12} /></h1>
      <p className="sub">MiniMax H3，ap-south-1 的 EC2 g7e 实例，空闲 15 分钟自己关机。每条 5 秒、768p、24 fps，画面和音频一起生成。
        热实例约 1 分钟一条，从 0 冷启动约 12.5 分钟。工作台打开「AI 视频镜头」后，讲解视频里合适的段落会用它做动态背景。</p>
      <div className="composer">
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)}
                  placeholder="描述一个镜头（英文效果更稳）：主体、场景、镜头运动、光线、时间。例如 a slow dolly over a misty lake at dawn, no text" />
        <div className="row">
          <label className="opt">画幅
            <select value={aspect} onChange={(e) => setAspect(e.target.value)}>
              <option value="16:9">横屏 16:9</option><option value="9:16">竖屏 9:16</option>
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
        {QUICK.map((q) => <button key={q} className="chip" onClick={() => setPrompt(q)}>{q.slice(0, 28)}…</button>)}
      </div>
      <div className="section-head"><h2>生成记录</h2></div>
      {(items || []).length === 0
        ? <div className="empty">还没有生成过视频。</div>
        : <div className="grid">{items.map((it) => <Result key={it.id} it={it} />)}</div>}
    </div>
  )
}
