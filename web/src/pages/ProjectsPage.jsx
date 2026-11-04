import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, ago } from '../api.js'

const CSS = `
.pj-wrap { display: grid; grid-template-columns: 280px 1fr; gap: 20px; align-items: start; }
@media (max-width: 820px) { .pj-wrap { grid-template-columns: 1fr; } }
.pj-list { display: flex; flex-direction: column; gap: 6px; }
.pj-item { text-align: left; border: 1px solid var(--line); background: var(--card); border-radius: 10px;
  padding: 8px 12px; cursor: pointer; font: inherit; color: inherit; }
.pj-item.on { border-color: var(--accent); background: var(--accent-soft); }
.pj-new { display: flex; gap: 6px; margin-bottom: 10px; }
.pj-new input, .pj-head input, .pj-note { flex: 1; font: inherit; padding: 6px 10px; border: 1px solid var(--line2); border-radius: 10px; background: var(--card); }
.pj-note { width: 100%; min-height: 54px; resize: vertical; box-sizing: border-box; }
.pj-head { display: flex; gap: 8px; align-items: center; margin-bottom: 10px; }
.pj-head h2 { font: 500 19px var(--serif); margin: 0; flex: 1; }
.pj-sec { margin-top: 18px; }
.pj-sec h3 { font: 500 15px var(--serif); margin: 0 0 8px; }
.pj-rows { display: flex; flex-direction: column; gap: 6px; }
.pj-row { display: flex; align-items: center; gap: 10px; background: var(--card); border: 1px solid var(--line);
  border-radius: 10px; padding: 6px 10px; }
.pj-row img { width: 64px; height: 40px; object-fit: cover; border-radius: 6px; background: #1d1a17; }
.pj-row .t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pj-row .btn { padding: 3px 9px; font-size: 12px; }
`

const GROUPS = [
  ['jobs', '讲解视频', 'job', (x) => `/jobs/${x.id}`],
  ['images', '图像', 'image', () => '/image'],
  ['videos', 'AI 视频', 'video', () => '/video'],
  ['musics', '音乐', 'music', () => '/music'],
  ['wf_runs', '工作流运行', 'wf_run', () => '/workflow'],
]
const KIND = { image: '图片', audio: '音频', video: '视频' }

function Detail({ id, onRenamed, onDeleted }) {
  const [p, setP] = useState(null)
  const [items, setItems] = useState(null)
  const [name, setName] = useState('')
  const [note, setNote] = useState('')
  const [err, setErr] = useState(null)
  const [msg, setMsg] = useState(null)
  const load = () => {
    api(`/projects/${id}`).then((x) => { setP(x); setName(x.name); setNote(x.note || '') }).catch((e) => setErr(e.message))
    api(`/projects/${id}/items`).then(setItems).catch((e) => setErr(e.message))
  }
  useEffect(() => { setErr(null); setMsg(null); load() }, [id]) // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (body) => {
    try { const x = await api(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify(body) }); setP(x); onRenamed(x) } catch (e) { setErr(e.message) }
  }
  const remove = async () => {
    if (!window.confirm(`删除项目「${p.name}」？项目里的素材会保留在素材库，只是不再归入任何项目。`)) return
    try { await api(`/projects/${id}`, { method: 'DELETE' }); onDeleted(id) } catch (e) { setErr(e.message) }
  }
  const importItem = async (source, x, file) => {
    setMsg(null)
    try {
      await api('/assets/import', { method: 'POST', body: JSON.stringify({ source, id: x.id, file: file || '', project_id: id }) })
      setMsg(`已把 ${x.title} 导入素材库`); load()
    } catch (e) { setErr(e.message) }
  }
  const unassign = async (a) => {
    try { await api(`/assets/${a.id}`, { method: 'PATCH', body: JSON.stringify({ project_id: '' }) }); load() } catch (e) { setErr(e.message) }
  }
  if (!p) return err ? <div className="error">{err}</div> : <div className="muted">加载中…</div>
  return (
    <div>
      <div className="pj-head">
        <input value={name} maxLength={200} onChange={(e) => setName(e.target.value)}
          onBlur={() => name.trim() && name !== p.name && save({ name: name.trim() })}
          onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }} title="改名" />
        <button className="btn danger" onClick={remove}>删除项目</button>
      </div>
      <textarea className="pj-note" placeholder="备注" value={note} maxLength={2000}
        onChange={(e) => setNote(e.target.value)} onBlur={() => note !== (p.note || '') && save({ note })} />
      <div className="muted">创建于 {ago(p.created)} · id {p.id}</div>
      {err && <div className="error">{err}</div>}
      {msg && <div className="muted" style={{ marginTop: 6 }}>{msg}</div>}
      {items && (
        <>
          <div className="pj-sec">
            <h3>素材（{items.assets.length}）</h3>
            {items.assets.length === 0 && <div className="empty">还没有素材。在素材库上传时选这个项目，或把下面的作品导入。</div>}
            <div className="pj-rows">
              {items.assets.map((a) => (
                <div className="pj-row" key={a.id}>
                  {a.thumb_url ? <img src={a.thumb_url} alt="" /> : <span className="pill">{KIND[a.kind]}</span>}
                  <a className="t" href={a.url} target="_blank" rel="noreferrer">{a.name}</a>
                  <span className="muted">{KIND[a.kind]} · {ago(a.created)}</span>
                  <button className="btn" onClick={() => unassign(a)}>移出项目</button>
                </div>
              ))}
            </div>
            <div className="muted" style={{ marginTop: 6 }}><Link to="/library">去素材库管理</Link></div>
          </div>
          {GROUPS.map(([key, label, source, href]) => (items[key] || []).length > 0 && (
            <div className="pj-sec" key={key}>
              <h3>{label}（{items[key].length}）</h3>
              <div className="pj-rows">
                {items[key].map((x) => (
                  <div className="pj-row" key={x.id}>
                    <Link className="t" to={href(x)}>{x.title}</Link>
                    {x.status && <span className={`pill ${x.status}`}>{x.status}</span>}
                    <span className="muted">{x.created ? ago(x.created) : ''}</span>
                    {x.status === 'done' && (source === 'job' || (x.files || []).length > 0) && (
                      <button className="btn" onClick={() => importItem(source, x, source === 'job' ? '' : x.files[0])}>导入素材库</button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  )
}

export default function ProjectsPage() {
  const [list, setList] = useState(null)
  const [name, setName] = useState('')
  const [err, setErr] = useState(null)
  const [params, setParams] = useSearchParams()
  const sel = params.get('id') || ''
  const load = () => api('/projects').then((xs) => { setList(xs); setErr(null) }).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  const select = (id) => setParams(id ? { id } : {})
  const create = async () => {
    if (!name.trim()) return
    try {
      const p = await api('/projects', { method: 'POST', body: JSON.stringify({ name: name.trim() }) })
      setName(''); await load(); select(p.id)
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="page">
      <style>{CSS}</style>
      <h1 className="h1-sm">项目</h1>
      <p className="sub">把素材和作品归到一个项目里。讲解视频、图像、视频、音乐和工作流运行带上项目后也会出现在这里。</p>
      {err && <div className="error">{err}</div>}
      <div className="pj-wrap">
        <div>
          <div className="pj-new">
            <input placeholder="新项目名称" value={name} maxLength={200} onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') create() }} />
            <button className="btn" onClick={create} disabled={!name.trim()}>新建</button>
          </div>
          {list && list.length === 0 && <div className="empty">还没有项目。</div>}
          <div className="pj-list">
            {(list || []).map((p) => (
              <button key={p.id} className={`pj-item ${sel === p.id ? 'on' : ''}`} onClick={() => select(p.id)}>
                <div>{p.name}</div>
                <div className="muted">{p.asset_count || 0} 个素材 · {ago(p.created)}</div>
              </button>
            ))}
          </div>
        </div>
        <div>
          {sel
            ? <Detail key={sel} id={sel}
                onRenamed={(x) => setList((xs) => xs.map((p) => (p.id === x.id ? { ...p, ...x } : p)))}
                onDeleted={() => { select(''); load() }} />
            : <div className="empty">选一个项目查看内容。</div>}
        </div>
      </div>
    </div>
  )
}
