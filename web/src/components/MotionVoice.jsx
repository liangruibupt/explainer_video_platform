import { useEffect, useRef, useState } from 'react'
import { call, errText } from './MotionChat.jsx'
import { liveNarrations } from './voiceLive.js'

// 「配音」 panel of the motion editor (docs/REVOICE_CONTRACT.md §4). The user edits the narration
// per scene (or lets Opus draft it with the consistency check, §3), re-voices only the changed
// scenes as a new take (§2), listens to it, then applies it to the editor or undoes it.

const STATUS = {
  queued: '排队中', running: '正在重新配音', ready: '配好了，请试听确认', failed: '配音失败',
  applied: '已应用到编辑器', undone: '已撤销',
}
const WHERE = { narration: '旁白', caption: '字幕', props: '画面参数' }
const takeLive = (r) => r && (r.status === 'queued' || r.status === 'running')
const norm = (s) => String(s ?? '').trim()

const secs = (t) => {
  if (typeof t === 'number') return t
  const d = t ? new Date(t).getTime() : NaN
  return Number.isNaN(d) ? null : d / 1000
}
const fmtSecs = (frames, fps) => (Math.abs(frames) / fps).toFixed(2)

// 「+0.43 秒，后面的段落顺延」 / 「长度不变」
export function deltaText(delta, fps, last) {
  if (!delta) return '长度不变'
  if (delta > 0) return `+${fmtSecs(delta, fps)} 秒${last ? '，全片相应变长' : '，后面的段落顺延'}`
  return `−${fmtSecs(delta, fps)} 秒${last ? '' : '，后面的段落提前'}`
}


// set_props ops from the consistency check, applied to a copy of the project
export function applyOps(project, ops) {
  const next = JSON.parse(JSON.stringify(project))
  const clips = new Map()
  for (const t of next.tracks || []) for (const c of t.clips || []) clips.set(c.id, c)
  const missing = []
  for (const op of ops || []) {
    const c = clips.get(op.clip_id)
    if (!c || op.op !== 'set_props') { missing.push(op.clip_id); continue }
    c.props = { ...(c.props || {}), ...(op.props || {}) }
    if (typeof op.props?.text === 'string' && c.cardId?.includes(':caption:')) c.label = op.props.text
  }
  return { project: next, missing }
}

export default function MotionVoice({ id, scenes, ready, hidden, readProject, writeProject }) {
  const [records, setRecords] = useState(null) // GET /revoice, newest first
  const [base, setBase] = useState({}) // scene id → narration in use
  const [texts, setTexts] = useState({}) // scene id → edited narration
  const [check, setCheck] = useState(null) // consistency result (+ propsState)
  const [instruction, setInstruction] = useState('')
  const [take, setTake] = useState(null) // record being followed
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState('')
  const [fps, setFps] = useState(30)
  const [playing, setPlaying] = useState(null)
  const [, setNow] = useState(0)
  const audio = useRef(null)
  const propsPrev = useRef(null)
  const loaded = useRef(false)
  const takeBox = useRef(null)
  // bring the take card into view when a take starts or changes state
  useEffect(() => {
    if (take && !hidden) takeBox.current?.scrollIntoView?.({ block: 'nearest' })
  }, [take?.n, take?.status, hidden]) // eslint-disable-line react-hooks/exhaustive-deps

  const order = (scenes || []).map((s) => s.id)
  const label = (sid) => {
    const i = order.indexOf(sid)
    return i < 0 ? sid : `S${i + 1} ${scenes[i].title || sid}`
  }

  // load the takes once the job's scenes are known; resume a running or ready take
  const reload = async (keepTexts) => {
    let list = []
    let failed = null
    try { list = await call(`/jobs/${id}/revoice`) || [] } catch (e) { if (e.status !== 404) failed = e }
    const b = liveNarrations(scenes, list)
    setRecords(list); setBase(b)
    setTexts((t) => (keepTexts ? { ...b, ...t } : b))
    if (failed) throw failed
    return list
  }
  useEffect(() => {
    if (!scenes?.length || loaded.current) return
    loaded.current = true
    ;(async () => {
      try {
        const list = await reload(false)
        const newest = list[0]
        if (newest && (takeLive(newest) || newest.status === 'ready' || (newest.status === 'applied' && newest.live))) setTake(newest)
      } catch (e) { setError(errText(e, '读不到配音记录')) }
    })()
  }, [id, scenes]) // eslint-disable-line react-hooks/exhaustive-deps

  // poll the running take every 2 s
  const liveN = takeLive(take) ? take.n : null
  useEffect(() => {
    if (liveN == null) return
    let alive = true
    let timer
    const tick = async () => {
      try {
        const r = await call(`/jobs/${id}/revoice/${liveN}`)
        if (!alive) return
        setTake(r)
        if (!takeLive(r)) return
      } catch (e) {
        if (alive && e.status === 404) { setTake((t) => ({ ...t, status: 'failed', error: '这次配音的记录不见了' })); return }
      }
      if (alive) timer = setTimeout(tick, 2000)
    }
    timer = setTimeout(tick, 2000)
    const clock = setInterval(() => setNow(Date.now()), 1000)
    return () => { alive = false; clearTimeout(timer); clearInterval(clock) }
  }, [id, liveN])

  useEffect(() => () => audio.current?.pause(), [])
  useEffect(() => { if (hidden) stop() }, [hidden]) // eslint-disable-line react-hooks/exhaustive-deps

  const changed = order.filter((sid) => sid in texts && norm(texts[sid]) !== norm(base[sid]) && norm(texts[sid]))
  const emptied = order.filter((sid) => sid in texts && !norm(texts[sid]))

  const run = async (what, lbl, fn) => {
    if (busy) return
    setError(''); setBusy(lbl)
    try { await fn() } catch (e) { setError(errText(e, what)) } finally { setBusy(null) }
  }

  const project = () => {
    const p = readProject()
    if (p?.fps) setFps(p.fps)
    return p
  }

  const runCheck = () => run('一致性检查失败', 'Opus 正在检查旁白、字幕和画面是否一致…', async () => {
    const body = { project: project() }
    if (instruction.trim()) body.instruction = instruction.trim()
    const r = await call(`/jobs/${id}/consistency`, { method: 'POST', body: JSON.stringify(body) })
    const nar = r?.narrations || {}
    setTexts((t) => {
      const next = { ...t }
      for (const [sid, txt] of Object.entries(nar)) if (sid in next && typeof txt === 'string') next[sid] = txt
      return next
    })
    propsPrev.current = null
    setCheck({ ...r, issues: r?.issues || [], props: r?.props || [], rejected: r?.rejected || [], propsState: null,
      filled: Object.keys(nar).filter((sid) => order.includes(sid)) })
  })

  const applyProps = () => run('应用参数修改失败', '正在应用参数修改…', async () => {
    const prev = readProject()
    const { project: next, missing } = applyOps(prev, check.props)
    await writeProject(next)
    propsPrev.current = prev
    setCheck((c) => ({ ...c, propsState: 'applied', propsMissing: missing }))
  })
  const undoProps = () => run('撤销参数修改失败', '正在撤销参数修改…', async () => {
    await writeProject(propsPrev.current)
    propsPrev.current = null
    setCheck((c) => ({ ...c, propsState: 'undone' }))
  })

  const startRevoice = (all) => run('没能开始重新配音', '正在提交配音…', async () => {
    try { project() } catch { /* fps only */ }
    const narrations = {}
    for (const sid of changed) narrations[sid] = norm(texts[sid])
    const body = all ? { narrations, all: true } : { narrations }
    const r = await call(`/jobs/${id}/revoice`, { method: 'POST', body: JSON.stringify(body) })
    stop(); setTake(r)
  })

  const applyTake = () => run('应用配音失败', '正在把新配音放进编辑器…', async () => {
    const r = await call(`/jobs/${id}/revoice/${take.n}/apply`, { method: 'POST', body: JSON.stringify({ project: project() }) })
    if (!r?.project) throw new Error('服务没有返回新的工程')
    await writeProject(r.project)
    stop()
    setTake((t) => ({ ...t, status: 'applied', changes: r.changes || [] }))
    setCheck(null)
    await reload(false).catch(() => {})
  })

  const undoTake = () => run('撤销配音失败', '正在恢复原来的配音…', async () => {
    const r = await call(`/jobs/${id}/revoice/${take.n}/undo`, { method: 'POST', body: JSON.stringify({ project: project() }) })
    if (!r?.project) throw new Error('服务没有返回恢复后的工程')
    await writeProject(r.project)
    setTake((t) => ({ ...t, status: 'undone' }))
    await reload(false).catch(() => {})
  })

  function stop() {
    audio.current?.pause()
    setPlaying(null)
  }
  const play = (sid) => {
    if (playing === sid) { stop(); return }
    stop()
    const a = new Audio(`/api/jobs/${id}/revoice/${take.n}/audio/${sid}.mp3`)
    a.onended = () => setPlaying(null)
    audio.current = a
    setPlaying(sid)
    a.play().catch(() => { setPlaying(null); setError('试听失败：浏览器播放不了这段音频') })
  }

  const live = takeLive(take)
  const started = take ? secs(take.created) : null
  const elapsed = started ? Math.max(0, Math.round(Date.now() / 1000 - started)) : null
  const tf = take?.total_frames_old != null && take?.total_frames_new != null
  const N = changed.length

  return (
    <section className="vo-pane" hidden={hidden} aria-label="配音">
      <div className="vo-scroll">
        <p className="mc-note">改旁白后重新配音：同一个声音，只重配改动的段落，再整片重新混音和对齐字幕。</p>

        <div className="vo-check">
          <input value={instruction} maxLength={500} placeholder="给 Opus 的说明（可选），例如「把配音里的数字改成和画面一致」"
                 aria-label="一致性检查说明" onChange={(e) => setInstruction(e.target.value)} />
          <button className="btn mc-apply" disabled={!ready || !!busy || live || !scenes?.length} onClick={runCheck}>检查一致性</button>
        </div>

        {check && (
          <div className="vo-result">
            {check.summary && <p className="vo-summary">{check.summary}</p>}
            {check.issues.length > 0 ? (
              <ul className="vo-issues">
                {check.issues.map((x, i) => (
                  <li key={i}>
                    <b>{label(x.scene_id)}</b> <span className="mc-tag">{WHERE[x.where] || x.where}</span>
                    <div>「{x.found}」→「{x.expected}」</div>
                    {x.why && <div className="muted">{x.why}</div>}
                  </li>
                ))}
              </ul>
            ) : <p className="mc-ok">没有发现不一致的地方</p>}
            {check.filled.length > 0 && <p className="muted vo-filled">已把 {check.filled.length} 段新旁白填进下面的文本框，可以再改。</p>}
            {check.rejected.length > 0 && (
              <div className="mc-rejected"><span>没有采用：</span>
                <ul>{check.rejected.map((c, i) => <li key={i}>{typeof c === 'string' ? c : JSON.stringify(c)}</li>)}</ul>
              </div>
            )}
            {check.props.length > 0 && (
              <div className="mc-actions">
                {check.propsState !== 'applied' && (
                  <button className="btn mc-apply" disabled={!ready || !!busy} onClick={applyProps}>
                    {check.propsState === 'undone' ? '重新应用参数修改' : '应用参数修改'}（{check.props.length} 处）
                  </button>
                )}
                {check.propsState === 'applied' && (
                  <>
                    <button className="btn" disabled={!ready || !!busy} onClick={undoProps}>撤销参数修改</button>
                    <span className="mc-ok">参数修改已应用到编辑器</span>
                  </>
                )}
                {check.propsState === 'undone' && <span className="muted">已撤销</span>}
              </div>
            )}
            {check.propsMissing?.length > 0 && check.propsState === 'applied' && (
              <p className="mc-warn">工程里没找到这些片段，已跳过：{check.propsMissing.join('、')}</p>
            )}
          </div>
        )}

        <div className="vo-scenes">
          {(scenes || []).map((s, i) => {
            const on = changed.includes(s.id)
            return (
              <label key={s.id} className={`vo-scene${on ? ' changed' : ''}`}>
                <span className="vo-scene-head">
                  <b>{`S${i + 1} ${s.title || s.id}`}</b>
                  {on && <span className="vo-tag">已改动</span>}
                  {on && (
                    <button type="button" className="mc-link" disabled={live}
                            onClick={(e) => { e.preventDefault(); setTexts((t) => ({ ...t, [s.id]: base[s.id] })) }}>还原</button>
                  )}
                </span>
                <textarea value={texts[s.id] ?? ''} rows={3} maxLength={600} disabled={live} aria-label={`S${i + 1} 旁白`}
                          onChange={(e) => setTexts((t) => ({ ...t, [s.id]: e.target.value }))} />
                {on && <span className="vo-old">原文：{base[s.id]}</span>}
              </label>
            )
          })}
        </div>

        {take && (
          <div ref={takeBox} className={`vo-take ${take.status}`}>
            <div className="mc-edit-head">
              第 {take.n} 次配音
              <span className={`pill ${live ? 'running' : take.status === 'failed' ? 'failed' : take.status === 'ready' || take.status === 'applied' ? 'done' : ''}`}>
                {STATUS[take.status] || take.status}
              </span>
            </div>
            {live && (
              <div className="mc-progress">
                <div className="bar"><i /></div>
                <span className="muted">
                  {take.status === 'queued' ? '等待开始' : (take.stage || '合成配音、整片混音、对齐字幕')}
                  {elapsed !== null ? ` · 已用 ${elapsed} 秒` : ''}
                </span>
              </div>
            )}
            {take.status === 'failed' && <pre className="error pre">{take.error || '配音失败'}</pre>}
            {(take.status === 'ready' || take.status === 'applied') && (
              <>
                {take.voice_drift && (
                  <div className="mc-warn vo-drift" role="alert">
                    这次合成的声音和原配音不完全一样（抽查的未改动段落对不上）。为了整片听起来是同一个声音，建议整片重新配音。
                    {take.status === 'ready' && (
                      <div className="mc-actions">
                        <button className="btn mc-apply" disabled={!!busy || !ready} onClick={() => startRevoice(true)}>整片重新配音</button>
                      </div>
                    )}
                  </div>
                )}
                <ul className="vo-diff">
                  {(take.scenes || []).map((x) => (
                    <li key={x.id}>
                      <div className="vo-scene-head">
                        <b>{label(x.id)}</b>
                        <span className={`vo-delta${x.delta_frames > 0 ? ' longer' : ''}`}>
                          {deltaText(x.delta_frames, fps, order.indexOf(x.id) === order.length - 1)}
                        </span>
                        {take.status === 'ready' && (
                          <button className="btn vo-play" aria-label={`试听 ${label(x.id)}`} onClick={() => play(x.id)}>
                            {playing === x.id ? '■ 停止' : '▶ 试听'}
                          </button>
                        )}
                      </div>
                      {x.old_text !== x.new_text && <div className="vo-old">原：{x.old_text}</div>}
                      <div className="vo-new">新：{x.new_text}</div>
                    </li>
                  ))}
                </ul>
                {tf && (
                  <p className="muted vo-total">
                    全片 {fmtSecs(take.total_frames_old, fps)} 秒 → {fmtSecs(take.total_frames_new, fps)} 秒
                    {take.total_frames_new === take.total_frames_old ? '（总长不变）' : ''}
                  </p>
                )}
              </>
            )}
            {take.status === 'ready' && (
              <div className="mc-actions">
                <button className="btn mc-apply" disabled={!!busy || !ready} onClick={applyTake}>应用到编辑器</button>
              </div>
            )}
            {take.status === 'applied' && (
              <>
                {take.changes?.length > 0 && <ul className="mc-changes">{take.changes.map((c, i) => <li key={i}>{typeof c === 'string' ? c : JSON.stringify(c)}</li>)}</ul>}
                <div className="mc-actions">
                  <button className="btn" disabled={!!busy || !ready} onClick={undoTake}>撤销配音</button>
                  <span className="mc-ok">新配音已放进编辑器，用「保存为新版本」出片</span>
                </div>
              </>
            )}
            {take.status === 'undone' && <p className="muted">已恢复原来的配音</p>}
          </div>
        )}
        {busy && <div className="mc-busy"><i />{busy}</div>}
      </div>

      {error && (
        <div className="mc-error" role="alert">{error}
          <button className="mv-x" aria-label="关闭" onClick={() => setError('')}>×</button>
        </div>
      )}
      <div className="vo-foot">
        {emptied.length > 0 && <span className="mc-warn">旁白不能为空：{emptied.map(label).join('、')}</span>}
        <button className="primary vo-go" disabled={!ready || !!busy || live || !N || emptied.length > 0 || records === null}
                onClick={() => startRevoice(false)}>
          重新配音改动的段落（{N} 段）
        </button>
      </div>
    </section>
  )
}
