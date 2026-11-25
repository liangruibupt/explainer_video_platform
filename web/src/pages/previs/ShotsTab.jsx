import { useState } from 'react'
import { usePoll } from '../../api.js'
import { papi, withMock } from './mock.js'
import { Issues, issuesOf, PCSS } from './ui.jsx'

// 「镜头」 tab of a previs3d job (docs/PREVIS_CONTRACT.md §12): one row per shot with the 5
// stills, the top-down plan, the check report, the standalone clip and a per-shot re-render
// (GET /api/jobs/{id}/previs, POST /api/jobs/{id}/previs/{scene}/render).

const CSS = `
.ps-list { display: grid; gap: 14px; }
.ps-row { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; display: grid; gap: 10px; }
.ps-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ps-head b { font: 500 15px var(--serif); }
.ps-head .btn { margin-left: auto; padding: 4px 12px; font-size: 12.5px; }
.ps-body { display: grid; grid-template-columns: minmax(260px, 1.25fr) minmax(0, 2fr) 150px; gap: 12px; align-items: start; }
.ps-clip { width: 100%; aspect-ratio: 16 / 9; background: #1d1a17; border-radius: 8px; display: block; }
.ps-clip.tall { aspect-ratio: 9 / 16; max-height: 360px; }
.ps-ph { width: 100%; aspect-ratio: 16 / 9; border-radius: 8px; border: 1px dashed var(--line2); display: grid; place-items: center;
  color: var(--ink3); font-size: 12.5px; text-align: center; padding: 10px; }
.ps-stills { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; }
.ps-stills a { display: block; border-radius: 6px; overflow: hidden; background: #e9e6e1; aspect-ratio: 16 / 9; }
.ps-stills img { width: 100%; height: 100%; object-fit: cover; display: block; }
.ps-plan { display: block; width: 100%; aspect-ratio: 1; border-radius: 8px; border: 1px solid var(--line); background: #f6f1ea; object-fit: contain; }
.ps-cap { font-size: 11px; color: var(--ink3); margin-top: 3px; }
.ps-ok { font-size: 12.5px; color: var(--ok); }
@media (max-width: 1000px) { .ps-body { grid-template-columns: 1fr; } .ps-plan { max-width: 220px; } }
`

const ST = {
  queued: ['排队中', 'queued'], running: ['渲染中', 'running'], done: ['已渲染', 'done'],
  failed: ['未渲染', 'failed'], blocked: ['检查未通过', 'failed'],
}
const live = (s) => s === 'queued' || s === 'running'

function ShotRow({ jobId, shot, title, tall, onRendered }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const issues = issuesOf(shot.report)
  const errors = issues.filter((x) => x.severity === 'error').length
  const warns = issues.length - errors
  const [label, cls] = ST[shot.status] || [shot.status || '未知', '']
  const rerender = async () => {
    setBusy(true); setErr(null)
    try {
      await withMock(() => papi(`/jobs/${jobId}/previs/${shot.scene}/render`, { method: 'POST' }), 'render_shot', jobId, shot.scene)
      onRendered()
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }
  const stills = shot.stills || []
  return (
    <div className="ps-row">
      <div className="ps-head">
        <b>{shot.scene}{title ? ` · ${title}` : ''}</b>
        <span className={`pill ${cls}`}>{label}</span>
        {errors > 0 && <span className="pill failed">{errors} 个错误</span>}
        {warns > 0 && <span className="pill running">{warns} 个警告</span>}
        <button className="btn" disabled={busy || live(shot.status)} onClick={rerender}
                title="只重新编译、检查、渲染这一个镜头，成片要重新合成">
          {live(shot.status) ? '渲染中…' : busy ? '提交中…' : '重渲这个镜头'}
        </button>
      </div>
      <div className="ps-body">
        <div>
          {shot.clip_url && shot.status === 'done'
            ? <video key={shot.clip_url} className={`ps-clip ${tall ? 'tall' : ''}`} src={shot.clip_url} poster={stills[0]} controls preload="metadata" />
            : <div className="ps-ph">{live(shot.status) ? '正在渲染这个镜头…' : errors ? '检查有错误，这个镜头没有渲染。改好镜头后点「重渲这个镜头」。' : '还没有独立片段'}</div>}
          <div className="ps-cap">独立片段（不带字幕和配音）</div>
        </div>
        <div>
          {stills.length > 0
            ? <div className="ps-stills">
                {stills.map((u, i) => <a key={u} href={u} target="_blank" rel="noreferrer" title={`静帧 ${i + 1}`}><img src={u} alt={`静帧 ${i + 1}`} loading="lazy" /></a>)}
              </div>
            : <div className="ps-ph" style={{ aspectRatio: 'auto', minHeight: 70 }}>还没有静帧</div>}
          <div className="ps-cap">5 张静帧（版面检查用）</div>
          <div style={{ marginTop: 8 }}>
            {issues.length ? <Issues issues={issues} /> : <div className="ps-ok">检查通过，没有错误和警告</div>}
          </div>
        </div>
        <div>
          {shot.plan_png ? <a href={shot.plan_png} target="_blank" rel="noreferrer"><img className="ps-plan" src={shot.plan_png} alt="俯视路径图" /></a> : <div className="ps-ph">没有俯视图</div>}
          <div className="ps-cap">俯视路径图：相机路径（颜色 = 时间）、关键帧编号、看向点</div>
        </div>
      </div>
      {err && <div className="error">{err}</div>}
    </div>
  )
}

export default function ShotsTab({ job }) {
  const [tick, setTick] = useState(0)
  const [data, error] = usePoll(() => withMock(() => papi(`/jobs/${job.id}/previs`), 'previs', job.id), 3000,
    (d) => (d?.shots || []).some((s) => live(s.status)), [job.id, tick])
  const titles = Object.fromEntries((job.scenes || []).map((s) => [s.id, s.title]))
  return (
    <div>
      <style>{PCSS}</style>
      <style>{CSS}</style>
      {error && !data && <div className="error">{error}</div>}
      {!data && !error && <p className="sub">加载镜头…</p>}
      {data && (data.shots || []).length === 0 && <div className="empty">还没有镜头。规划和镜头编译完成后出现在这里。</div>}
      <div className="ps-list">
        {(data?.shots || []).map((s) => (
          <ShotRow key={s.scene} jobId={job.id} shot={s} title={titles[s.scene]} tall={job.ratio === '9:16'}
                   onRendered={() => setTick((t) => t + 1)} />
        ))}
      </div>
    </div>
  )
}
