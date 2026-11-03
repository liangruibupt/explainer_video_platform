import { Link } from 'react-router-dom'
import { api, usePoll, isLive, ago, STATUS } from '../api.js'

export default function Tasks() {
  const [jobs, error] = usePoll(() => api('/jobs'), 3000, (js) => js.some(isLive), [])
  return (
    <div className="page">
      <h1 className="h1-sm">任务中心</h1>
      <p className="sub">所有制作任务，包括正在进行和失败的。</p>
      {error && <div className="error">{error}</div>}
      {jobs && jobs.length === 0 && <div className="empty">还没有任务。</div>}
      <table className="tasks">
        <tbody>
          {(jobs || []).map((j) => {
            const cur = j.steps.find((s) => s.status === 'running')
            const n = j.steps.filter((s) => s.status === 'done' || s.status === 'skipped').length
            return (
              <tr key={j.id}>
                <td><Link to={`/jobs/${j.id}`}>{j.topic}</Link></td>
                <td><span className={`pill ${j.status}`}>{STATUS[j.status]}</span></td>
                <td className="muted">{cur ? cur.label : j.status === 'failed' ? (j.error || '').split('\n')[0].slice(0, 60) : ''}</td>
                <td className="muted">{n}/{j.steps.length}</td>
                <td className="muted">{ago(j.created)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
