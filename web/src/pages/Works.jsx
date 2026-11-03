import { api, usePoll, isLive } from '../api.js'
import WorkCard from '../components/WorkCard.jsx'

export default function Works() {
  const [jobs, error] = usePoll(() => api('/jobs'), 4000, (js) => js.some(isLive), [])
  const done = (jobs || []).filter((j) => j.status === 'done')
  return (
    <div className="page">
      <h1 className="h1-sm">作品集</h1>
      <p className="sub">已完成的讲解视频。正在制作和失败的任务在「任务中心」。</p>
      {error && <div className="error">{error}</div>}
      {jobs && done.length === 0 && <div className="empty">还没有完成的作品。</div>}
      <div className="grid">{done.map((j) => <WorkCard key={j.id} job={j} />)}</div>
    </div>
  )
}
