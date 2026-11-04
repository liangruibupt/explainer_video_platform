import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, usePoll, ago } from '../api.js'

const TABS = [
  ['all', '全部'], ['live', '进行中'], ['review', '待审核'], ['done', '已完成'], ['failed', '失败'],
]
const KIND = {
  job: ['🎬', '讲解视频'], image: ['🖼️', '图像'], video: ['📹', 'AI 视频'],
  music: ['🎵', '音乐'], workflow: ['🔀', '工作流'], avatar: ['🧑‍💼', '数字人'],
}
const STATUS_ZH = {
  queued: '排队中', running: '进行中', waiting_review: '待审核', done: '已完成', failed: '失败',
}
const LIVE = (s) => s === 'queued' || s === 'running'

const CSS = `
.tc-tabs { display: flex; gap: 6px; margin: 14px 0 12px; flex-wrap: wrap; }
.tc-tabs button { border: 1px solid var(--line); background: var(--card); border-radius: 99px;
  padding: 4px 14px; cursor: pointer; font: inherit; font-size: 13px; color: var(--ink2); }
.tc-tabs button.on { background: var(--accent-soft); color: var(--accent); border-color: transparent; }
.tc-tabs .n { margin-left: 4px; opacity: .7; }
.tc-kind { width: 30px; font-size: 18px; text-align: center; }
.tc-title small { display: block; color: var(--ink2); font-weight: 400; font-size: 12px; }
.tc-bar { width: 110px; height: 6px; background: var(--line); border-radius: 99px; overflow: hidden; }
.tc-bar > div { height: 100%; background: var(--accent); }
.tc-bar.done > div { background: var(--ok); } .tc-bar.failed > div { background: var(--bad); }
.tc-gpu { color: var(--warn); font-size: 12px; }
.tc-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.tc-bell { position: relative; }
.tc-bell > button { border: 1px solid var(--line); background: var(--card); border-radius: 99px;
  padding: 4px 12px; cursor: pointer; font: inherit; }
.tc-bell .badge { background: var(--bad); color: #fff; border-radius: 99px; font-size: 11px;
  padding: 0 6px; margin-left: 4px; }
.tc-notes { position: absolute; right: 0; top: 36px; width: 360px; max-height: 420px; overflow: auto;
  background: var(--card); border: 1px solid var(--line); border-radius: 10px; z-index: 20;
  box-shadow: 0 6px 24px rgba(0,0,0,.12); }
.tc-notes .top { display: flex; justify-content: space-between; padding: 8px 12px;
  border-bottom: 1px solid var(--line); font-size: 13px; }
.tc-note { padding: 9px 12px; border-top: 1px solid var(--line); font-size: 13px; }
.tc-note:first-of-type { border-top: 0; }
.tc-note.unread { background: var(--accent-soft); }
.tc-note .t { font-weight: 500; } .tc-note .b { color: var(--ink2); font-size: 12px; }
.tc-note.error .t { color: var(--bad); }
.tc-reviews { display: grid; gap: 12px; margin-bottom: 18px; }
.tc-review { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
.tc-review .meta { display: flex; justify-content: space-between; gap: 8px; font-size: 13px; margin-bottom: 8px; }
.tc-review img, .tc-review video { max-width: 100%; max-height: 320px; border-radius: 6px; display: block; }
.tc-review audio { width: 100%; }
.tc-review textarea { width: 100%; box-sizing: border-box; margin: 8px 0; font: inherit; }
.tc-review .text { white-space: pre-wrap; max-height: 200px; overflow: auto; font-size: 13px;
  background: var(--bg, #faf7f2); padding: 8px; border-radius: 6px; }
.tc-cands { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 8px; }
.tc-cand { border: 2px solid var(--line); border-radius: 8px; padding: 4px; cursor: pointer;
  background: none; font: inherit; max-width: 220px; }
.tc-cand.on { border-color: var(--accent); }
.tc-cand img, .tc-cand video { max-width: 200px; max-height: 140px; }
.tc-actions { display: flex; gap: 8px; }
`

function Media({ kind, src }) {
  if (kind === 'image') return <img src={src} alt="" />
  if (kind === 'video') return <video src={src} controls preload="metadata" />
  if (kind === 'audio') return <audio src={src} controls preload="metadata" />
  return null
}

function ReviewCard({ rv, onDone }) {
  const [note, setNote] = useState('')
  const [choice, setChoice] = useState(rv.candidates.length ? 0 : null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const decide = async (decision) => {
    setBusy(true); setErr(null)
    try {
      const body = { decision, note }
      if (choice !== null && rv.candidates.length) body.choice = choice
      await api(`/tasks/reviews/${rv.run_id}/${rv.node_id}`, { method: 'POST', body: JSON.stringify(body) })
      onDone()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  return (
    <div className="tc-review">
      <div className="meta">
        <span><b>{rv.run_name || rv.run_id}</b> · 节点 {rv.node_id}</span>
        <span className="muted">{rv.since ? ago(rv.since) : ''}</span>
      </div>
      {rv.note && <div className="muted" style={{ fontSize: 13, marginBottom: 6 }}>{rv.note}</div>}
      {rv.candidates.length > 0 ? (
        <div className="tc-cands">
          {rv.candidates.map((c) => (
            <button type="button" key={c.index} className={`tc-cand ${choice === c.index ? 'on' : ''}`}
              onClick={() => setChoice(c.index)}>
              {c.url ? <Media kind={c.kind} src={c.url} /> : <div className="text">{c.text}</div>}
              <div style={{ fontSize: 12 }}>{c.label}</div>
            </button>
          ))}
        </div>
      ) : rv.preview_url ? <Media kind={rv.preview_kind} src={rv.preview_url} />
        : rv.text != null ? <div className="text">{rv.text}</div>
          : rv.value != null ? <div className="text">{JSON.stringify(rv.value, null, 1)}</div> : null}
      <textarea rows={2} placeholder="审核意见（可选）" value={note} onChange={(e) => setNote(e.target.value)} />
      {err && <div className="error">{err}</div>}
      <div className="tc-actions">
        <button className="primary" disabled={busy} onClick={() => decide('approve')}>
          {rv.candidates.length ? `批准候选 ${choice + 1}` : '批准'}
        </button>
        <button className="btn" disabled={busy} onClick={() => decide('reject')}>打回</button>
        <Link className="btn" to={`/workflow?run=${rv.run_id}`}>打开工作流</Link>
      </div>
    </div>
  )
}

function Bell() {
  const [open, setOpen] = useState(false)
  const [tick, setTick] = useState(0)
  const [data] = usePoll(() => api('/tasks/notifications?limit=50'), 10000, () => true, [tick])
  const shown = useRef(null)
  useEffect(() => {
    if (!data) return
    const unread = data.items.filter((n) => !n.read)
    if (shown.current === null) { shown.current = new Set(unread.map((n) => n.id)); return }
    const fresh = unread.filter((n) => !shown.current.has(n.id))
    fresh.forEach((n) => shown.current.add(n.id))
    if (!data.settings?.browser || !('Notification' in window) || !fresh.length) return
    const show = () => fresh.forEach((n) => {
      const x = new Notification(n.title, { body: n.body, tag: n.id })
      x.onclick = () => { window.focus(); if (n.url) window.location.href = n.url }
    })
    if (Notification.permission === 'granted') show()
    else if (Notification.permission !== 'denied') Notification.requestPermission().then((p) => p === 'granted' && show())
  }, [data])
  const readAll = async () => {
    await api('/tasks/notifications/read', { method: 'POST', body: JSON.stringify({ all: true }) })
    setTick((t) => t + 1)
  }
  const readOne = async (id) => {
    await api('/tasks/notifications/read', { method: 'POST', body: JSON.stringify({ ids: [id] }) })
    setTick((t) => t + 1)
  }
  const unread = data?.unread || 0
  return (
    <div className="tc-bell">
      <button onClick={() => setOpen((o) => !o)}>🔔 通知{unread > 0 && <span className="badge">{unread}</span>}</button>
      {open && (
        <div className="tc-notes">
          <div className="top">
            <span>通知</span>
            <button className="btn" disabled={!unread} onClick={readAll}>全部已读</button>
          </div>
          {(!data || data.items.length === 0) && <div className="tc-note muted">还没有通知。</div>}
          {(data?.items || []).map((n) => (
            <div key={n.id} className={`tc-note ${n.read ? '' : 'unread'} ${n.level}`}>
              <div className="t">
                {n.url ? <Link to={n.url} onClick={() => { readOne(n.id); setOpen(false) }}>{n.title}</Link> : n.title}
              </div>
              {n.body && <div className="b">{n.body}</div>}
              <div className="b">{ago(n.t)}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Row({ it }) {
  const [icon, label] = KIND[it.kind] || ['•', it.kind]
  const { done = 0, total = 0 } = it.progress || {}
  const pct = total ? Math.round((100 * done) / total) : 0
  const sub = it.status === 'failed' ? it.error : it.step || (it.reviews?.length ? `等待审核：${it.reviews.map((r) => r.node_id).join('、')}` : '')
  return (
    <tr>
      <td className="tc-kind" title={label}>{icon}</td>
      <td className="tc-title">
        <Link to={it.url}>{it.title}</Link>
        <small>{label}{sub ? ` · ${String(sub).slice(0, 80)}` : ''}</small>
      </td>
      <td><span className={`pill ${it.status}`}>{STATUS_ZH[it.status] || it.status}</span></td>
      <td>
        <div className={`tc-bar ${it.status}`}><div style={{ width: `${it.status === 'done' ? 100 : pct}%` }} /></div>
        <span className="muted" style={{ fontSize: 12 }}>{done}/{total}</span>
      </td>
      <td className="tc-gpu">{it.gpu_wait ? `等待 GPU ${it.gpu_wait} 分钟` : ''}</td>
      <td className="muted">{it.created ? ago(it.created) : ''}</td>
    </tr>
  )
}

export default function Tasks() {
  const [params, setParams] = useSearchParams()
  const tab = TABS.some(([k]) => k === params.get('tab')) ? params.get('tab') : 'all'
  const [tick, setTick] = useState(0)
  const [items, error] = usePoll(() => api('/tasks'), 4000, () => true, [tick])
  const [reviews] = usePoll(() => api('/tasks/reviews'), 5000, () => true, [tick])
  const all = items || []
  const count = {
    all: all.length, live: all.filter((i) => LIVE(i.status)).length,
    review: (reviews || []).length, done: all.filter((i) => i.status === 'done').length,
    failed: all.filter((i) => i.status === 'failed').length,
  }
  const shown = all.filter((i) => (
    tab === 'all' || (tab === 'live' && LIVE(i.status)) || (tab === 'review' && i.status === 'waiting_review')
    || (tab === 'done' && i.status === 'done') || (tab === 'failed' && i.status === 'failed')))
  return (
    <div className="page">
      <style>{CSS}</style>
      <div className="tc-head">
        <h1 className="h1-sm">任务中心</h1>
        <Bell />
      </div>
      <p className="sub">讲解视频、图像、视频、音乐和工作流的所有任务；需要你审核的节点在「待审核」。</p>
      <div className="tc-tabs">
        {TABS.map(([k, l]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setParams(k === 'all' ? {} : { tab: k })}>
            {l}<span className="n">{count[k]}</span>
          </button>
        ))}
      </div>
      {error && <div className="error">{error}</div>}
      {tab === 'review' && (
        <div className="tc-reviews">
          {reviews && reviews.length === 0 && <div className="empty">没有等待审核的节点。</div>}
          {(reviews || []).map((rv) => (
            <ReviewCard key={`${rv.run_id}/${rv.node_id}`} rv={rv} onDone={() => setTick((t) => t + 1)} />
          ))}
        </div>
      )}
      {items && shown.length === 0 && tab !== 'review' && <div className="empty">这里还没有任务。</div>}
      {shown.length > 0 && (
        <table className="tasks">
          <tbody>{shown.map((it) => <Row key={`${it.kind}:${it.id}`} it={it} />)}</tbody>
        </table>
      )}
    </div>
  )
}
