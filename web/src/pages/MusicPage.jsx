import { useState } from 'react'
import { api, usePoll, ago } from '../api.js'
import GpuPill from '../components/GpuPill.jsx'

const INSTRUMENTAL = '[Intro]\n\n[Instrumental]\n\n[Outro]'
const TAIL = 'ambient, no drums, instrumental, no vocals'
const QUICK = [
  `Chinese classical instrumental, guqin and xiao flute, slow, ethereal, ${TAIL}`,
  `calm cinematic underscore, soft piano and warm strings, slow, hopeful, ${TAIL}`,
  'Mandopop, warm female vocal, acoustic guitar and piano, mid tempo, nostalgic',
]
const live = (x) => x.status === 'queued' || x.status === 'running'

function Result({ it }) {
  const f = it.files?.[0]
  return (
    <div className="card">
      <div className="card-body">
        {f
          ? <audio src={`/api/musics/${it.id}/${f}`} controls preload="none" style={{ width: '100%' }} />
          : <div className="thumb ph" style={{ aspectRatio: 'auto', padding: 16 }}>
              {live(it) ? `生成中${it.waited ? `（已等 ${Math.floor(it.waited / 60)} 分钟）` : '…'}` : '失败'}</div>}
        <div className="muted">cot {it.cot} · seed {it.seed} · {ago(it.created)}
          {it.audio_seconds ? ` · 时长 ${Math.round(it.audio_seconds)} 秒` : ''}
          {it.seconds ? ` · 用时 ${it.seconds} 秒` : ''}
          {f && <> · <a href={`/api/musics/${it.id}/${f}`} download>下载</a></>}</div>
        <div className="clamp">{it.style}</div>
        {it.lyrics !== INSTRUMENTAL && <div className="clamp muted">{it.lyrics}</div>}
        {it.error && <div className="error">{it.error}</div>}
      </div>
    </div>
  )
}

export default function MusicPage() {
  const [style, setStyle] = useState('')
  const [lyrics, setLyrics] = useState(INSTRUMENTAL)
  const [instrumental, setInstrumental] = useState(true)
  const [cot, setCot] = useState('full')
  const [seed, setSeed] = useState(831001)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [tick, setTick] = useState(0)
  const [items] = usePoll(() => api('/musics'), 5000, (xs) => xs.some(live), [tick])

  const toggle = (on) => {
    setInstrumental(on)
    if (on) {
      setLyrics(INSTRUMENTAL)
      if (style && !style.toLowerCase().endsWith(TAIL)) setStyle(`${style.replace(/[.,\s]+$/, '')}, ${TAIL}`)
    } else if (lyrics === INSTRUMENTAL) setLyrics('[Verse]\n\n\n[Chorus]\n')
  }
  const go = async () => {
    if (style.trim().length < 2) { setErr('先写风格'); return }
    setBusy(true); setErr(null)
    try {
      await api('/musics', { method: 'POST', body: JSON.stringify({ style, lyrics, cot, seed }) })
      setTick((t) => t + 1)
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }

  return (
    <div className="page">
      <h1 className="h1-sm">音乐 <GpuPill module="music" cold={9} /></h1>
      <p className="sub">YuE2-3B，us-east-1 的 EC2 GPU 实例（g6/g5），空闲 15 分钟自己关机。48 kHz 立体声 FLAC，附 ABC 乐谱。
        纯音乐长度 4–5 分钟，不能指定；热实例上约 3 分钟一首，从 0 冷启动约 9 分钟。纯音乐用英文风格词效果最好。</p>
      <div className="composer">
        <textarea value={style} onChange={(e) => setStyle(e.target.value)} style={{ minHeight: 70 }}
                  placeholder="风格（英文）：流派、乐器、速度、情绪。纯音乐以 ambient, no drums, instrumental, no vocals 结尾" />
        <textarea value={lyrics} onChange={(e) => setLyrics(e.target.value)} disabled={instrumental}
                  style={{ minHeight: 90 }} placeholder="歌词，用 [Verse] [Chorus] 等段落标签分段" />
        <div className="row">
          <label className="toggle">
            <input type="checkbox" checked={instrumental} onChange={(e) => toggle(e.target.checked)} />
            <span />纯音乐
          </label>
          <label className="opt" title="full 先写乐谱再生成，结构更完整；off 更快，纯音乐更不容易冒出人声">cot
            <select value={cot} onChange={(e) => setCot(e.target.value)}>
              <option value="full">full</option><option value="off">off</option>
            </select>
          </label>
          <label className="opt">seed
            <input type="number" value={seed} min={0} onChange={(e) => setSeed(+e.target.value || 0)}
                   style={{ width: 90, border: 0, background: 'transparent' }} />
          </label>
          <button className="primary" disabled={busy} onClick={go}>{busy ? '提交中…' : '生成'} <span aria-hidden>→</span></button>
        </div>
      </div>
      {err && <div className="error">{err}</div>}
      <div className="quick"><span>示例</span>
        {QUICK.map((q) => <button key={q} className="chip" onClick={() => { setStyle(q); toggle(q.endsWith(TAIL)) }}>{q.slice(0, 28)}…</button>)}
      </div>
      <div className="section-head"><h2>生成记录</h2></div>
      {(items || []).length === 0
        ? <div className="empty">还没有生成过音乐。</div>
        : <div className="grid">{items.map((it) => <Result key={it.id} it={it} />)}</div>}
    </div>
  )
}
