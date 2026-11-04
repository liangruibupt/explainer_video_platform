import { useState } from 'react'
import { GROUP_ORDER, portColor } from './graph.js'

export const DND_TYPE = 'application/x-evp-node'

// Node palette grouped like DESIGN.md section 5; drag an item onto the canvas (or click it
// to add it at the centre).
export default function Palette({ decls, onAdd }) {
  const [q, setQ] = useState('')
  const groups = {}
  for (const d of decls || []) {
    if (q && !`${d.label} ${d.type}`.toLowerCase().includes(q.toLowerCase())) continue
    ;(groups[d.group] = groups[d.group] || []).push(d)
  }
  const names = Object.keys(groups).sort((a, b) => {
    const ia = GROUP_ORDER.indexOf(a), ib = GROUP_ORDER.indexOf(b)
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib)
  })
  return (
    <div className="fe-palette panel">
      <h3 style={{ marginTop: 0 }}>节点库</h3>
      <input className="wf-note" style={{ marginTop: 0 }} placeholder="搜索节点" value={q} onChange={(e) => setQ(e.target.value)} />
      {names.map((g) => (
        <div key={g}>
          <div className="fe-pgroup">{g}</div>
          {groups[g].map((d) => (
            <div key={d.type} className="fe-pitem" draggable title={d.doc || d.type}
                 onDragStart={(e) => { e.dataTransfer.setData(DND_TYPE, d.type); e.dataTransfer.effectAllowed = 'move' }}
                 onDoubleClick={() => onAdd(d.type)}>
              <div>{d.label}</div>
              <div className="fe-pdots">
                {Object.values(d.inputs).map((t, i) => <i key={`i${i}`} style={{ background: portColor(t) }} />)}
                <span>→</span>
                {Object.values(d.outputs).map((t, i) => <i key={`o${i}`} style={{ background: portColor(t) }} />)}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
