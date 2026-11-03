import { Link } from 'react-router-dom'
import { fileUrl, ago, STATUS } from '../api.js'

export default function WorkCard({ job }) {
  const done = job.status === 'done'
  const doneSteps = (job.steps || []).filter((s) => s.status === 'done' || s.status === 'skipped').length
  const total = (job.steps || []).length || 1
  const cur = (job.steps || []).find((s) => s.status === 'running')
  return (
    <Link to={`/jobs/${job.id}`} className="card">
      <div className={`thumb ${job.ratio === '9:16' ? 'tall' : ''}`}>
        {done
          ? <img src={fileUrl(job.id, 'poster.jpg')} alt="" loading="lazy" />
          : <div className={`placeholder ${job.status}`}>
              {job.status === 'failed' ? '制作失败'
                : <>
                    <div className="bar"><i style={{ width: `${(doneSteps / total) * 100}%` }} /></div>
                    <small>{cur ? cur.label : STATUS[job.status]}</small>
                  </>}
            </div>}
        {done && job.duration && <span className="dur">{Math.round(job.duration)}s</span>}
      </div>
      <div className="card-body">
        <div className="title">{job.topic}</div>
        <div className="meta">
          <span className={`pill ${job.status}`}>{STATUS[job.status]}</span>
          <span>{ago(job.created)}</span>
        </div>
      </div>
    </Link>
  )
}
