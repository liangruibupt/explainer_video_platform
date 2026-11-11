import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { api, usePoll, isLive, fileUrl, STATUS } from '../api.js'
import FeedbackButton from '../components/FeedbackButton.jsx'
import MotionVersions from '../components/MotionVersions.jsx'

const GALLERY = 'https://vincentwei1021.github.io/video-shotcraft/'
const cardLabel = (c) => (c === 'fallback/title-card' ? '标题卡（回退）' : c)
const secs = (s) => (s.started && s.ended ? `${Math.round(s.ended - s.started)}s` : '')

export default function JobPage() {
  const { id } = useParams()
  const nav = useNavigate()
  const [job, error] = usePoll(() => api(`/jobs/${id}`), 2500, isLive, [id])
  const [log, setLog] = useState(null)

  if (error && !job) return <div className="page"><div className="error">{error}</div></div>
  if (!job) return <div className="page"><p className="sub">加载中…</p></div>

  const remotion = job.renderer === 'remotion'
  const tall = job.ratio === '9:16'
  const showLog = async () => {
    const r = await fetch(fileUrl(id, 'pipeline.log'))
    setLog(r.ok ? (await r.text()).slice(-6000) : '还没有日志')
  }
  const remove = async () => {
    if (!confirm('删除这个作品和它的所有文件？此操作无法撤销。')) return
    await api(`/jobs/${id}`, { method: 'DELETE' })
    nav('/works')
  }
  const remake = async () => {
    const j = await api('/jobs', {
      method: 'POST',
      body: JSON.stringify({ topic: job.topic, seconds: job.seconds, ratio: job.ratio,
                             captions: job.captions, music: job.music,
                             illustrations: job.illustrations || 0, clips: job.clips || 0,
                             renderer: job.renderer || 'html' }),
    })
    nav(`/jobs/${j.id}`)
  }

  return (
    <div className="page">
      <Link to="/works" className="back">← 作品集</Link>
      <div className="job-head">
        <h1 className="h1-sm">{job.topic}</h1>
        <span className={`pill ${job.status}`}>{STATUS[job.status]}</span>
        <span className={`pill mv-renderer ${remotion ? 'remotion' : ''}`}
              title={remotion ? '每段由 video-shotcraft 动效卡写成 Remotion 场景' : 'Canvas2D 逐帧绘制'}>
          {remotion ? 'Remotion 动效卡' : 'Canvas 绘制'}
        </span>
      </div>
      <p className="sub">
        {job.ratio === '9:16' ? '竖屏' : '横屏'} · 约 {job.seconds} 秒 · {job.captions ? '逐字字幕' : '无字幕'} · {({ synth: '合成垫乐', none: '无音乐', guqin: '古琴', ai: 'AI 音乐' })[job.music] || job.music}
        {job.duration ? ` · 成片 ${job.duration} 秒` : ''}
      </p>
      {job.music === 'ai' && (job.music_source || job.music_style) && (
        <p className="muted">音乐：{job.music_source || '生成中'}{job.music_style ? ` · 风格「${job.music_style}」` : ''}</p>
      )}

      <div className="job-grid">
        <div>
          {job.status === 'done'
            ? <video className={`player ${job.ratio === '9:16' ? 'tall' : ''}`}
                     src={fileUrl(id, 'final.mp4')} poster={fileUrl(id, 'poster.jpg')} controls />
            : <div className={`player waiting ${job.ratio === '9:16' ? 'tall' : ''}`}>
                {job.status === 'failed' ? '制作失败' : '正在制作，完成后在这里播放'}
              </div>}
          {job.status === 'failed' && <pre className="error pre">{job.error}</pre>}
          <div className="actions">
            {job.status === 'done' && <a className="btn" href={fileUrl(id, 'final.mp4')} download>下载 MP4</a>}
            {!isLive(job) && <button className="btn" onClick={remake}>用同一主题重做</button>}
            <button className="btn" onClick={showLog}>查看日志</button>
            {!isLive(job) && <button className="btn danger" onClick={remove}>删除</button>}
            {job.status === 'done' && <FeedbackButton target={{ kind: 'job', id }} />}
            {remotion && job.status === 'done' && (
              <Link className="btn mv-open" to={`/jobs/${id}/edit`}>在动效编辑器中打开</Link>
            )}
          </div>
          {log && <pre className="log">{log}</pre>}
          {remotion && job.status === 'done' && (
            <div className="panel mv-versions">
              <h3>版本</h3>
              <p className="muted">v1 是原片；在动效编辑器里每次「保存为新版本」都会渲染一个新版本，原片保留不变。</p>
              <MotionVersions id={id} tall={tall} />
            </div>
          )}
        </div>

        <div className="panel">
          <h3>制作步骤</h3>
          <ol className="steps">
            {job.steps.map((s) => (
              <li key={s.key} className={s.status}>
                <i />{s.label}{s.note && <em className="muted"> · {s.note}</em>}
                <span>{s.status === 'skipped' ? '跳过' : secs(s)}</span>
              </li>
            ))}
          </ol>
          {job.scenes?.length > 0 && <>
            <h3>分镜（{job.scenes.length} 段）</h3>
            <ol className="scenes">
              {job.scenes.map((s, i) => (
                <li key={s.id}>
                  <b>{i + 1}. {s.title || s.id}</b>
                  {s.illustration && <span className="pill soon">AI 插图</span>}
                  {s.clip && <span className="pill soon">AI 镜头</span>}
                  {s.frames && <span className="frames">{(s.frames / 30).toFixed(1)}s</span>}
                  {remotion && s.card && (
                    <a className="mv-card" href={GALLERY} target="_blank" rel="noreferrer"
                       title="在 video-shotcraft 镜头卡图库中查看">
                      动效卡 · {cardLabel(s.card)}
                    </a>
                  )}
                  <p>{s.narration}</p>
                </li>
              ))}
            </ol>
          </>}
        </div>
      </div>
    </div>
  )
}
