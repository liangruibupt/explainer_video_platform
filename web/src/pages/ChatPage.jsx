import { useEffect, useRef, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { api, ago } from '../api.js'

// 对话创作 (DESIGN.md section 7): one Agent that asks follow-up questions, builds a workflow,
// runs it and shows progress, intermediate results and review buttons inline.

const MODES = [
  ['auto', 'Agent'], ['image', '图像'], ['video', '视频'], ['music', '音乐'], ['explainer', '讲解视频'],
]
const MODE_LABEL = Object.fromEntries(MODES)
const NODE_STATUS = {
  pending: '等待', running: '运行中', waiting_review: '待审核', done: '完成', failed: '失败',
  cached: '缓存命中',
}
const RUN_STATUS = { running: '运行中', waiting_review: '等待审核', done: '已完成', failed: '失败' }
const STATE_LABEL = { collecting: '沟通中', running: '制作中', done: '已完成', failed: '失败' }
const live = (s) => s?.progress && ['running', 'waiting_review'].includes(s.progress.status)

const CSS = `
.chat-shell { display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: 18px; align-items: start; }
.chat-list { display: flex; flex-direction: column; gap: 6px; max-height: calc(100vh - 160px); overflow: auto; }
.chat-item { text-align: left; border: 1px solid var(--line); background: var(--card); border-radius: 10px;
  padding: 8px 10px; cursor: pointer; font-size: 13px; color: var(--ink); }
.chat-item.sel { border-color: var(--accent); background: var(--accent-soft); }
.chat-item .muted { display: block; margin-top: 2px; font-size: 11.5px; }
.chat-main { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.chat-msgs { display: flex; flex-direction: column; gap: 10px; min-height: 260px; max-height: calc(100vh - 360px);
  overflow: auto; padding: 4px 2px; }
.msg { max-width: 82%; padding: 9px 13px; border-radius: 12px; font-size: 14px; line-height: 1.55;
  white-space: pre-wrap; word-break: break-word; }
.msg.user { align-self: flex-end; background: var(--accent); color: #fff; border-bottom-right-radius: 4px; }
.msg.assistant { align-self: flex-start; background: var(--card); border: 1px solid var(--line);
  border-bottom-left-radius: 4px; }
.msg.assistant.question { border-color: var(--accent); }
.msg.assistant.error { background: #f8e3df; color: var(--bad); border-color: #ecc3bc; }
.msg.assistant.status { background: transparent; border-style: dashed; color: var(--ink2); font-size: 13px; }
.chat-modes { display: flex; gap: 6px; flex-wrap: wrap; }
.chat-mode { border: 1px solid var(--line2); background: var(--card); border-radius: 999px; padding: 5px 13px;
  cursor: pointer; font-size: 13px; color: var(--ink2); }
.chat-mode.sel { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); }
.chat-input textarea { min-height: 60px; }
.prog-nodes { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
.prog-node { border-top: 1px solid var(--line); padding-top: 8px; }
.prog-node:first-child { border-top: 0; padding-top: 0; }
.prog-head { display: flex; gap: 8px; align-items: center; font-size: 13px; }
.prog-head .nid { font: 11px var(--mono); color: var(--ink3); }
.prog-head .pill { margin-left: auto; }
.pill.cached { background: #dcebe1; color: var(--ok); }
.prog-out { margin-top: 6px; }
.prog-out img, .prog-out video { max-width: 100%; max-height: 300px; border-radius: 8px; display: block; background: #1c1916; }
.prog-out audio { width: 100%; }
.prog-text { white-space: pre-wrap; font-size: 12.5px; background: #fff; border: 1px solid var(--line);
  border-radius: 8px; padding: 8px 10px; max-height: 200px; overflow: auto; }
.prog-review { margin-top: 8px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.prog-review input { flex: 1; min-width: 160px; border: 1px solid var(--line2); border-radius: 8px; padding: 6px 10px; }
@media (max-width: 900px) { .chat-shell { grid-template-columns: 1fr; } .chat-list { max-height: 160px; } }
`

function Output({ o }) {
  if (o.kind === 'image') return <img src={o.url} alt={o.port} />
  if (o.kind === 'video') return <video src={o.url} controls preload="metadata" />
  if (o.kind === 'audio') return <audio src={o.url} controls preload="metadata" />
  if (o.text) return <div className="prog-text">{o.text}</div>
  return null
}

function Review({ runId, node, onDone }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const send = async (decision) => {
    setBusy(true); setErr(null)
    try {
      await api(`/wf_runs/${runId}/review`, {
        method: 'POST', body: JSON.stringify({ node_id: node.id, decision, note }),
      })
      onDone()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }
  return (
    <>
      {node.review && <div className="prog-out"><Output o={node.review} /></div>}
      <div className="prog-review">
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="审核意见（可选）" />
        <button className="btn" disabled={busy} onClick={() => send('approve')}>批准</button>
        <button className="btn danger" disabled={busy} onClick={() => send('reject')}>打回</button>
      </div>
      {err && <div className="error">{err}</div>}
    </>
  )
}

function Progress({ p, onChange }) {
  return (
    <div className="panel">
      <h3>
        {p.name || '工作流'} <span className={`pill ${p.status}`}>{RUN_STATUS[p.status] || p.status}</span>
        <Link to="/workflow" className="more" style={{ float: 'right' }}>在工作流页查看</Link>
      </h3>
      <ul className="prog-nodes">
        {p.nodes.map((n) => (
          <li key={n.id} className="prog-node">
            <div className="prog-head">
              <span className="nid">{n.id}</span> {n.label}
              {n.note && n.status === 'running' && <span className="muted">{n.note}</span>}
              {n.seconds != null && <span className="muted">{Math.round(n.seconds)}s</span>}
              <span className={`pill ${n.status}`}>{NODE_STATUS[n.status] || n.status}</span>
            </div>
            {n.error && <div className="error">{n.error}</div>}
            {n.outputs.map((o) => <div key={o.port} className="prog-out"><Output o={o} /></div>)}
            {n.status === 'waiting_review' && <Review runId={p.run_id} node={n} onDone={onChange} />}
          </li>
        ))}
      </ul>
      {p.works.length > 0 && (
        <>
          <h3 style={{ marginTop: 14 }}>作品</h3>
          {p.works.map((w) => (
            <div key={w.node} className="prog-out">
              <div className="muted">{w.title}</div>
              <Output o={w} />
            </div>
          ))}
        </>
      )}
    </div>
  )
}

export default function ChatPage() {
  const [params, setParams] = useSearchParams()
  const sid = params.get('session')
  const [sessions, setSessions] = useState([])
  const [s, setS] = useState(null)
  const [text, setText] = useState('')
  const [mode, setMode] = useState('auto')
  const [web, setWeb] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [tick, setTick] = useState(0)
  const bottom = useRef(null)

  const loadList = () => api('/chat/sessions').then(setSessions).catch(() => {})
  useEffect(() => { loadList() }, [])

  // load the open session, and poll it while its run is live
  useEffect(() => {
    if (!sid) { setS(null); return }
    let alive = true, timer
    const load = async () => {
      try {
        const d = await api(`/chat/sessions/${sid}`)
        if (!alive) return
        setS(d); setMode(d.mode); setWeb(d.allow_web); setErr(null)
        if (live(d) || d.busy) timer = setTimeout(load, 4000)
      } catch (e) { if (alive) setErr(e.message) }
    }
    load()
    return () => { alive = false; clearTimeout(timer) }
  }, [sid, tick])

  const count = s?.messages?.length || 0
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'end' }) }, [count, sid])

  const open = (id) => setParams(id ? { session: id } : {})
  const fresh = () => { open(null); setMode('auto'); setWeb(false); setText(''); setErr(null) }

  const pickMode = async (m) => {
    setMode(m)
    if (s) {
      try { setS(await api(`/chat/sessions/${s.id}`, { method: 'PATCH', body: JSON.stringify({ mode: m }) })) }
      catch (e) { setErr(e.message) }
    }
  }
  const pickWeb = async (v) => {
    setWeb(v)
    if (s) {
      try { setS(await api(`/chat/sessions/${s.id}`, { method: 'PATCH', body: JSON.stringify({ allow_web: v }) })) }
      catch (e) { setErr(e.message) }
    }
  }

  const send = async () => {
    const t = text.trim()
    if (!t || busy) return
    setBusy(true); setErr(null)
    try {
      let id = s?.id
      if (!id) {
        id = (await api('/chat/sessions', { method: 'POST', body: JSON.stringify({ mode, allow_web: web }) })).id
      }
      // show the user's message right away; Opus answers in a few seconds to a minute
      setS((cur) => ({ ...(cur || { id, mode, allow_web: web, messages: [] }),
        messages: [...(cur?.messages || []), { role: 'user', text: t, kind: 'text', t: Date.now() / 1000 }] }))
      setText('')
      const d = await api(`/chat/sessions/${id}/messages`, { method: 'POST', body: JSON.stringify({ text: t }) })
      setS(d)
      if (id !== sid) open(id)
      else setTick((x) => x + 1)
      loadList()
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  return (
    <div className="page">
      <style>{CSS}</style>
      <h1 className="h1-sm">对话创作</h1>
      <p className="sub">说想做什么。Agent 判断要图像、视频、音乐还是讲解视频，缺信息时一次问一个问题（最多 3 个），然后生成工作流并运行。做好后直接说要改哪里，例如「封面换成夜景」，只重跑相关节点。</p>
      <div className="chat-shell">
        <div className="chat-list">
          <button className="btn" onClick={fresh}>＋ 新对话</button>
          {sessions.map((x) => (
            <button key={x.id} className={`chat-item ${x.id === sid ? 'sel' : ''}`} onClick={() => open(x.id)}>
              {x.title || '新对话'}
              <span className="muted">{MODE_LABEL[x.mode] || x.mode} · {STATE_LABEL[x.state] || x.state} · {ago(x.updated)}</span>
            </button>
          ))}
          {sessions.length === 0 && <div className="muted">还没有对话。</div>}
        </div>

        <div className="chat-main">
          <div className="chat-msgs">
            {(!s || s.messages.length === 0) && (
              <div className="empty">例如：「做一张小红书封面，秋天的西湖」「给咖啡店写一首轻快的歌」「讲讲为什么天空是蓝色的」</div>
            )}
            {s?.messages.map((m, i) => (
              <div key={i} className={`msg ${m.role} ${m.kind || ''}`}>{m.text}</div>
            ))}
            {busy && <div className="msg assistant status">思考中…</div>}
            <div ref={bottom} />
          </div>

          {s?.progress && <Progress p={s.progress} onChange={() => setTick((x) => x + 1)} />}

          <div className="composer chat-input">
            <textarea value={text} onChange={(e) => setText(e.target.value)}
                      placeholder={s?.workflow ? '想改哪里？例如：封面换成夜景' : '想做什么？'}
                      onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send() } }} />
            <div className="row">
              <div className="chat-modes">
                {MODES.map(([k, label]) => (
                  <button key={k} className={`chat-mode ${mode === k ? 'sel' : ''}`} onClick={() => pickMode(k)}>{label}</button>
                ))}
              </div>
              <label className="toggle" title="目前还没有联网检索节点；关闭时 Agent 不会规划任何联网步骤">
                <input type="checkbox" checked={web} onChange={(e) => pickWeb(e.target.checked)} />
                <span />允许联网检索资料
              </label>
              <button className="primary" disabled={busy || !text.trim()} onClick={send}>
                {busy ? '发送中…' : '发送'} <span aria-hidden>→</span>
              </button>
            </div>
          </div>
          {err && <div className="error">{err}</div>}
        </div>
      </div>
    </div>
  )
}
