import { useState } from 'react'
import { Link } from 'react-router-dom'

// 不满意 button (docs/PHASE5_CONTRACTS.md, UI). Opens a small dialog with six reason chips
// and one line of text, POSTs /api/feedback, then shows 已记录 with a link to the item.

export const REASONS = ['画面', '声音', '字幕', '内容', '节奏', '其他']
const MAX = 500

const CSS = `
.fb-btn { border: 1px solid var(--line2); background: var(--card); border-radius: 10px; padding: 5px 12px;
  cursor: pointer; font-size: 12.5px; color: var(--ink2); }
.fb-btn:hover { border-color: var(--bad); color: var(--bad); }
.fb-done { font-size: 12.5px; color: var(--ok); }
.fb-done a { color: var(--accent); margin-left: 6px; }
.fb-mask { position: fixed; inset: 0; background: rgba(40,30,20,.28); z-index: 50; display: grid; place-items: center; }
.fb-dlg { background: var(--card); border: 1px solid var(--line2); border-radius: 14px; padding: 18px 20px;
  width: min(440px, 92vw); box-shadow: 0 12px 40px rgba(40,30,20,.18); }
.fb-dlg h3 { font: 500 16px var(--serif); margin: 0 0 4px; }
.fb-dlg .chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
.fb-dlg input { width: 100%; border: 1px solid var(--line2); border-radius: 8px; padding: 7px 10px; background: #fff; font-size: 13px; }
.fb-dlg .cnt { text-align: right; font-size: 11px; color: var(--ink3); margin-top: 2px; }
`

export default function FeedbackButton({ target }) {
  const [open, setOpen] = useState(false)
  const [reasons, setReasons] = useState([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [done, setDone] = useState(null)

  const toggle = (r) => setReasons((xs) => (xs.includes(r) ? xs.filter((x) => x !== r) : [...xs, r]))
  const close = () => { setOpen(false); setErr(null) }
  const submit = async () => {
    if (!reasons.length) { setErr('至少选一个原因'); return }
    setBusy(true); setErr(null)
    try {
      const t = Object.fromEntries(Object.entries(target).filter(([, v]) => v != null && v !== ''))
      const r = await fetch('/api/feedback', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: t, reasons, text: text.slice(0, MAX) }),
      })
      if (r.status === 501 || r.status === 404) throw new Error('反馈功能还没有接上后端，暂时无法记录')
      if (!r.ok) {
        let msg = `提交失败（${r.status}）`
        try { const d = (await r.json()).detail; if (d) msg = typeof d === 'string' ? d : JSON.stringify(d) } catch { /* not json */ }
        throw new Error(msg)
      }
      const item = await r.json()
      setDone(item); setOpen(false)
    } catch (e) {
      setErr(e.message === 'Failed to fetch' ? '网络错误，没有提交成功' : e.message)
    }
    setBusy(false)
  }

  return (
    <>
      <style>{CSS}</style>
      {done
        ? <span className="fb-done">已记录{done.id && <Link to={`/feedback?item=${done.id}`}>查看分析 →</Link>}</span>
        : <button type="button" className="fb-btn" onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen(true) }}>不满意</button>}
      {open && (
        <div className="fb-mask" onClick={close}>
          <div className="fb-dlg" role="dialog" aria-label="不满意的原因" onClick={(e) => e.stopPropagation()}>
            <h3>哪里不满意？</h3>
            <div className="muted">可多选。系统会分析原因：偶发问题直接重跑，规则问题起草改进（需要人工合并才生效）。</div>
            <div className="chips">
              {REASONS.map((r) => (
                <button key={r} type="button" className={`chip ${reasons.includes(r) ? 'on' : ''}`}
                        aria-pressed={reasons.includes(r)} onClick={() => toggle(r)}>{r}</button>
              ))}
            </div>
            <input value={text} maxLength={MAX} onChange={(e) => setText(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter') submit() }}
                   placeholder="一句话说明（可空），例如：字幕太快看不过来" />
            <div className="cnt">{text.length}/{MAX}</div>
            {err && <div className="error">{err}</div>}
            <div className="actions">
              <button type="button" className="btn" onClick={close}>取消</button>
              <button type="button" className="primary" style={{ padding: '7px 18px', fontSize: 13 }}
                      disabled={busy} onClick={submit}>{busy ? '提交中…' : '提交'}</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
