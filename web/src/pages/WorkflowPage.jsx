import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, usePoll, ago } from '../api.js'

// Workflow page (DESIGN.md sections 5-6): templates, one-sentence generation, a graph view
// (nodes in topological columns), a param inspector built from the node declarations,
// run with live per-node status, review / retry, the AI 助手 and the outputs.

const NODE_W = 148, NODE_H = 66, COL_W = 172, ROW_H = 80, PAD = 12
const RUN_LABEL = {
  pending: '等待', running: '运行中', waiting_review: '待审核', done: '完成',
  failed: '失败', cached: '缓存命中',
}
const RUN_STATUS = { running: '运行中', waiting_review: '等待审核', done: '已完成', failed: '失败' }
const liveRun = (r) => r && r.status === 'running'

function mediaKind(v) {
  if (typeof v !== 'string') return null
  if (v.endsWith('.png')) return 'image'
  if (v.endsWith('.mp4')) return 'video'
  if (/\.(flac|mp3|wav|m4a)$/.test(v)) return 'audio'
  return null
}

function Media({ src, kind }) {
  if (kind === 'image') return <img className="wf-media" src={src} alt="" />
  if (kind === 'video') return <video className="wf-media" src={src} controls preload="metadata" />
  if (kind === 'audio') return <audio src={src} controls preload="metadata" style={{ width: '100%' }} />
  return null
}

function layout(wf, levels) {
  const cols = {}
  for (const n of wf.nodes) {
    const c = levels?.[n.id] ?? 0
    ;(cols[c] = cols[c] || []).push(n.id)
  }
  const pos = {}
  let rows = 1
  Object.entries(cols).forEach(([c, ids]) => {
    rows = Math.max(rows, ids.length)
    ids.forEach((id, i) => { pos[id] = { x: PAD + c * COL_W, y: PAD + i * ROW_H } })
  })
  const ncol = Math.max(1, Object.keys(cols).length)
  return { pos, w: PAD * 2 + (ncol - 1) * COL_W + NODE_W, h: PAD * 2 + (rows - 1) * ROW_H + NODE_H }
}

function Graph({ wf, levels, decl, run, selected, onSelect, errors }) {
  const { pos, w, h } = useMemo(() => layout(wf, levels), [wf, levels])
  const bad = new Set((errors || []).map((e) => e.node).filter(Boolean))
  return (
    <div className="wf-graph">
      <svg width={w} height={h}>
        {wf.edges.map((e, i) => {
          const [f] = e.from.split('.'), [t] = e.to.split('.')
          if (!pos[f] || !pos[t]) return null
          const x1 = pos[f].x + NODE_W, y1 = pos[f].y + NODE_H / 2
          const x2 = pos[t].x, y2 = pos[t].y + NODE_H / 2
          const mx = (x1 + x2) / 2
          return (
            <g key={i}>
              <path d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} className="wf-edge" />
              <title>{`${e.from} → ${e.to}`}</title>
            </g>
          )
        })}
        {wf.nodes.map((n) => {
          const p = pos[n.id]
          const st = run?.nodes?.[n.id]?.status
          const d = decl[n.type]
          return (
            <g key={n.id} transform={`translate(${p.x},${p.y})`} onClick={() => onSelect(n.id)}
               className={`wf-node ${st || ''} ${selected === n.id ? 'sel' : ''} ${bad.has(n.id) ? 'bad' : ''}`}>
              <rect width={NODE_W} height={NODE_H} rx="10" />
              <text x="10" y="17" className="wf-nid">{n.id} · {d?.group || ''}</text>
              <text x="10" y="35" className="wf-nlabel">{(d?.label || n.type).slice(0, 11)}</text>
              <text x="10" y="49" className="wf-port">{d ? `${Object.keys(d.inputs).join(',') || '–'} → ${Object.keys(d.outputs).join(',') || '–'}`.slice(0, 24) : ''}</text>
              <text x="10" y="61" className="wf-nstate">
                {st ? RUN_LABEL[st] : n.type}{run?.nodes?.[n.id]?.seconds != null ? ` · ${Math.round(run.nodes[n.id].seconds)}s` : ''}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function ParamField({ name, schema, value, onChange }) {
  const v = value ?? schema.default ?? ''
  const label = schema.label || name
  if (schema.enum) {
    return (
      <label className="wf-field"><span>{label}</span>
        <select value={v} onChange={(e) => onChange(e.target.value)}>
          {schema.enum.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </label>
    )
  }
  if (schema.type === 'boolean') {
    return (
      <label className="wf-field wf-check"><span>{label}</span>
        <input type="checkbox" checked={!!v} onChange={(e) => onChange(e.target.checked)} />
      </label>
    )
  }
  if (schema.type === 'integer' || schema.type === 'number') {
    return (
      <label className="wf-field"><span>{label}</span>
        <input type="number" value={v} min={schema.min} max={schema.max}
               step={schema.type === 'integer' ? 1 : 'any'}
               onChange={(e) => onChange(e.target.value === '' ? undefined
                 : schema.type === 'integer' ? parseInt(e.target.value, 10) : parseFloat(e.target.value))} />
      </label>
    )
  }
  const long = String(v).length > 40 || ['text', 'instruction', 'lyrics', 'brief', 'prompt'].includes(name)
  return (
    <label className="wf-field"><span>{label}{schema.required ? ' *' : ''}</span>
      {long
        ? <textarea value={v} rows={3} onChange={(e) => onChange(e.target.value)} />
        : <input value={v} onChange={(e) => onChange(e.target.value)} />}
    </label>
  )
}

function Value({ runId, nid, port, v }) {
  const k = mediaKind(v)
  if (k) return <Media kind={k} src={`/api/wf_runs/${runId}/file?node=${nid}&port=${port}`} />
  if (typeof v === 'string') return <div className="pre wf-text">{v}</div>
  return <div className="pre wf-text">{JSON.stringify(v, null, 1)}</div>
}

function Inspector({ wf, nid, decl, run, onParam, onReview, onRetry }) {
  const n = wf.nodes.find((x) => x.id === nid)
  const [note, setNote] = useState('')
  if (!n) return <div className="panel"><div className="muted">点图里的节点，在这里改参数、看结果。</div></div>
  const d = decl[n.type]
  const rs = run?.nodes?.[nid]
  return (
    <div className="panel">
      <h3>{n.id} · {d?.label || n.type}</h3>
      <div className="muted" style={{ fontSize: 12 }}>{n.type} · {d?.runner}{d?.doc ? ` · ${d.doc}` : ''}</div>
      {d && Object.entries(d.params).map(([k, s]) => (
        <ParamField key={k} name={k} schema={s} value={n.params?.[k]} onChange={(v) => onParam(nid, k, v)} />
      ))}
      {d && Object.keys(d.params).length === 0 && <div className="muted">没有参数。</div>}
      {rs && (
        <div className="wf-result">
          <div><span className={`pill ${rs.status === 'cached' ? 'done' : rs.status}`}>{RUN_LABEL[rs.status]}</span>
            {rs.seconds != null && <span className="muted"> 用时 {rs.seconds} 秒</span>}
            {rs.note && <span className="muted"> · {rs.note}</span>}</div>
          {rs.error && <div className="error">{rs.error}</div>}
          {rs.status === 'waiting_review' && rs.candidates?.length > 0 && (
            <>
              <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>{rs.candidates.length} 个候选，选一个往下走</div>
              <input className="wf-note" placeholder="审核意见（可空）" value={note} onChange={(e) => setNote(e.target.value)} />
              {rs.candidates.map((c) => (
                <div key={c.k} className="wf-cand">
                  <div className="muted" style={{ fontSize: 12 }}>候选 {c.k + 1} · seed {c.seed}{c.cached ? ' · 缓存' : ''}</div>
                  {Object.keys(c.outputs).map((p) => (
                    <Value key={p} runId={run.id} nid={nid} port={`c${c.k}.${p}`} v={c.outputs[p]} />
                  ))}
                  <div className="actions"><button className="btn" onClick={() => onReview(nid, 'approve', note, c.k)}>选这个</button></div>
                </div>
              ))}
              <div className="actions"><button className="btn danger" onClick={() => onReview(nid, 'reject', note)}>都不要（打回）</button></div>
            </>
          )}
          {rs.status === 'waiting_review' && !rs.candidates?.length && (
            <>
              <Value runId={run.id} nid={nid} port="input" v={rs.review_input} />
              <input className="wf-note" placeholder="审核意见（可空）" value={note} onChange={(e) => setNote(e.target.value)} />
              <div className="actions">
                <button className="btn" onClick={() => onReview(nid, 'approve', note)}>批准</button>
                <button className="btn danger" onClick={() => onReview(nid, 'reject', note)}>打回</button>
              </div>
            </>
          )}
          {rs.status === 'failed' && <div className="actions"><button className="btn" onClick={() => onRetry(nid)}>单独重跑这个节点</button></div>}
          {rs.status === 'done' && rs.candidates?.length > 1 && (
            <div className="actions"><button className="btn" onClick={() => onRetry(nid)}>重新选候选（{rs.candidates.length} 个，已选候选 {rs.choice + 1}）</button></div>
          )}
          {rs.outputs && !(rs.status === 'waiting_review' && rs.candidates?.length) && Object.entries(rs.outputs).map(([p, v]) => (
            <div key={p}><div className="muted" style={{ fontSize: 12, marginTop: 8 }}>{p}</div>
              <Value runId={run.id} nid={nid} port={p} v={v} /></div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function WorkflowPage() {
  const [decls] = usePoll(() => api('/workflows/nodes'), 0, () => false, [])
  const [templates] = usePoll(() => api('/workflows/templates'), 0, () => false, [])
  const [wf, setWf] = useState(null)
  const [check, setCheck] = useState({ errors: [], levels: {} })
  const [sel, setSel] = useState(null)
  const [runId, setRunId] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState(null)
  const [prompt, setPrompt] = useState('')
  const [instr, setInstr] = useState('')
  const [proposal, setProposal] = useState(null)
  const [genInfo, setGenInfo] = useState(null)
  const [tick, setTick] = useState(0)
  const decl = useMemo(() => Object.fromEntries((decls || []).map((d) => [d.type, d])), [decls])
  const [run] = usePoll(() => (runId ? api(`/wf_runs/${runId}`) : Promise.resolve(null)), 2500,
    liveRun, [runId, tick])
  const [runs] = usePoll(() => api('/wf_runs'), 8000, () => true, [tick])
  const [saved] = usePoll(() => api('/workflows'), 0, () => false, [tick])
  const nav = useNavigate()
  const [search] = useSearchParams()
  const linkedRun = search.get('run')

  useEffect(() => { if (!wf && !linkedRun && templates?.length) setWf(templates.find((t) => t.id === 'wf_mv_from_one_line') || templates[0]) }, [templates]) // eslint-disable-line
  useEffect(() => {
    if (!wf) return
    const t = setTimeout(() => api('/workflows/validate', { method: 'POST', body: JSON.stringify({ workflow: wf }) })
      .then(setCheck).catch((e) => setErr(e.message)), 250)
    return () => clearTimeout(t)
  }, [wf])

  const act = async (label, fn) => {
    setBusy(label); setErr(null)
    try { await fn() } catch (e) { setErr(e.message) }
    setBusy('')
  }
  const pick = (t) => { setWf(structuredClone(t)); setSel(null); setRunId(null); setProposal(null); setGenInfo(null) }
  const onParam = (nid, k, v) => setWf((w) => ({ ...w, nodes: w.nodes.map((n) => n.id !== nid ? n
    : { ...n, params: Object.fromEntries(Object.entries({ ...n.params, [k]: v }).filter(([, x]) => x !== undefined)) }) }))
  const generate = () => act('gen', async () => {
    const r = await api('/workflows/generate', { method: 'POST', body: JSON.stringify({ prompt }) })
    setGenInfo({ rounds: r.rounds, valid: r.valid, errors: r.errors })
    if (r.workflow) { setWf(r.workflow); setSel(null); setRunId(null) }
  })
  const edit = () => act('edit', async () => {
    const r = await api('/workflows/edit', { method: 'POST', body: JSON.stringify({ workflow: wf, instruction: instr }) })
    setProposal(r)
  })
  const start = () => act('run', async () => {
    const r = await api('/wf_runs', { method: 'POST', body: JSON.stringify({ workflow: wf }) })
    setRunId(r.id); setTick((t) => t + 1)
  })
  const save = () => act('save', async () => { await api('/workflows', { method: 'POST', body: JSON.stringify({ workflow: wf }) }); setTick((t) => t + 1) })
  const review = (nid, decision, note, choice) => act('review', async () => {
    await api(`/wf_runs/${runId}/review`, { method: 'POST', body: JSON.stringify({ node_id: nid, decision, note, ...(choice != null ? { choice } : {}) }) })
    setTick((t) => t + 1)
  })
  const retry = (nid) => act('retry', async () => {
    await api(`/wf_runs/${runId}/retry`, { method: 'POST', body: JSON.stringify({ node_id: nid }) })
    setTick((t) => t + 1)
  })
  const openRun = (id) => act('open', async () => {
    const r = await api(`/wf_runs/${id}`)
    setWf(r.workflow); setRunId(id); setSel(null); setProposal(null)
  })
  useEffect(() => { if (linkedRun) openRun(linkedRun) }, [linkedRun]) // eslint-disable-line
  // Open the current workflow in the drag-and-drop editor: templates and saved workflows
  // by id; anything else (generated, edited here, opened from a run) is saved first.
  const editCurrent = () => act('edit-open', async () => {
    const isTemplate = (templates || []).some((t) => t.id === wf.id && JSON.stringify(t) === JSON.stringify(wf))
    if (isTemplate) { nav(`/workflow/edit/${wf.id}`); return }
    const r = await api('/workflows', { method: 'POST', body: JSON.stringify({ workflow: wf }) })
    nav(`/workflow/edit/${r.id}`)
  })

  const waiting = run ? Object.entries(run.nodes).filter(([, n]) => n.status === 'waiting_review').map(([k]) => k) : []
  const hits = run ? Object.values(run.nodes).filter((n) => n.status === 'cached').length : 0

  return (
    <div className="page wf-page">
      <h1 className="h1-sm">工作流</h1>
      <p className="sub">用节点图编排生成步骤：上游结果按「节点类型 + 参数 + 输入」缓存，改了下游参数重跑时上游直接用缓存，不再调 GPU。
        人工审核节点会暂停运行，等你批准或打回。</p>

      <div className="quick"><span>快捷开始</span>
        {(templates || []).map((t) => (
          <span key={t.id} className="wf-tpl">
            <button className={`chip ${wf?.id === t.id ? 'on' : ''}`} onClick={() => pick(t)}>{t.name}</button>
            <button className="chip wf-edit" title="在拖拽编辑器里打开（另存为新工作流）" onClick={() => nav(`/workflow/edit/${t.id}`)}>编辑</button>
          </span>
        ))}
        <button className="chip" onClick={() => nav('/workflow/edit/new')}>＋ 新建空白工作流</button>
      </div>
      {(saved || []).length > 0 && (
        <div className="quick" style={{ marginTop: 8 }}><span>已保存</span>
          {saved.map((s) => (
            <span key={s.id} className="wf-tpl">
              <button className={`chip ${wf?.id === s.id ? 'on' : ''}`} onClick={() => pick(s)}>{s.name || s.id}</button>
              <button className="chip wf-edit" onClick={() => nav(`/workflow/edit/${s.id}`)}>编辑</button>
            </span>
          ))}
        </div>
      )}
      <style>{'.wf-tpl { display: inline-flex; gap: 2px; } .chip.wf-edit { padding-left: 8px; padding-right: 8px; font-size: 12px; color: var(--ink3); } .wf-cand { border-top: 1px dashed var(--line); margin-top: 8px; padding-top: 6px; }'}</style>

      <div className="composer" style={{ marginTop: 16 }}>
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} style={{ minHeight: 48 }}
                  placeholder="用一句话生成工作流，例如：给一首古风纯音乐配一张封面" />
        <div className="row">
          {genInfo && <span className="muted">{genInfo.valid ? `通过校验（${genInfo.rounds} 轮）` : `${genInfo.rounds} 轮后仍有 ${genInfo.errors.length} 个错误`}</span>}
          <button className="primary" disabled={!!busy || prompt.trim().length < 2} onClick={generate}>
            {busy === 'gen' ? 'Agent 生成中…' : '生成工作流'} <span aria-hidden>→</span></button>
        </div>
      </div>
      {err && <div className="error">{err}</div>}

      {wf && (
        <>
          <div className="section-head">
            <h2>{wf.name} {check.errors?.length ? <span className="pill failed">{check.errors.length} 个校验错误</span> : <span className="pill done">校验通过</span>}</h2>
            <div className="actions" style={{ marginTop: 0 }}>
              <button className="btn" onClick={editCurrent} disabled={!!busy}>在编辑器里打开</button>
              <button className="btn" onClick={save} disabled={!!busy}>保存</button>
              <button className="primary" style={{ marginLeft: 0 }} disabled={!!busy || !!check.errors?.length || liveRun(run)} onClick={start}>
                {busy === 'run' ? '提交中…' : runId ? '重新运行（用缓存）' : '运行'}</button>
            </div>
          </div>
          {wf.description && <p className="sub" style={{ marginTop: -6 }}>{wf.description}</p>}
          {check.errors?.length > 0 && <div className="error">{check.errors.map((e, i) => <div key={i}>{e.message}</div>)}</div>}
          {run && (
            <div className="muted" style={{ margin: '8px 0' }}>
              运行 {run.id} · <span className={`pill ${run.status}`}>{RUN_STATUS[run.status]}</span>
              {hits > 0 && ` · ${hits} 个节点缓存命中`}
              {waiting.length > 0 && <> · <button className="btn" onClick={() => setSel(waiting[0])}>去审核 {waiting[0]}</button></>}
            </div>
          )}
          <div className="wf-grid">
            <Graph wf={wf} levels={check.levels} decl={decl} run={run} selected={sel} onSelect={setSel} errors={check.errors} />
            <div className="wf-side">
              <Inspector wf={wf} nid={sel} decl={decl} run={run} onParam={onParam} onReview={review} onRetry={retry} />
              <div className="panel" style={{ marginTop: 14 }}>
                <h3>AI 助手</h3>
                <textarea className="wf-note" rows={2} value={instr} onChange={(e) => setInstr(e.target.value)}
                          placeholder="例如：在视频后面加一个人工审核节点" />
                <div className="actions"><button className="btn" disabled={!!busy || instr.trim().length < 2} onClick={edit}>
                  {busy === 'edit' ? '修改中…' : '让 Agent 改图'}</button></div>
                {proposal && (
                  <div className="wf-result">
                    <div className="muted">{proposal.valid ? `通过校验（${proposal.rounds} 轮）` : `仍有错误：${proposal.errors.map((e) => e.message).join('；')}`}</div>
                    <div className="pre wf-text">{proposal.diff?.summary}</div>
                    <div className="actions">
                      <button className="btn" disabled={!proposal.valid} onClick={() => { setWf(proposal.workflow); setProposal(null); setRunId(null) }}>应用改动</button>
                      <button className="btn" onClick={() => setProposal(null)}>放弃</button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {run?.works?.length > 0 && (
        <>
          <div className="section-head"><h2>作品</h2></div>
          <div className="grid">
            {run.works.map((w) => (
              <div className="card" key={w.node}>
                {w.file ? <Media kind={mediaKind(w.file)} src={`/api/wf_runs/${run.id}/file?node=${w.node}&work=${w.file}`} />
                  : <div className="pre wf-text">{JSON.stringify(w.value)}</div>}
                <div className="card-body"><div className="title">{w.title}</div></div>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="section-head"><h2>运行记录</h2></div>
      {(runs || []).length === 0 ? <div className="empty">还没有运行过工作流。</div> : (
        <ul className="wf-runs">
          {runs.map((r) => (
            <li key={r.id} onClick={() => openRun(r.id)} className={r.id === runId ? 'on' : ''}>
              <span className={`pill ${r.status}`}>{RUN_STATUS[r.status] || r.status}</span>
              <b>{r.name}</b>
              <span className="muted">{Object.entries(r.counts).map(([k, v]) => `${RUN_LABEL[k]} ${v}`).join(' · ')}</span>
              <span className="muted" style={{ marginLeft: 'auto' }}>{ago(r.created)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
