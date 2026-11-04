// Param inspector: a form built from the node's param schema (nodes.py: type, default,
// enum, min, max, required, required_unless_input, label).
function ParamField({ name, schema, value, onChange }) {
  const v = value ?? schema.default ?? ''
  const label = `${schema.label || name}${schema.required ? ' *' : ''}`
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
      <label className="wf-field"><span>{label}{schema.min != null || schema.max != null ? ` (${schema.min ?? ''}–${schema.max ?? ''})` : ''}</span>
        <input type="number" value={v} min={schema.min} max={schema.max}
               step={schema.type === 'integer' ? 1 : 'any'}
               onChange={(e) => onChange(e.target.value === '' ? undefined
                 : schema.type === 'integer' ? parseInt(e.target.value, 10) : parseFloat(e.target.value))} />
      </label>
    )
  }
  const long = String(v).length > 40 || ['text', 'instruction', 'lyrics', 'brief', 'prompt', 'style'].includes(name)
  return (
    <label className="wf-field"><span>{label}</span>
      {long
        ? <textarea value={v} rows={3} onChange={(e) => onChange(e.target.value)} />
        : <input value={v} onChange={(e) => onChange(e.target.value)} />}
    </label>
  )
}

export default function Inspector({ node, decl, errors, onParam, onDelete, onRename }) {
  if (!node) {
    return <div className="panel"><div className="muted">选中画布上的节点，在这里改参数。从左边拖节点到画布上添加；从输出端口拖到输入端口连线；选中后按 Delete 删除。</div></div>
  }
  const d = decl[node.data.type]
  return (
    <div className="panel">
      <h3 style={{ marginTop: 0 }}>{d?.label || node.data.type}</h3>
      <div className="muted" style={{ fontSize: 12 }}>{node.data.type} · {d?.runner}{d?.doc ? ` · ${d.doc}` : ''}</div>
      <label className="wf-field"><span>节点 id</span>
        <input defaultValue={node.id} key={node.id}
               onBlur={(e) => e.target.value !== node.id && onRename(node.id, e.target.value)} />
      </label>
      {errors?.length > 0 && <div className="error" style={{ marginTop: 10 }}>{errors.map((m, i) => <div key={i}>{m}</div>)}</div>}
      {d && Object.entries(d.params).map(([k, s]) => (
        <ParamField key={`${node.id}-${k}`} name={k} schema={s} value={node.data.params?.[k]}
                    onChange={(v) => onParam(node.id, k, v)} />
      ))}
      {d && Object.keys(d.params).length === 0 && <div className="muted" style={{ marginTop: 10 }}>没有参数。</div>}
      <div className="actions"><button className="btn danger" onClick={() => onDelete(node.id)}>删除节点</button></div>
    </div>
  )
}
