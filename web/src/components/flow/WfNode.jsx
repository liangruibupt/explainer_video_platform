import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import { portColor } from './graph.js'

// One workflow node on the canvas: a handle per declared input (left) and output (right),
// coloured by port type. data.decl is the node declaration, data.errors the validator
// messages that point at this node.
function WfNode({ id, data, selected }) {
  const d = data.decl
  const ins = Object.entries(d?.inputs || {})
  const outs = Object.entries(d?.outputs || {})
  const rows = Math.max(ins.length, outs.length, 1)
  const bad = data.errors?.length > 0
  const nCand = data.params?.candidates > 1 ? data.params.candidates : 0
  return (
    <div className={`fe-node ${selected ? 'sel' : ''} ${bad ? 'bad' : ''}`} title={bad ? data.errors.join('\n') : d?.doc || ''}>
      <div className="fe-head">
        <span className="fe-id">{id}</span>
        <span className="fe-group">{d?.group || '?'}</span>
      </div>
      <div className="fe-label">{d?.label || data.type}{nCand ? <span className="fe-cand">候选 × {nCand}</span> : null}</div>
      <div className="fe-ports" style={{ height: rows * 20 }}>
        {ins.map(([p, t], i) => (
          <div key={`i-${p}`} className="fe-port in" style={{ top: i * 20 }}>
            <Handle type="target" position={Position.Left} id={p}
                    style={{ background: portColor(t), top: 10 }} />
            <span>{p}{d.optional?.includes(p) ? '?' : ''} <em>{t}</em></span>
          </div>
        ))}
        {outs.map(([p, t], i) => (
          <div key={`o-${p}`} className="fe-port out" style={{ top: i * 20 }}>
            <span><em>{t}</em> {p}</span>
            <Handle type="source" position={Position.Right} id={p}
                    style={{ background: portColor(t), top: 10 }} />
          </div>
        ))}
      </div>
      {bad && <div className="fe-err">{data.errors[0]}{data.errors.length > 1 ? ` 等 ${data.errors.length} 个` : ''}</div>}
    </div>
  )
}

export default memo(WfNode)
