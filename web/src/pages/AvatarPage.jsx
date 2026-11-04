import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, usePoll, ago } from '../api.js'
import GpuPill from '../components/GpuPill.jsx'

// 数字人 page (docs/AVATAR_CONTRACT.md): portrait + script/audio -> lip-synced presenter video.
const live = (x) => x.status === 'queued' || x.status === 'running'
const STEP = { queued: '排队中', speech: '合成语音', render: '生成视频', done: '完成' }
const SAMPLE = '大家好，欢迎来到直播间！今天给大家带来一款山茶花保湿面霜，质地轻薄，一抹就化开，干皮和换季的时候用都很舒服。直播间下单还送同款小样，喜欢的朋友点击下方链接带走它吧！'

const CSS = `
.av-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
@media (max-width: 980px) { .av-cols { grid-template-columns: 1fr; } }
.av-box { background: var(--card); border: 1px solid var(--line2); border-radius: 14px; padding: 14px 16px; }
.av-box h3 { margin: 0 0 10px; font-size: 15px; font-weight: 600; }
.av-tabs { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.av-pick { display: grid; grid-template-columns: repeat(auto-fill, minmax(84px, 1fr)); gap: 8px; max-height: 220px; overflow: auto; }
.av-pick img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 8px; cursor: pointer; border: 2px solid transparent; }
.av-pick img.on { border-color: var(--accent); }
.av-prev { max-width: 100%; max-height: 220px; border-radius: 10px; display: block; margin-top: 8px; }
.av-note { font-size: 12.5px; color: var(--warn); background: #f7efdc; border-radius: 8px; padding: 6px 10px; margin-top: 10px; }
.av-box textarea { width: 100%; min-height: 120px; resize: vertical; border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px; font: inherit; background: transparent; }
.av-box input[type=text] { width: 100%; border: 1px solid var(--line); border-radius: 10px; padding: 6px 10px; font: inherit; background: transparent; }
.av-est { font-size: 13px; color: var(--ink2); margin-top: 10px; }
.av-thumb { aspect-ratio: 9/16; max-height: 420px; width: 100%; background: #1c1916; object-fit: contain; display: block; }
`

function Seg({ value, onChange, options }) {
  return (
    <div className="av-tabs">
      {options.map(([v, l]) => (
        <button key={v} type="button" className={`chip ${value === v ? 'on' : ''}`} onClick={() => onChange(v)}>{l}</button>
      ))}
    </div>
  )
}

function Result({ it, onDelete, onRetry }) {
  const notes = it.notes || []
  return (
    <div className="card">
      {it.urls?.video
        ? <video className="av-thumb" src={it.urls.video} poster={it.urls.poster} controls preload="metadata" />
        : it.urls?.portrait
          ? <div style={{ position: 'relative' }}>
              <img className="av-thumb" src={it.urls.portrait} alt="" style={{ opacity: live(it) ? 0.55 : 0.35 }} />
              <div className="thumb ph" style={{ position: 'absolute', inset: 0, background: 'transparent', color: '#fff' }}>
                {live(it) ? `${STEP[it.step] || '生成中'}${it.waited ? `（已等 ${Math.floor(it.waited / 60)} 分钟）` : '…'}` : '失败'}
              </div>
            </div>
          : <div className="thumb ph">{live(it) ? '生成中…' : '失败'}</div>}
      <div className="card-body">
        <div className="muted">{it.model_used} · {it.size}p{it.fast ? ' · 快速' : ''} · seed {it.seed} · {ago(it.created)}
          {it.video_seconds ? ` · 时长 ${Math.round(it.video_seconds)} 秒` : it.audio_seconds ? ` · 语音约 ${Math.round(it.audio_seconds)} 秒` : ''}
          {it.seconds && !live(it) ? ` · 用时 ${Math.round(it.seconds / 60)} 分钟` : ''}
          {it.segments > 1 ? ` · 分 ${it.segments} 段` : ''}</div>
        {it.title && <div className="clamp">{it.title}</div>}
        {it.script && <div className="clamp muted" title={it.script}>口播稿：{it.script}</div>}
        {live(it) && notes.length > 0 && <div className="muted">{notes[notes.length - 1].text}</div>}
        {it.error && <div className="error">{it.error}</div>}
        <div className="muted" style={{ marginTop: 6 }}>
          {it.urls?.video && <><a href={it.urls.video} download>下载视频</a> · </>}
          {it.urls?.audio && <><a href={it.urls.audio} download>语音</a> · </>}
          {it.status === 'failed' && <><a href="#" onClick={(e) => { e.preventDefault(); onRetry(it.id) }}>重试{it.segments > 1 ? '（已完成的段直接复用）' : ''}</a> · </>}
          {!live(it) && <a href="#" onClick={(e) => { e.preventDefault(); onDelete(it.id) }}>删除</a>}
        </div>
      </div>
    </div>
  )
}

export default function AvatarPage() {
  const [opt, setOpt] = useState(null)
  const [pSrc, setPSrc] = useState('upload')
  const [pFile, setPFile] = useState(null)
  const [pAsset, setPAsset] = useState('')
  const [pImage, setPImage] = useState('')       // "<image id>:<index>"
  const [assets, setAssets] = useState([])
  const [gens, setGens] = useState([])
  const [aSrc, setASrc] = useState('script')
  const [script, setScript] = useState('')
  const [voice, setVoice] = useState('Zhiyu')
  const [aFile, setAFile] = useState(null)
  const [aSeconds, setASeconds] = useState(0)
  const [scene, setScene] = useState('live')
  const [prompt, setPrompt] = useState('')
  const [model, setModel] = useState('')
  const [size, setSize] = useState('480')
  const [fast, setFast] = useState(true)
  const [seed, setSeed] = useState(42)
  const [segment, setSegment] = useState('auto')   // auto = 每段 ≤ 55 秒, off = 不分段
  const [est, setEst] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [tick, setTick] = useState(0)
  const [items] = usePoll(() => api('/avatars'), 5000, (xs) => xs.some(live), [tick])

  useEffect(() => {
    api('/avatars/options').then((o) => {
      setOpt(o)
      const sc = o.scenes.find((s) => s.id === 'live')
      if (sc) setPrompt(sc.prompt)
    }).catch((e) => setErr(e.message))
  }, [])
  useEffect(() => {
    if (pSrc === 'library') api('/assets?kind=image').then(setAssets).catch(() => setAssets([]))
    if (pSrc === 'generated') api('/images').then((xs) => setGens(xs.filter((x) => x.status === 'done'))).catch(() => setGens([]))
  }, [pSrc])

  const pPreview = useMemo(() => (pFile ? URL.createObjectURL(pFile) : null), [pFile])
  useEffect(() => () => { if (pPreview) URL.revokeObjectURL(pPreview) }, [pPreview])

  const segS = opt?.limits?.segment_s || 55
  useEffect(() => {
    const q = new URLSearchParams({ model, size, fast: String(fast), segment_s: segment === 'auto' ? String(segS) : '0' })
    if (aSrc === 'script') q.set('script', script || ' ')
    else q.set('seconds', String(aSeconds || 30))
    const t = setTimeout(() => api(`/avatars/estimate?${q}`).then(setEst).catch(() => {}), 300)
    return () => clearTimeout(t)
  }, [model, size, fast, aSrc, script, aSeconds, segment, segS])

  const pickAudio = (f) => {
    setAFile(f); setASeconds(0)
    if (!f) return
    const a = new Audio(URL.createObjectURL(f))
    a.onloadedmetadata = () => setASeconds(a.duration || 0)
  }
  const pickScene = (id) => {
    setScene(id)
    const sc = opt?.scenes.find((s) => s.id === id)
    if (sc) setPrompt(sc.prompt)
  }

  const go = async () => {
    setErr(null)
    const fd = new FormData()
    if (pSrc === 'upload') { if (!pFile) return setErr('先选一张人像图片'); fd.append('portrait', pFile) }
    if (pSrc === 'library') { if (!pAsset) return setErr('先在素材库里选一张人像'); fd.append('portrait_asset_id', pAsset) }
    if (pSrc === 'generated') {
      if (!pImage) return setErr('先选一张图像页生成的人像')
      const [id, idx] = pImage.split(':'); fd.append('image_id', id); fd.append('image_index', idx)
    }
    if (aSrc === 'script') {
      if (script.trim().length < 2) return setErr('先写口播稿')
      fd.append('script', script); fd.append('voice', voice)
    } else {
      if (!aFile) return setErr('先选一个音频文件'); fd.append('audio', aFile)
    }
    fd.append('scene', scene); fd.append('prompt', prompt); fd.append('model', model)
    fd.append('size', size); fd.append('fast', String(fast)); fd.append('seed', String(seed))
    fd.append('segment_s', segment === 'auto' ? String(segS) : '0')
    setBusy(true)
    try {
      const r = await fetch('/api/avatars', { method: 'POST', body: fd })
      if (!r.ok) {
        let m = `${r.status}`
        try { const j = await r.json(); m = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail) } catch { /* */ }
        throw new Error(m)
      }
      setTick((t) => t + 1)
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  const del = async (id) => {
    if (!window.confirm('删除这条数字人视频？')) return
    try { await api(`/avatars/${id}`, { method: 'DELETE' }); setTick((t) => t + 1) } catch (e) { setErr(e.message) }
  }

  const retry = async (id) => {
    try { await api(`/avatars/${id}/retry`, { method: 'POST' }); setTick((t) => t + 1) } catch (e) { setErr(e.message) }
  }

  const max = opt?.limits?.script_chars || 2000
  return (
    <div className="page">
      <style>{CSS}</style>
      <h1 className="h1-sm">数字人 <GpuPill module="avatar" cold={opt?.cold_min || 13} /></h1>
      <p className="sub">一张人像（或一段参考视频）加一段口播语音，生成口型、表情和上半身动作同步的口播视频，用于电商口播、商超口播和直播间预录片段（离线生成，不是实时直播）。
        GPU 在 us-east-1 的 EC2 上按需启动，空闲 15 分钟自己关机。{opt && !opt.configured && <b> 数字人 GPU 模块还未部署，现在提交会失败。</b>}</p>

      <div className="av-cols">
        <div className="av-box">
          <h3>1. 人像</h3>
          <Seg value={pSrc} onChange={setPSrc} options={[['upload', '上传图片'], ['library', '素材库'], ['generated', '图像页生成']]} />
          {pSrc === 'upload' && <>
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => setPFile(e.target.files[0] || null)} />
            {pPreview && <img className="av-prev" src={pPreview} alt="人像预览" />}
          </>}
          {pSrc === 'library' && (assets.length
            ? <div className="av-pick">{assets.map((a) => (
                <img key={a.id} src={a.thumb_url || a.url} alt={a.name} title={a.name}
                     className={pAsset === a.id ? 'on' : ''} onClick={() => setPAsset(a.id)} />))}</div>
            : <div className="muted">素材库里还没有图片。<Link to="/library">去素材库上传</Link></div>)}
          {pSrc === 'generated' && (gens.length
            ? <div className="av-pick">{gens.flatMap((g) => (g.files || []).map((f, i) => (
                <img key={`${g.id}:${i}`} src={`/api/images/${g.id}/${f}`} alt={g.prompt} title={g.prompt}
                     className={pImage === `${g.id}:${i}` ? 'on' : ''} onClick={() => setPImage(`${g.id}:${i}`)} />)))}</div>
            : <div className="muted">还没有生成过图像。</div>)}
          <div className="muted" style={{ marginTop: 8 }}>没有合适的人像？<Link to="/image">去图像页 AI 生成</Link>（例如「电商直播间女主播半身像，正面看镜头，写实摄影」，竖屏 9:16）。正脸、五官清晰、上半身入镜效果最好。</div>
          <div className="av-note">只能使用本人、已获授权或 AI 生成的人像。</div>
        </div>

        <div className="av-box">
          <h3>2. 声音</h3>
          <Seg value={aSrc} onChange={setASrc} options={[['script', '口播稿 + 声音'], ['audio', '上传音频']]} />
          {aSrc === 'script' ? <>
            <textarea value={script} maxLength={max} onChange={(e) => setScript(e.target.value)}
                      placeholder="写一段口播稿，Polly 合成语音。30–60 秒约 150–250 字。" />
            <div className="row" style={{ marginTop: 6 }}>
              <label className="opt">声音
                <select value={voice} onChange={(e) => setVoice(e.target.value)}>
                  {(opt?.voices || [{ id: 'Zhiyu', label: '知语 · 普通话女声' }]).map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
                </select>
              </label>
              <span className="muted">{script.length} / {max} 字</span>
              <button type="button" className="chip" onClick={() => setScript(SAMPLE)}>示例口播稿</button>
            </div>
          </> : <>
            <input type="file" accept="audio/*" onChange={(e) => pickAudio(e.target.files[0] || null)} />
            <div className="muted" style={{ marginTop: 6 }}>wav / mp3 / m4a / flac，最长 {Math.round((opt?.limits?.audio_seconds || 600) / 60)} 分钟。
              {aSeconds ? ` 这段 ${Math.round(aSeconds)} 秒。` : ''}</div>
          </>}
        </div>
      </div>

      <div className="av-box" style={{ marginTop: 18 }}>
        <h3>3. 场景与模型</h3>
        <Seg value={scene} onChange={pickScene} options={(opt?.scenes || []).map((s) => [s.id, s.label])} />
        <input type="text" value={prompt} maxLength={opt?.limits?.prompt_chars || 600} onChange={(e) => setPrompt(e.target.value)}
               placeholder="场景描述（英文效果更稳）" />
        <div className="row">
          <label className="opt">模型
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              {(opt?.models || []).map((m) => m.default
                ? <option key={m.id} value="">{m.label}（推荐）</option>
                : <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </label>
          <label className="opt">清晰度
            <select value={size} onChange={(e) => setSize(e.target.value)}>
              <option value="480">480p</option><option value="720">720p</option>
            </select>
          </label>
          <label className="opt"><input type="checkbox" checked={fast} onChange={(e) => setFast(e.target.checked)} /> 快速（少步数）</label>
          <label className="opt">seed
            <input type="number" value={seed} min={0} onChange={(e) => setSeed(+e.target.value || 0)}
                   style={{ width: 80, border: 0, background: 'transparent' }} />
          </label>
          <button className="primary" disabled={busy} onClick={go}>{busy ? '提交中…' : '生成数字人视频'} <span aria-hidden>→</span></button>
        </div>
        <div className="row" style={{ marginTop: 6 }}>
          <span className="opt">分段</span>
          <Seg value={segment} onChange={setSegment} options={[['auto', `自动，每段 ≤ ${segS} 秒`], ['off', '不分段']]} />
        </div>
        <div className="muted">长口播一条生成，人像在约 60 秒后会慢慢不像原图；自动分段在句子结尾（上传音频则在停顿处）切开，每段都从原人像生成，再拼成一条（声音连续，接缝处 0.3 秒淡入淡出）。参考视频输入不分段。</div>
        {est && <div className="av-est">
          预计：视频约 {Math.round(est.seconds)} 秒{est.segments > 1 ? `，分 ${est.segments} 段生成` : ''}，生成约 {est.render_min} 分钟（GPU 从 0 启动另加约 {est.cold_min} 分钟），
          GPU 费用约 ${est.usd_warm}（冷启动约 ${est.usd_cold}，按 ${est.usd_per_hour}/小时）。粗估，实测以 docs/AVATAR.md 为准。
        </div>}
      </div>
      {err && <div className="error">{err}</div>}

      <div className="section-head"><h2>生成记录</h2></div>
      {(items || []).length === 0
        ? <div className="empty">还没有生成过数字人视频。</div>
        : <div className="grid">{items.map((it) => <Result key={it.id} it={it} onDelete={del} onRetry={retry} />)}</div>}
    </div>
  )
}
