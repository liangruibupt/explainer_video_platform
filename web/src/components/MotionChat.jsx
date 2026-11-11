import { useEffect, useRef, useState } from 'react'

// Opus chat panel of the motion editor (docs/MOTION_ASSIST_CONTRACT.md §5). Parameter edits come
// back as a whole project that the page writes into the workbench and reloads; scene rewrites run
// as a staged scene edit (§4) that the user previews as stills before applying.

const MAX_TURNS = 20
const histKey = (id) => `evp-motion-chat-${id}`
const NOTE = '旁白和配音不能在这里改；改完画面后用「保存为新版本」出片。'
const EDIT_STATUS = {
  queued: '排队中', running: 'Opus 正在改写画面', ready: '改好了，请确认', failed: '改写失败',
  applied: '已应用', discarded: '已放弃', undone: '已撤销',
}
const editLive = (e) => e && (e.status === 'queued' || e.status === 'running')

// fetch that keeps the HTTP status, so 409 can get its own message
export async function call(path, opts = {}) {
  const r = await fetch(`/api${path}`, { headers: { 'Content-Type': 'application/json' }, ...opts })
  let body = null
  try { body = await r.json() } catch { /* not json */ }
  if (!r.ok) {
    const d = body?.detail
    const e = new Error(typeof d === 'string' ? d : d ? JSON.stringify(d) : `${r.status}`)
    e.status = r.status
    throw e
  }
  return body
}

// Chinese message for a failed request; plain errors (no status) carry their own message
export function errText(e, what) {
  const detail = e.message && e.message !== String(e.status) ? e.message : ''
  if (e.status === 409) return `${what}：${detail || '当前状态不允许这个操作'}`
  if (e.status === 404) return `${what}：找不到这个作品或记录${detail ? `（${detail}）` : ''}`
  if (e.status === 400 || e.status === 422) return `${what}：请求不合法${detail ? `（${detail}）` : ''}`
  if (e.status >= 500) return `${what}：服务出错（${e.status}），请稍后再试${detail ? `。${detail}` : ''}`
  if (e.status) return `${what}：${detail || e.status}`
  if (e.name === 'TypeError') return `${what}：连不上服务，请检查网络后重试`
  return `${what}：${e.message}`
}

const newId = () => `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`
const trim = (ts) => ts.slice(-MAX_TURNS)

function loadHistory(id) {
  try {
    const v = JSON.parse(localStorage.getItem(histKey(id)) || '[]')
    return Array.isArray(v) ? v.filter((t) => t && t.id && (t.role === 'user' || t.role === 'assistant')) : []
  } catch { return [] }
}

// the persisted part of a scene-edit record (log_tail stays in memory only)
const slim = (r) => r && ({
  n: r.n, scene_id: r.scene_id, status: r.status, created: r.created, ended: r.ended,
  stills: r.stills || [], layout: r.layout || null, error: r.error || null,
  last: (r.log_tail || '').trim().split('\n').pop() || '',
})

const secs = (t) => {
  if (typeof t === 'number') return t
  const d = t ? new Date(t).getTime() : NaN
  return Number.isNaN(d) ? null : d / 1000
}

// scene clips carry cardId proj:shot:scene-<id> (workbench manifest grouping, maybe a ~n suffix)
export function withDefaults(project, sceneId, defaults) {
  const next = JSON.parse(JSON.stringify(project))
  const card = `proj:shot:scene-${sceneId}`
  let count = 0
  for (const track of next.tracks || []) {
    for (const clip of track.clips || []) {
      if (clip.cardId !== card && !String(clip.cardId || '').startsWith(`${card}~`)) continue
      const props = { ...defaults }
      if (clip.props && 'duration' in clip.props && !('duration' in props)) props.duration = clip.props.duration
      clip.props = props
      count += 1
    }
  }
  return { project: next, count }
}

export default function MotionChat({ id, scenes, ready, readProject, writeProject, onClose }) {
  const [turns, setTurns] = useState(() => loadHistory(id))
  const [text, setText] = useState('')
  const [scene, setScene] = useState('')
  const [busy, setBusy] = useState(null) // label of the running action
  const [error, setError] = useState('')
  const [zoom, setZoom] = useState(null)
  const [, setNow] = useState(0)
  const mem = useRef({}) // turn id → {project, prev}; not persisted (projects are large)
  const list = useRef(null)

  useEffect(() => { setTurns(loadHistory(id)); mem.current = {} }, [id])
  useEffect(() => {
    try { localStorage.setItem(histKey(id), JSON.stringify(trim(turns))) } catch { /* storage full */ }
  }, [id, turns])
  useEffect(() => { list.current?.scrollTo(0, list.current.scrollHeight) }, [turns.length, busy])
  useEffect(() => {
    if (!zoom) return
    const esc = (e) => e.key === 'Escape' && setZoom(null)
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [zoom])

  const patch = (tid, f) => setTurns((ts) => ts.map((t) => (t.id === tid ? { ...t, ...(typeof f === 'function' ? f(t) : f) } : t)))
  const label = (sid) => {
    const i = (scenes || []).findIndex((s) => s.id === sid)
    return i < 0 ? sid : `S${i + 1} ${scenes[i].title || sid}`
  }

  // poll every running scene edit every 2 s (also resumes after a page reload)
  const live = turns.filter((t) => editLive(t.edit)).map((t) => `${t.id}:${t.edit.n}`).join(',')
  useEffect(() => {
    if (!live) return
    let alive = true
    let timer
    const tick = async () => {
      for (const pair of live.split(',')) {
        const [tid, n] = pair.split(':')
        try {
          const r = await call(`/jobs/${id}/scene-edits/${n}`)
          if (alive) patch(tid, { edit: slim(r) })
        } catch (e) {
          if (alive && e.status === 404) patch(tid, (t) => ({ edit: { ...t.edit, status: 'failed', error: '这次画面修改的记录不见了' } }))
        }
      }
      if (alive) { setNow(Date.now()); timer = setTimeout(tick, 2000) }
    }
    timer = setTimeout(tick, 2000)
    return () => { alive = false; clearTimeout(timer) }
  }, [id, live]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (what, label, fn) => {
    if (busy) return
    setError(''); setBusy(label)
    try { await fn() } catch (e) { setError(errText(e, what)) } finally { setBusy(null) }
  }

  const send = async () => {
    const msg = text.trim()
    if (!msg || busy || !ready) return
    setError('')
    let project
    try { project = readProject() } catch (e) { setError(e.message); return }
    const history = turns.filter((t) => t.text).slice(-MAX_TURNS).map((t) => ({ role: t.role, text: t.text }))
    const ut = { id: newId(), role: 'user', text: msg, scene_id: scene || null }
    setTurns((ts) => trim([...ts, ut])); setText(''); setBusy('Opus 正在思考…')
    try {
      const body = { message: msg, project, history }
      if (scene) body.scene_id = scene
      const r = await call(`/jobs/${id}/assist`, { method: 'POST', body: JSON.stringify(body) })
      const at = {
        id: newId(), role: 'assistant', text: r?.reply || '', changes: r?.changes || [], rejected: r?.rejected || [],
        scene_edit: r?.scene_edit || null, hasProject: !!r?.project, applied: null,
      }
      if (r?.project) mem.current[at.id] = { project: r.project }
      setTurns((ts) => trim([...ts, at]))
    } catch (e) {
      setTurns((ts) => ts.filter((t) => t.id !== ut.id)); setText(msg)
      setError(errText(e, 'Opus 没有回答'))
    } finally { setBusy(null) }
  }

  const applyParams = (t) => run('应用失败', '正在应用修改…', async () => {
    const m = mem.current[t.id]
    const prev = readProject()
    await writeProject(m.project)
    m.prev = prev
    patch(t.id, { applied: 'applied' })
  })
  const undoParams = (t) => run('撤销失败', '正在撤销…', async () => {
    await writeProject(mem.current[t.id].prev)
    patch(t.id, { applied: 'undone' })
  })

  // after an apply/undo the editor UI was rebuilt: put the scene's (new or old) DEFAULTS into its
  // clips, keep the user's timing, and reload the editor
  const reloadWithDefaults = async (tid, sceneId, defaults) => {
    const current = readProject()
    if (!defaults) { await writeProject(current); return }
    const { project, count } = withDefaults(current, sceneId, defaults)
    await writeProject(project)
    if (!count) patch(tid, { notice: '工程里没找到这个镜头的片段，画面已更新，参数没有替换' })
  }

  const startEdit = (t) => run('没能开始改画面', '正在提交画面修改…', async () => {
    const r = await call(`/jobs/${id}/scene-edits`, { method: 'POST', body: JSON.stringify(t.scene_edit) })
    patch(t.id, { edit: slim(r), notice: null })
  })
  const applyEdit = (t) => run('应用画面失败', '正在应用新画面并重建编辑器…', async () => {
    const r = await call(`/jobs/${id}/scene-edits/${t.edit.n}/apply`, { method: 'POST' })
    patch(t.id, (x) => ({ edit: { ...x.edit, status: 'applied' } }))
    await reloadWithDefaults(t.id, r?.scene_id || t.edit.scene_id, r?.defaults)
  })
  const discardEdit = (t) => run('放弃失败', '正在放弃…', async () => {
    await call(`/jobs/${id}/scene-edits/${t.edit.n}/discard`, { method: 'POST' })
    patch(t.id, (x) => ({ edit: { ...x.edit, status: 'discarded' } }))
  })
  const undoEdit = (t) => run('撤销画面失败', '正在恢复原来的画面…', async () => {
    const r = await call(`/jobs/${id}/scene-edits/${t.edit.n}/undo`, { method: 'POST' })
    patch(t.id, (x) => ({ edit: { ...x.edit, status: 'undone' } }))
    await reloadWithDefaults(t.id, r?.scene_id || t.edit.scene_id, r?.defaults)
  })

  const clear = () => { if (!busy) { setTurns([]); mem.current = {}; setError('') } }

  return (
    <aside className="mc-panel" aria-label="Opus 助手">
      <div className="mc-head">
        <b>Opus 助手</b>
        {turns.length > 0 && <button className="mc-link" onClick={clear} disabled={!!busy}>清空记录</button>}
        <button className="mc-close" aria-label="收起助手" title="收起" onClick={onClose}>›</button>
      </div>
      <p className="mc-note">{NOTE}</p>
      <label className="mc-scene">
        <span>修改范围</span>
        <select value={scene} onChange={(e) => setScene(e.target.value)} aria-label="修改范围">
          <option value="">整片</option>
          {(scenes || []).map((s, i) => <option key={s.id} value={s.id}>{`S${i + 1} ${s.title || s.id}`}</option>)}
        </select>
      </label>

      <div className="mc-list" ref={list}>
        {!turns.length && (
          <p className="mc-empty">用一句话说想怎么改，例如「把第二段的标题改成『三年三倍的含义』」或「柱状图换成折线图」。</p>
        )}
        {turns.map((t) => (t.role === 'user'
          ? (
            <div key={t.id} className="mc-turn user">
              {t.scene_id && <span className="mc-tag">{label(t.scene_id)}</span>}
              <div className="mc-text">{t.text}</div>
            </div>
          ) : (
            <div key={t.id} className="mc-turn assistant">
              {t.text && <div className="mc-text">{t.text}</div>}
              {t.changes?.length > 0 && (
                <ul className="mc-changes">{t.changes.map((c, i) => <li key={i}>{c}</li>)}</ul>
              )}
              {t.rejected?.length > 0 && (
                <div className="mc-rejected"><span>没有执行：</span>
                  <ul>{t.rejected.map((c, i) => <li key={i}>{c}</li>)}</ul>
                </div>
              )}
              {t.hasProject && (mem.current[t.id] ? (
                <div className="mc-actions">
                  {t.applied !== 'applied' && (
                    <button className="btn mc-apply" disabled={!!busy || !ready} onClick={() => applyParams(t)}>
                      {t.applied === 'undone' ? '重新应用' : '应用修改'}
                    </button>
                  )}
                  {t.applied === 'applied' && (
                    <button className="btn" disabled={!!busy || !ready} onClick={() => undoParams(t)}>撤销这次修改</button>
                  )}
                  {t.applied === 'applied' && <span className="mc-ok">已应用到编辑器</span>}
                  {t.applied === 'undone' && <span className="muted">已撤销</span>}
                </div>
              ) : (
                <p className="muted mc-stale">
                  {t.applied === 'applied' ? '已应用。' : ''}刷新页面后这次修改不能再一键应用或撤销，需要的话请重新发送。
                </p>
              ))}
              {t.scene_edit && (
                <SceneEdit t={t} label={label(t.scene_edit.scene_id)} busy={!!busy} ready={ready}
                           onStart={startEdit} onApply={applyEdit} onDiscard={discardEdit} onUndo={undoEdit} onZoom={setZoom} />
              )}
              {t.notice && <p className="mc-warn">{t.notice}</p>}
            </div>
          )))}
        {busy && <div className="mc-busy"><i />{busy}</div>}
      </div>

      {error && (
        <div className="mc-error" role="alert">{error}
          <button className="mv-x" aria-label="关闭" onClick={() => setError('')}>×</button>
        </div>
      )}
      <div className="mc-input">
        <textarea value={text} placeholder={ready ? '告诉 Opus 想怎么改…（Enter 发送，Shift+Enter 换行）' : '等编辑器加载完成…'}
                  maxLength={2000} rows={3} disabled={!ready} aria-label="给 Opus 的消息"
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send() } }} />
        <button className="primary mc-send" disabled={!ready || !!busy || !text.trim()} onClick={send}>发送</button>
      </div>

      {zoom && (
        <div className="mc-zoom" role="dialog" aria-label="放大预览" onClick={() => setZoom(null)}>
          <img src={zoom} alt="预览大图" />
        </div>
      )}
    </aside>
  )
}

function SceneEdit({ t, label, busy, ready, onStart, onApply, onDiscard, onUndo, onZoom }) {
  const e = t.edit
  if (!e) {
    return (
      <div className="mc-edit">
        <div className="mc-edit-head">这需要改写画面：<b>{label}</b></div>
        {t.scene_edit.instruction && <p className="mc-instr">{t.scene_edit.instruction}</p>}
        <div className="mc-actions">
          <button className="btn mc-apply" disabled={busy || !ready} onClick={() => onStart(t)}>让 Opus 改这段画面</button>
        </div>
      </div>
    )
  }
  const started = secs(e.created)
  const elapsed = started ? Math.max(0, Math.round(Date.now() / 1000 - started)) : null
  const layout = e.layout
  return (
    <div className={`mc-edit ${e.status}`}>
      <div className="mc-edit-head">
        改写画面 <b>{label}</b>
        <span className={`pill ${editLive(e) ? 'running' : e.status === 'failed' ? 'failed' : e.status === 'applied' || e.status === 'ready' ? 'done' : ''}`}>
          {EDIT_STATUS[e.status] || e.status}
        </span>
      </div>
      {editLive(e) && (
        <div className="mc-progress">
          <div className="bar"><i /></div>
          <span className="muted">{e.status === 'queued' ? '等待开始' : '改写代码、类型检查、出预览图'}{elapsed !== null ? ` · 已用 ${elapsed} 秒` : ''}</span>
          {e.last && <code className="mc-log">{e.last}</code>}
        </div>
      )}
      {e.status === 'failed' && <pre className="error pre">{e.error || '改写失败'}</pre>}
      {e.stills?.length > 0 && !editLive(e) && e.status !== 'failed' && (
        <div className="mc-stills">
          {e.stills.map((u, k) => (
            <button key={k} className="mc-still" title="点击放大" onClick={() => onZoom(u)}>
              <img src={u} alt={`预览 ${k + 1}`} loading="lazy" />
            </button>
          ))}
        </div>
      )}
      {layout && !editLive(e) && e.status !== 'failed' && (
        layout.status === 'ok' || !layout.issues?.length
          ? <p className="mc-ok">版面检查：通过</p>
          : (
            <div className="mc-warn">版面检查发现 {layout.issues.length} 个问题：
              <ul>{layout.issues.map((x, i) => <li key={i}>{typeof x === 'string' ? x : JSON.stringify(x)}</li>)}</ul>
            </div>
          )
      )}
      {e.status === 'ready' && (
        <div className="mc-actions">
          <button className="btn mc-apply" disabled={busy || !ready} onClick={() => onApply(t)}>应用</button>
          <button className="btn" disabled={busy} onClick={() => onDiscard(t)}>放弃</button>
        </div>
      )}
      {e.status === 'applied' && (
        <div className="mc-actions">
          <button className="btn" disabled={busy || !ready} onClick={() => onUndo(t)}>撤销</button>
          <span className="mc-ok">新画面已放进编辑器</span>
        </div>
      )}
    </div>
  )
}
