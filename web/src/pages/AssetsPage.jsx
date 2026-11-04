import { useEffect, useRef, useState } from 'react'
import { api, ago } from '../api.js'

const KINDS = [['', '全部'], ['image', '图片'], ['audio', '音频'], ['video', '视频']]
const ACCEPT = '.png,.jpg,.jpeg,.webp,.wav,.mp3,.flac,.m4a,.mp4,.m4v,.mov,.webm'
const MAX = 200 * 1024 * 1024

const CSS = `
.al-drop { border: 1.5px dashed var(--line2); border-radius: 12px; padding: 22px; text-align: center;
  color: var(--ink2); background: var(--card); cursor: pointer; transition: border-color .15s, background .15s; }
.al-drop.over { border-color: var(--accent); background: var(--accent-soft); }
.al-drop small { display: block; margin-top: 4px; color: var(--ink3); }
.al-tabs { display: flex; gap: 6px; margin: 18px 0 12px; flex-wrap: wrap; align-items: center; }
.al-tabs button { border: 1px solid var(--line2); background: var(--card); border-radius: 99px;
  padding: 4px 14px; cursor: pointer; font-size: 13px; color: var(--ink2); }
.al-tabs button.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.al-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; }
.al-card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; overflow: hidden;
  display: flex; flex-direction: column; }
.al-media { aspect-ratio: 16 / 10; background: #efe8de; display: flex; align-items: center; justify-content: center; }
.al-media img, .al-media video { width: 100%; height: 100%; object-fit: contain; background: #1d1a17; }
.al-media audio { width: 92%; }
.al-body { padding: 10px 12px 12px; display: flex; flex-direction: column; gap: 6px; }
.al-name { font-weight: 500; word-break: break-all; }
.al-name input { width: 100%; font: inherit; padding: 2px 6px; border: 1px solid var(--line2); border-radius: 6px; }
.al-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.al-actions .btn { padding: 3px 9px; font-size: 12px; }
.al-actions select { font-size: 12px; border: 1px solid var(--line2); border-radius: 8px; background: var(--card); padding: 2px 4px; max-width: 140px; }
.al-up { font-size: 12.5px; color: var(--ink2); margin-top: 8px; }
.al-up .error { margin-top: 4px; }
`

function fmtSize(n) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function Preview({ a }) {
  if (a.kind === 'image') return <a href={a.url} target="_blank" rel="noreferrer"><img src={a.thumb_url || a.url} alt="" loading="lazy" /></a>
  if (a.kind === 'video') return <video src={a.url} poster={a.thumb_url || undefined} controls preload="none" />
  return <audio src={a.url} controls preload="none" />
}

function AssetCard({ a, projects, onChange, onError }) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(a.name)
  const [copied, setCopied] = useState(false)
  const patch = async (body) => {
    try { onChange(await api(`/assets/${a.id}`, { method: 'PATCH', body: JSON.stringify(body) })) } catch (e) { onError(e.message) }
  }
  const save = async () => {
    setEditing(false)
    if (name.trim() && name !== a.name) await patch({ name: name.trim() })
    else setName(a.name)
  }
  const remove = async () => {
    if (!window.confirm(`删除素材「${a.name}」？文件会被永久删除。`)) return
    try { await api(`/assets/${a.id}`, { method: 'DELETE' }); onChange(null, a.id) } catch (e) { onError(e.message) }
  }
  const copy = async () => {
    try { await navigator.clipboard.writeText(a.id) } catch { window.prompt('素材 id', a.id) }
    setCopied(true); setTimeout(() => setCopied(false), 1500)
  }
  const dims = a.width ? `${a.width}×${a.height}` : ''
  const dur = a.duration ? `${a.duration.toFixed(1)} 秒` : ''
  return (
    <div className="al-card">
      <div className="al-media"><Preview a={a} /></div>
      <div className="al-body">
        <div className="al-name">
          {editing
            ? <input autoFocus value={name} maxLength={200} onChange={(e) => setName(e.target.value)}
                onBlur={save} onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') { setName(a.name); setEditing(false) } }} />
            : <span title="点击改名" onClick={() => setEditing(true)} style={{ cursor: 'text' }}>{a.name}</span>}
        </div>
        <div className="muted">{a.format} · {[dims, dur].filter(Boolean).join(' · ')}{dims || dur ? ' · ' : ''}{fmtSize(a.size)} · {ago(a.created)}</div>
        <div className="al-actions">
          <button className="btn" onClick={copy}>{copied ? '已复制' : '复制素材 id'}</button>
          <button className="btn" onClick={() => setEditing(true)}>改名</button>
          <select value={a.project_id || ''} onChange={(e) => patch({ project_id: e.target.value })} title="移到项目">
            <option value="">未归入项目</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <button className="btn danger" onClick={remove}>删除</button>
        </div>
      </div>
    </div>
  )
}

export default function AssetsPage() {
  const [kind, setKind] = useState('')
  const [assets, setAssets] = useState(null)
  const [projects, setProjects] = useState([])
  const [project, setProject] = useState('')
  const [err, setErr] = useState(null)
  const [over, setOver] = useState(false)
  const [uploads, setUploads] = useState([])
  const input = useRef(null)

  const load = () => {
    const q = new URLSearchParams()
    if (kind) q.set('kind', kind)
    api(`/assets${q.toString() ? `?${q}` : ''}`).then((xs) => { setAssets(xs); setErr(null) }).catch((e) => setErr(e.message))
  }
  useEffect(load, [kind]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api('/projects').then(setProjects).catch(() => {}) }, [])

  const send = async (file) => {
    const key = `${file.name}-${Date.now()}-${Math.random()}`
    const set = (st) => setUploads((u) => u.map((x) => (x.key === key ? { ...x, ...st } : x)))
    setUploads((u) => [{ key, name: file.name, status: '上传中…' }, ...u].slice(0, 8))
    if (file.size > MAX) { set({ status: '失败', error: '文件超过 200 MB' }); return }
    const fd = new FormData()
    fd.append('file', file)
    if (project) fd.append('project_id', project)
    try {
      const r = await fetch('/api/assets', { method: 'POST', body: fd })
      if (!r.ok) {
        let msg = `${r.status}`
        try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail) } catch { /* not json */ }
        throw new Error(msg)
      }
      set({ status: '完成' })
      load()
    } catch (e) { set({ status: '失败', error: e.message }) }
  }
  const sendAll = (files) => { [...files].forEach(send) }

  const onChange = (m, removedId) => {
    setAssets((xs) => (removedId ? xs.filter((x) => x.id !== removedId) : xs.map((x) => (x.id === m.id ? m : x))))
  }
  const shown = (assets || []).filter((a) => !project || a.project_id === project)

  return (
    <div className="page">
      <style>{CSS}</style>
      <h1 className="h1-sm">素材库</h1>
      <p className="sub">上传图片、音频、视频，在工作流里用「素材输入」节点按素材 id 引用。也可以在项目页把已有作品导入进来。</p>
      <div className={`al-drop ${over ? 'over' : ''}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); sendAll(e.dataTransfer.files) }}>
        把文件拖到这里，或点击选择
        <small>png / jpg / webp · wav / mp3 / flac / m4a · mp4 / mov / webm，单个不超过 200 MB{project ? '，上传后归入当前项目' : ''}</small>
        <input ref={input} type="file" multiple accept={ACCEPT} hidden onChange={(e) => { sendAll(e.target.files); e.target.value = '' }} />
      </div>
      {uploads.length > 0 && (
        <div className="al-up">
          {uploads.map((u) => (
            <div key={u.key}>{u.name}：{u.status}{u.error && <div className="error">{u.error}</div>}</div>
          ))}
        </div>
      )}
      <div className="al-tabs">
        {KINDS.map(([k, label]) => (
          <button key={k} className={kind === k ? 'on' : ''} onClick={() => setKind(k)}>{label}</button>
        ))}
        <span style={{ flex: 1 }} />
        <label className="opt">项目
          <select value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="">全部</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
      </div>
      {err && <div className="error">{err}</div>}
      {assets && shown.length === 0 && <div className="empty">还没有素材。</div>}
      <div className="al-grid">
        {shown.map((a) => <AssetCard key={a.id} a={a} projects={projects} onChange={onChange} onError={setErr} />)}
      </div>
    </div>
  )
}
