import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { api, usePoll, isLive } from '../api.js'
import WorkCard from '../components/WorkCard.jsx'

const QUICK = [
  '为什么天空是蓝色的：瑞利散射原理',
  '用 5 页幻灯片讲清楚 TCP 三次握手',
  '电动车的电池管理系统（BMS）是怎么工作的',
  'Transformer 的注意力机制，一步一步讲',
  '复利的原理，以及为什么越早开始越好',
  '光合作用的两个阶段：光反应和暗反应',
]

export default function Workbench() {
  const nav = useNavigate()
  const [topic, setTopic] = useState('')
  const [seconds, setSeconds] = useState(60)
  const [ratio, setRatio] = useState('16:9')
  const [captions, setCaptions] = useState(true)
  const [music, setMusic] = useState('synth')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [jobs] = usePoll(() => api('/jobs'), 4000, (js) => js.some(isLive), [])

  const start = async () => {
    if (topic.trim().length < 2) { setErr('请先写一句想讲的主题'); return }
    setBusy(true); setErr(null)
    try {
      const j = await api('/jobs', {
        method: 'POST',
        body: JSON.stringify({ topic, seconds, ratio, captions, music }),
      })
      nav(`/jobs/${j.id}`)
    } catch (e) { setErr(e.message); setBusy(false) }
  }

  const recent = (jobs || []).slice(0, 3)
  return (
    <div className="page">
      <div className="kicker">EXPLAINER STUDIO</div>
      <h1>把<span className="accent">原理</span>，讲成一支视频。</h1>
      <p className="sub">写一句主题。Opus 5.5 写分镜和绘制代码，代码逐帧画出画面，配上旁白、逐字字幕、音乐和音效。适合原理讲解、幻灯片式讲解，不做 AI 短剧。</p>

      <div className="composer">
        <textarea value={topic} onChange={(e) => setTopic(e.target.value)}
                  placeholder="想讲清楚什么？例如：为什么飞机能飞起来"
                  onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) start() }} />
        <div className="row">
          <label className="opt">时长
            <select value={seconds} onChange={(e) => setSeconds(+e.target.value)}>
              {[30, 45, 60, 90, 120].map((s) => <option key={s} value={s}>约 {s} 秒</option>)}
            </select>
          </label>
          <label className="opt">画幅
            <select value={ratio} onChange={(e) => setRatio(e.target.value)}>
              <option value="16:9">横屏 16:9</option>
              <option value="9:16">竖屏 9:16</option>
            </select>
          </label>
          <label className="opt">音乐
            <select value={music} onChange={(e) => setMusic(e.target.value)}>
              <option value="synth">合成垫乐</option>
              <option value="none">无</option>
            </select>
          </label>
          <label className="toggle">
            <input type="checkbox" checked={captions} onChange={(e) => setCaptions(e.target.checked)} />
            <span />逐字字幕
          </label>
          <button className="primary" disabled={busy} onClick={start}>
            {busy ? '提交中…' : '开始制作'} <span aria-hidden>→</span>
          </button>
        </div>
      </div>
      {err && <div className="error">{err}</div>}

      <div className="quick">
        <span>快捷开始</span>
        {QUICK.map((q) => <button key={q} className="chip" onClick={() => setTopic(q)}>{q}</button>)}
      </div>
      <p className="hint">一支 60 秒的视频大约需要 3–6 分钟：写分镜、配音、写绘制代码、逐帧渲染、合成。</p>

      <div className="section-head">
        <h2>最近作品</h2>
        <Link to="/works" className="more">查看全部 →</Link>
      </div>
      {recent.length === 0
        ? <div className="empty">还没有作品。写一个主题试试。</div>
        : <div className="grid">{recent.map((j) => <WorkCard key={j.id} job={j} />)}</div>}
    </div>
  )
}
