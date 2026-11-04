import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap, addEdge,
  applyEdgeChanges, applyNodeChanges, useReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { api } from '../api.js'
import WfNode from '../components/flow/WfNode.jsx'
import Palette, { DND_TYPE } from '../components/flow/Palette.jsx'
import Inspector from '../components/flow/Inspector.jsx'
import {
  autoLayout, connectionProblem, edgeStyle, nextId, outputType, PORT_COLORS, toFlow, toWorkflow,
} from '../components/flow/graph.js'

// Drag-and-drop workflow editor (DESIGN.md sections 5 and 9) at /workflow/edit/:id, where
// id is a saved workflow or template id, or "new". Node positions are kept in the
// workflow JSON as an optional ui: {x, y} per node (the validator ignores it).

const NODE_TYPES = { wf: WfNode }
const ID_RE = /^[A-Za-z][A-Za-z0-9_]{0,31}$/
const stamp = () => new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)

const CSS = `
.fe-page { max-width: none; padding: 24px 28px 40px; }
.fe-top { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
.fe-top input.fe-name { font: 500 20px var(--serif); border: 1px solid transparent; background: transparent;
  border-radius: 8px; padding: 2px 6px; min-width: 260px; }
.fe-top input.fe-name:hover, .fe-top input.fe-name:focus { border-color: var(--line2); background: var(--card); }
.fe-top .fe-wid { font: 12px var(--mono); color: var(--ink3); }
.fe-top .spacer { flex: 1; }
.fe-layout { display: grid; grid-template-columns: 210px minmax(0, 1fr) 300px; gap: 14px; align-items: start; }
.fe-canvas { height: calc(100vh - 170px); min-height: 520px; background: var(--card); border: 1px solid var(--line);
  border-radius: 12px; overflow: hidden; }
.fe-palette { max-height: calc(100vh - 170px); overflow: auto; padding: 12px; }
.fe-pgroup { font-size: 11px; color: var(--ink3); margin: 12px 0 4px; letter-spacing: .5px; }
.fe-pitem { border: 1px solid var(--line2); background: #fff; border-radius: 8px; padding: 5px 8px; margin-bottom: 5px;
  cursor: grab; font-size: 12.5px; }
.fe-pitem:hover { border-color: var(--accent); }
.fe-pdots { display: flex; gap: 3px; align-items: center; font-size: 10px; color: var(--ink3); }
.fe-pdots i { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.fe-node { background: #fff; border: 1.4px solid var(--line2); border-radius: 10px; width: 200px; font-size: 12px; }
.fe-node.sel { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft); }
.fe-node.bad { border-color: var(--bad); border-style: dashed; background: #fdf3f1; }
.fe-head { display: flex; justify-content: space-between; padding: 5px 9px 0; font: 10.5px var(--mono); color: var(--ink3); }
.fe-label { font-weight: 600; padding: 0 9px 6px; color: var(--ink); }
.fe-cand { margin-left: 6px; font-weight: 400; font-size: 10.5px; color: var(--accent); }
.fe-ports { position: relative; border-top: 1px solid var(--line); margin-bottom: 4px; }
.fe-port { position: absolute; height: 20px; line-height: 20px; font: 10.5px var(--mono); color: var(--ink2); width: 50%; }
.fe-port.in { left: 0; padding-left: 10px; }
.fe-port.out { right: 0; padding-right: 10px; text-align: right; }
.fe-port em { font-style: normal; color: var(--ink3); font-size: 9.5px; }
.fe-port .react-flow__handle { width: 10px; height: 10px; border: 2px solid #fff; }
.fe-err { color: var(--bad); font-size: 11px; padding: 4px 9px 6px; border-top: 1px dashed #e3b6af; }
.fe-msg { font-size: 13px; }
.fe-legend { display: flex; gap: 10px; font-size: 11px; color: var(--ink3); margin-top: 8px; flex-wrap: wrap; }
.fe-legend i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 3px; }
@media (max-width: 1100px) { .fe-layout { grid-template-columns: 1fr; } }
`

function Editor() {
  const { id } = useParams()
  const nav = useNavigate()
  const rf = useReactFlow()
  const wrap = useRef(null)
  const [decls, setDecls] = useState(null)
  const [meta, setMeta] = useState(null) // workflow fields other than nodes/edges
  const [nodes, setNodes] = useState([])
  const [edges, setEdges] = useState([])
  const [sel, setSel] = useState(null)
  const [errors, setErrors] = useState(null) // last validate result (null = not checked)
  const [msg, setMsg] = useState(null) // {kind: 'ok'|'error', text}
  const [busy, setBusy] = useState('')
  const [dirty, setDirty] = useState(false)
  const decl = useMemo(() => Object.fromEntries((decls || []).map((d) => [d.type, d])), [decls])

  useEffect(() => { api('/workflows/nodes').then(setDecls).catch((e) => setMsg({ kind: 'error', text: e.message })) }, [])
  useEffect(() => {
    if (!decls) return
    const d = Object.fromEntries(decls.map((x) => [x.type, x]))
    const load = id === 'new'
      ? Promise.resolve({ id: `wf_${stamp()}`, name: '新工作流', nodes: [], edges: [] })
      : api(`/workflows/${encodeURIComponent(id)}`).then((wf) => wf.template
        ? { ...wf, id: `${wf.id}_${stamp()}`, from_template: wf.id, template: undefined }
        : wf)
    load.then((wf) => {
      const { nodes: wn, edges: we, template, saved, ...rest } = wf // eslint-disable-line no-unused-vars
      const f = toFlow(wf, d)
      setMeta(rest); setNodes(f.nodes); setEdges(f.edges); setErrors(null); setSel(null); setDirty(false)
      setTimeout(() => rf.fitView({ padding: 0.15 }), 50)
    }).catch((e) => setMsg({ kind: 'error', text: e.message }))
  }, [id, decls]) // eslint-disable-line react-hooks/exhaustive-deps

  // errors per node id, shown on the node and in the inspector
  const byNode = useMemo(() => {
    const m = {}
    for (const e of errors || []) if (e.node) (m[e.node] = m[e.node] || []).push(e.message)
    return m
  }, [errors])
  const viewNodes = useMemo(() => nodes.map((n) => ({
    ...n, data: { ...n.data, decl: decl[n.data.type], errors: byNode[n.id] },
  })), [nodes, decl, byNode])

  const touch = () => { setDirty(true); setErrors(null) }
  const onNodesChange = useCallback((ch) => {
    setNodes((ns) => applyNodeChanges(ch, ns))
    if (ch.some((c) => c.type === 'remove' || (c.type === 'position' && !c.dragging))) setDirty(true)
    const s = ch.find((c) => c.type === 'select' && c.selected)
    if (s) setSel(s.id)
    if (ch.some((c) => c.type === 'remove' && c.id === sel)) setSel(null)
    if (ch.some((c) => c.type === 'remove')) setErrors(null)
  }, [sel])
  const onEdgesChange = useCallback((ch) => {
    setEdges((es) => applyEdgeChanges(ch, es))
    if (ch.some((c) => c.type === 'remove')) touch()
  }, [])
  const isValidConnection = useCallback((c) => !connectionProblem(c, nodes, edges, decl), [nodes, edges, decl])
  const onConnect = useCallback((c) => {
    const why = connectionProblem(c, nodes, edges, decl)
    if (why) { setMsg({ kind: 'error', text: `不能连线：${why}` }); return }
    const t = outputType(nodes, edges, decl, c.source, c.sourceHandle)
    setEdges((es) => addEdge({ ...c, id: `e-${c.source}.${c.sourceHandle}-${c.target}.${c.targetHandle}`, style: edgeStyle(t) }, es))
    setMsg(null); touch()
  }, [nodes, edges, decl])

  const addNode = (type, position) => {
    const nid = nextId(nodes)
    const pos = position || rf.screenToFlowPosition({
      x: (wrap.current?.getBoundingClientRect().left || 0) + 200,
      y: (wrap.current?.getBoundingClientRect().top || 0) + 120,
    })
    setNodes((ns) => [...ns.map((n) => ({ ...n, selected: false })),
      { id: nid, type: 'wf', position: pos, selected: true, data: { type, params: {} } }])
    setSel(nid); touch()
  }
  const onDragOver = (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move' }
  const onDrop = (e) => {
    e.preventDefault()
    const type = e.dataTransfer.getData(DND_TYPE)
    if (!type || !decl[type]) return
    addNode(type, rf.screenToFlowPosition({ x: e.clientX - 100, y: e.clientY - 20 }))
  }

  const onParam = (nid, k, v) => {
    setNodes((ns) => ns.map((n) => n.id !== nid ? n : {
      ...n, data: { ...n.data, params: Object.fromEntries(Object.entries({ ...n.data.params, [k]: v }).filter(([, x]) => x !== undefined && x !== '')) },
    }))
    touch()
  }
  const onDelete = (nid) => {
    setNodes((ns) => ns.filter((n) => n.id !== nid))
    setEdges((es) => es.filter((e) => e.source !== nid && e.target !== nid))
    setSel(null); touch()
  }
  const onRename = (old, nid) => {
    if (!ID_RE.test(nid) || nodes.some((n) => n.id === nid)) {
      setMsg({ kind: 'error', text: `节点 id ${nid} 无效或已存在（字母开头，只用字母、数字、下划线）` })
      return
    }
    setNodes((ns) => ns.map((n) => n.id === old ? { ...n, id: nid } : n))
    setEdges((es) => es.map((e) => ({ ...e, source: e.source === old ? nid : e.source, target: e.target === old ? nid : e.target })))
    setSel(nid); touch()
  }
  const layout = () => {
    setNodes((ns) => autoLayout(ns, edges)); setDirty(true)
    setTimeout(() => rf.fitView({ padding: 0.15 }), 50)
  }

  const workflow = () => toWorkflow(meta, nodes, edges)
  const act = async (label, fn) => {
    setBusy(label); setMsg(null)
    try { await fn() } catch (e) { setMsg({ kind: 'error', text: e.message }) }
    setBusy('')
  }
  const validate = () => act('validate', async () => {
    const r = await api('/workflows/validate', { method: 'POST', body: JSON.stringify({ workflow: workflow() }) })
    setErrors(r.errors)
    setMsg(r.valid ? { kind: 'ok', text: '校验通过' } : { kind: 'error', text: `${r.errors.length} 个校验错误，已在节点上标出` })
    return r
  })
  const save = () => act('save', async () => {
    const r = await api('/workflows', { method: 'POST', body: JSON.stringify({ workflow: workflow() }) })
    setMeta((m) => ({ ...m, id: r.id })); setDirty(false)
    setMsg({ kind: 'ok', text: `已保存为 ${r.id}` })
    if (r.id !== id) nav(`/workflow/edit/${r.id}`, { replace: true })
  })
  const run = () => act('run', async () => {
    const wf = workflow()
    const v = await api('/workflows/validate', { method: 'POST', body: JSON.stringify({ workflow: wf }) })
    setErrors(v.errors)
    if (!v.valid) { setMsg({ kind: 'error', text: `${v.errors.length} 个校验错误，改好再运行` }); return }
    const r = await api('/wf_runs', { method: 'POST', body: JSON.stringify({ workflow: wf }) })
    nav(`/workflow?run=${r.id}`)
  })

  const selNode = nodes.find((n) => n.id === sel)
  const general = (errors || []).filter((e) => !e.node)

  return (
    <div className="page fe-page">
      <style>{CSS}</style>
      <div className="fe-top">
        <button className="btn" onClick={() => nav('/workflow')}>← 工作流</button>
        {meta && <input className="fe-name" value={meta.name || ''} onChange={(e) => { setMeta({ ...meta, name: e.target.value }); setDirty(true) }} />}
        {meta && <span className="fe-wid">{meta.id}{meta.from_template ? ` · 来自模板 ${meta.from_template}` : ''}{dirty ? ' · 未保存' : ''}</span>}
        <span className="spacer" />
        <button className="btn" onClick={layout} disabled={!nodes.length}>自动布局</button>
        <button className="btn" onClick={validate} disabled={!!busy || !meta}>{busy === 'validate' ? '校验中…' : '校验'}</button>
        <button className="btn" onClick={save} disabled={!!busy || !meta}>{busy === 'save' ? '保存中…' : '保存'}</button>
        <button className="primary" style={{ marginLeft: 0 }} onClick={run} disabled={!!busy || !nodes.length}>{busy === 'run' ? '提交中…' : '运行'}</button>
      </div>
      {msg && <div className={msg.kind === 'error' ? 'error fe-msg' : 'muted fe-msg'} style={{ marginBottom: 10 }}>{msg.text}</div>}
      {general.length > 0 && <div className="error fe-msg" style={{ marginBottom: 10 }}>{general.map((e, i) => <div key={i}>{e.message}</div>)}</div>}
      <div className="fe-layout">
        <Palette decls={decls} onAdd={(t) => addNode(t)} />
        <div className="fe-canvas" ref={wrap} onDragOver={onDragOver} onDrop={onDrop}>
          <ReactFlow nodes={viewNodes} edges={edges} nodeTypes={NODE_TYPES}
                     onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
                     onConnect={onConnect} isValidConnection={isValidConnection}
                     onPaneClick={() => setSel(null)} deleteKeyCode={['Delete', 'Backspace']}
                     fitView minZoom={0.2} maxZoom={1.6} proOptions={{ hideAttribution: true }}>
            <Background gap={18} color="#e3d9cc" />
            <MiniMap pannable zoomable nodeColor={(n) => (byNode[n.id] ? '#b3362b' : '#d6c9b8')} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        <div>
          <Inspector node={selNode} decl={decl} errors={selNode && byNode[selNode.id]}
                     onParam={onParam} onDelete={onDelete} onRename={onRename} />
          <div className="fe-legend">
            {Object.entries(PORT_COLORS).map(([t, c]) => (
              <span key={t}><i style={{ background: c }} />{t}</span>
            ))}
          </div>
          <p className="hint">端口颜色表示类型；类型不同的端口连不上（any 可以接任何类型）。图像、视频、音乐生成节点可以把「候选数」设成 2–4，运行到这里会暂停，审核时选一个往下走。</p>
        </div>
      </div>
    </div>
  )
}

export default function WorkflowEditor() {
  return <ReactFlowProvider><Editor /></ReactFlowProvider>
}
