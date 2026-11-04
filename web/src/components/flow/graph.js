// Shared helpers for the workflow editor: port colours and type resolution (the same rules
// as server/app/workflows/validate.py), conversion between the workflow JSON and React
// Flow nodes/edges, and the topological-column auto layout.

export const PORT_COLORS = {
  text: '#6d625a', image: '#3d7a5a', audio: '#a8761c', video: '#c8452c',
  json: '#5d84a0', any: '#9a8f85',
}
export const portColor = (t) => PORT_COLORS[t] || PORT_COLORS.any

// Node groups in the order of DESIGN.md section 5 (节点类型); unknown groups go last.
export const GROUP_ORDER = ['输入', 'AI Agent', '图像', '视频', '音乐', '文本', '音频', '审核与质检', '输出']

export const COL_W = 260, ROW_H = 150

// Resolved type of an output port: "any" passthrough outputs take the type of the edge
// feeding the named input (validate.output_type).
export function outputType(nodes, edges, decl, nodeId, port, seen = new Set()) {
  const n = nodes.find((x) => x.id === nodeId)
  const d = n && decl[n.data.type]
  if (!d) return null
  const t = d.outputs[port]
  if (t !== 'any') return t
  const src = d.passthrough?.[port]
  const k = `${nodeId}.${port}`
  if (!src || seen.has(k)) return 'any'
  seen.add(k)
  const e = edges.find((x) => x.target === nodeId && x.targetHandle === src)
  return e ? outputType(nodes, edges, decl, e.source, e.sourceHandle, seen) : 'any'
}

// Why a new connection is refused, or '' when it is fine.
export function connectionProblem(c, nodes, edges, decl) {
  if (!c.source || !c.target || !c.sourceHandle || !c.targetHandle) return '端口不完整'
  if (c.source === c.target) return '不能把节点连到自己'
  const tn = nodes.find((x) => x.id === c.target)
  const want = tn && decl[tn.data.type]?.inputs[c.targetHandle]
  if (!want) return '没有这个输入端口'
  if (edges.some((e) => e.target === c.target && e.targetHandle === c.targetHandle)) {
    return `${c.target}.${c.targetHandle} 已经有连线`
  }
  const have = outputType(nodes, edges, decl, c.source, c.sourceHandle)
  if (want !== 'any' && have !== 'any' && have !== want) return `类型不匹配：${have} → ${want}`
  // a cycle: the target already reaches the source
  const seen = new Set(), todo = [c.target]
  while (todo.length) {
    const cur = todo.pop()
    if (cur === c.source) return '会形成环'
    if (seen.has(cur)) continue
    seen.add(cur)
    edges.filter((e) => e.source === cur).forEach((e) => todo.push(e.target))
  }
  return ''
}

export function levels(ids, edges) {
  const lv = Object.fromEntries(ids.map((i) => [i, 0]))
  // longest path from a source; ids not in a valid order (cycles) keep what they reached
  for (let pass = 0; pass < ids.length; pass++) {
    let changed = false
    for (const e of edges) {
      if (e.source in lv && e.target in lv && lv[e.target] < lv[e.source] + 1) {
        lv[e.target] = lv[e.source] + 1
        changed = true
      }
    }
    if (!changed) break
  }
  return lv
}

export function autoLayout(nodes, edges) {
  const lv = levels(nodes.map((n) => n.id), edges)
  const rows = {}
  return nodes.map((n) => {
    const c = lv[n.id] ?? 0
    const r = (rows[c] = (rows[c] ?? -1) + 1)
    return { ...n, position: { x: 20 + c * COL_W, y: 20 + r * ROW_H } }
  })
}

export function edgeStyle(type) {
  return { stroke: portColor(type), strokeWidth: 2 }
}

export function toFlow(wf, decl) {
  const nodes = (wf.nodes || []).map((n) => ({
    id: n.id, type: 'wf',
    position: n.ui && Number.isFinite(n.ui.x) ? { x: n.ui.x, y: n.ui.y } : { x: 0, y: 0 },
    data: { type: n.type, params: n.params || {} },
  }))
  const edges = (wf.edges || []).map((e, i) => {
    const [s, sh] = String(e.from).split('.'), [t, th] = String(e.to).split('.')
    return { id: `e${i}-${s}.${sh}-${t}.${th}`, source: s, sourceHandle: sh, target: t, targetHandle: th }
  })
  const placed = (wf.nodes || []).some((n) => n.ui && Number.isFinite(n.ui.x))
  const laid = placed ? nodes : autoLayout(nodes, edges)
  return { nodes: laid, edges: edges.map((e) => ({ ...e, style: edgeStyle(outputType(laid, edges, decl, e.source, e.sourceHandle)) })) }
}

export function toWorkflow(meta, nodes, edges) {
  return {
    ...meta,
    nodes: nodes.map((n) => ({
      id: n.id, type: n.data.type, params: n.data.params || {},
      ui: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
    })),
    edges: edges.map((e) => ({ from: `${e.source}.${e.sourceHandle}`, to: `${e.target}.${e.targetHandle}` })),
  }
}

export function nextId(nodes) {
  const used = new Set(nodes.map((n) => n.id))
  let k = 1
  while (used.has(`n${k}`)) k++
  return `n${k}`
}
