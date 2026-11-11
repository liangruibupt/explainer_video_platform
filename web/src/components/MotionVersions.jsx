import { api, usePoll, ago, fileUrl } from '../api.js'

// Versions of a Remotion job (docs/REMOTION_CONTRACT.md §7): v1 = final.mp4, v2+ = saved
// from the motion editor. Polls while any version is still queued or rendering.

export const VERSION_STATUS = { queued: '排队中', rendering: '渲染中', done: '已完成', failed: '失败' }
export const versionLive = (v) => v && (v.status === 'queued' || v.status === 'rendering')
export const versionUrl = (id, n) => `/api/jobs/${id}/versions/${n}/video`

const when = (t) => {
  if (typeof t === 'number') return ago(t)
  if (!t) return ''
  const d = new Date(t)
  return Number.isNaN(d.getTime()) ? String(t) : ago(d.getTime() / 1000)
}

export function VersionWarnings({ warnings }) {
  if (!warnings?.length) return null
  return (
    <ul className="mv-warn">
      {warnings.map((w, i) => <li key={i}>{w}</li>)}
    </ul>
  )
}

export default function MotionVersions({ id, tall }) {
  const [list, error] = usePoll(() => api(`/jobs/${id}/versions`), 4000,
                                (vs) => Array.isArray(vs) && vs.some(versionLive), [id])
  if (error && !list) return <p className="muted">版本列表暂时读不到：{error}</p>
  if (!list) return <p className="muted">读取版本…</p>
  const versions = [...list].sort((a, b) => b.n - a.n)
  return (
    <div className="mv-list">
      {versions.map((v) => (
        <div key={v.n} className="mv-item">
          <div className="mv-head">
            <b>v{v.n}</b>
            <span className="muted">{v.n === 1 ? '原片' : '编辑器保存'}{v.created ? ` · ${when(v.created)}` : ''}</span>
            <span className={`pill ${v.status === 'rendering' ? 'running' : v.status}`}>{VERSION_STATUS[v.status] || v.status}</span>
            {v.status === 'done' && <a className="more mv-dl" href={versionUrl(id, v.n)} download={`v${v.n}.mp4`}>下载</a>}
          </div>
          {v.status === 'done'
            ? <video className={`player mv-player ${tall ? 'tall' : ''}`} src={versionUrl(id, v.n)}
                     poster={fileUrl(id, 'poster.jpg')} controls preload="metadata" />
            : v.status === 'failed'
              ? <pre className="error pre">{v.error || '渲染失败'}</pre>
              : <div className="mv-wait muted">正在渲染这个版本，完成后在这里播放</div>}
          <VersionWarnings warnings={v.warnings} />
        </div>
      ))}
    </div>
  )
}
