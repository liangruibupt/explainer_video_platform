// Shared previs UI bits without three.js, so JobPage's 「镜头」 tab stays out of the three chunk.

export const PCSS = `
.pv-mask { position: fixed; inset: 0; background: rgba(40,30,20,.32); z-index: 50; display: grid; place-items: center; padding: 20px; }
.pv-dlg { background: var(--card); border: 1px solid var(--line2); border-radius: 14px; padding: 18px 20px;
  width: min(880px, 96vw); max-height: 94vh; overflow: auto; box-shadow: 0 12px 40px rgba(40,30,20,.18); }
.pv-dlg h3 { font: 500 17px var(--serif); margin: 0; }
.pv-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.pv-head .x { margin-left: auto; border: 0; background: transparent; font-size: 20px; cursor: pointer; color: var(--ink3); }
.pv-tabs { display: flex; gap: 6px; margin-bottom: 14px; }
.pv-tabs button { border: 1px solid var(--line2); background: var(--card); border-radius: 99px; padding: 5px 14px; cursor: pointer; font-size: 13px; color: var(--ink2); }
.pv-tabs button.on { background: var(--accent); border-color: var(--accent); color: #fff; }
.pv-form { display: grid; gap: 10px; }
.pv-form textarea { width: 100%; min-height: 72px; resize: vertical; border: 1px solid var(--line2); border-radius: 10px; padding: 8px 10px; background: #fff; font: inherit; }
.pv-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.pv-row input[type=number] { width: 80px; border: 0; background: transparent; font: inherit; }
.pv-row .primary { margin-left: auto; padding: 7px 16px; font-size: 14px; }
.pv-issues { margin: 0; padding: 0; list-style: none; display: grid; gap: 4px; font-size: 12.5px; }
.pv-issues li { padding: 4px 8px; border-radius: 6px; border-left: 3px solid; }
.pv-issues li.error { background: #f8e3df; border-color: var(--bad); color: var(--bad); }
.pv-issues li.warn { background: #f7edd5; border-color: var(--warn); color: #7a5512; }
.pv-issues code { font: 11px var(--mono); margin-right: 6px; opacity: .8; }
.pv-spec { font-size: 13px; color: var(--ink2); display: flex; flex-wrap: wrap; gap: 4px 14px; }
.pv-imgs { display: grid; grid-template-columns: repeat(auto-fill, minmax(92px, 1fr)); gap: 8px; max-height: 210px; overflow: auto; }
.pv-imgs button { padding: 0; border: 2px solid transparent; border-radius: 8px; overflow: hidden; cursor: pointer; background: #1d1a17; aspect-ratio: 1; }
.pv-imgs button.on { border-color: var(--accent); }
.pv-imgs img { width: 100%; height: 100%; object-fit: cover; display: block; }
.pv-ok { font-size: 13px; color: var(--ok); }
details.pv-json pre { max-height: 220px; overflow: auto; font: 11px/1.45 var(--mono); background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 8px; }
`

export const issuesOf = (r) => (Array.isArray(r) ? r : r?.issues || [])

export function Issues({ issues }) {
  if (!issues?.length) return null
  const sorted = [...issues].sort((a, b) => (a.severity === 'error' ? -1 : 0) - (b.severity === 'error' ? -1 : 0))
  return (
    <ul className="pv-issues">
      {sorted.map((x, i) => (
        <li key={i} className={x.severity === 'error' ? 'error' : 'warn'}>
          <code>{x.code}</code>{x.message}
          {x.where && <span className="muted"> · {x.where}</span>}
          {x.frame != null && <span className="muted"> · 第 {x.frame} 帧</span>}
        </li>
      ))}
    </ul>
  )
}

const SOURCE = { parametric: '参数化建模', image3d: '图生 3D', upload: '上传' }
export const sourceLabel = (a) => SOURCE[a?.source?.type] || ''

