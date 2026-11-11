import { useEffect, useRef, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { VERSION_STATUS, versionLive, versionUrl, VersionWarnings } from '../components/MotionVersions.jsx'

// Motion editor for a Remotion job (docs/REMOTION_CONTRACT.md §7–§8). The studio starts the
// shotcraft workbench for this job and proxies it under /motion/, so the iframe is same-origin
// and the page can read the workbench project straight from its localStorage.

const PROJECT_KEY = 'shotcraft-workbench-project-v1'
const VOICE_NOTE = '配音轨已锁定：保存时恢复原配音；镜头时长和配音偏离超过 0.5 秒会提示'
const READY_TRIES = 40 // × 1.5 s ≈ 1 minute for the dev server to come up

// fetch that keeps the HTTP status, so 409 can get its own message
async function call(path, opts = {}) {
  const r = await fetch(`/api${path}`, { headers: { 'Content-Type': 'application/json' }, ...opts })
  let body = null
  try { body = await r.json() } catch { /* not json */ }
  if (!r.ok) {
    const d = body?.detail
    const e = new Error(typeof d === 'string' ? d : d ? JSON.stringify(d) : `${r.status}`)
    e.status = r.status
    throw e
  }
  return body
}

const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms))

export default function MotionEditPage() {
  const { id } = useParams()
  const frame = useRef(null)
  const [job, setJob] = useState(null)
  // phase: starting | waiting | ready | conflict | error
  const [phase, setPhase] = useState('starting')
  const [msg, setMsg] = useState('')
  const [url, setUrl] = useState(null)
  const [attempt, setAttempt] = useState(0)
  const [save, setSave] = useState(null) // {state: saving|polling|done|failed|error, version, error}

  // start the editor, then wait until the proxied page answers before showing the iframe
  useEffect(() => {
    let alive = true
    setPhase('starting'); setMsg(''); setUrl(null)
    ;(async () => {
      // known-ineligible jobs get the conflict message without a request
      try {
        const j = await call(`/jobs/${id}`)
        if (!alive) return
        setJob(j)
        if (j.renderer !== 'remotion' || j.status !== 'done') { setPhase('conflict'); return }
      } catch { /* let the editor call report the problem */ }
      let res
      try {
        res = await call(`/jobs/${id}/editor`, { method: 'POST' })
      } catch (e) {
        if (!alive) return
        if (e.status === 409) {
          setPhase('conflict')
          setMsg(e.message && e.message !== '409' ? e.message : '')
        } else {
          setPhase('error')
          setMsg(e.message)
        }
        return
      }
      if (!alive) return
      const target = res?.url || '/motion/?import=project'
      setPhase('waiting')
      for (let i = 0; i < READY_TRIES && alive; i++) {
        try {
          const r = await fetch(target, { cache: 'no-store' })
          if (r.ok) { if (alive) { setUrl(target); setPhase('ready') } return }
        } catch { /* proxy not up yet */ }
        await sleep(1500)
      }
      if (alive) { setPhase('error'); setMsg('编辑器在 1 分钟内没有启动完成') }
    })()
    return () => { alive = false }
  }, [id, attempt])

  // poll the saved version until it is done or failed
  useEffect(() => {
    if (save?.state !== 'polling') return
    let alive = true
    let timer
    const tick = async () => {
      try {
        const list = await call(`/jobs/${id}/versions`)
        const v = (list || []).find((x) => x.n === save.version.n)
        if (!alive) return
        if (v && !versionLive(v)) {
          setSave({ state: v.status === 'done' ? 'done' : 'failed', version: v, error: v.error })
          return
        }
        if (v) setSave((s) => ({ ...s, version: v }))
      } catch { /* keep polling */ }
      if (alive) timer = setTimeout(tick, 3000)
    }
    timer = setTimeout(tick, 1500)
    return () => { alive = false; clearTimeout(timer) }
  }, [id, save?.state, save?.version?.n]) // eslint-disable-line react-hooks/exhaustive-deps

  const saveVersion = async () => {
    let raw
    try {
      raw = frame.current?.contentWindow?.localStorage?.getItem(PROJECT_KEY)
    } catch {
      setSave({ state: 'error', error: '读不到编辑器里的工程（编辑器页面不是同源，无法读取）' })
      return
    }
    if (!raw) {
      setSave({ state: 'error', error: '编辑器里还没有可保存的工程。等编辑器加载完成，或先做一处修改再保存。' })
      return
    }
    let project
    try { project = JSON.parse(raw) } catch {
      setSave({ state: 'error', error: '编辑器里的工程数据损坏，无法解析' })
      return
    }
    setSave({ state: 'saving' })
    try {
      const v = await call(`/jobs/${id}/versions`, { method: 'POST', body: JSON.stringify({ project }) })
      if (v && !versionLive(v)) {
        setSave({ state: v.status === 'done' ? 'done' : 'failed', version: v, error: v.error })
      } else {
        setSave({ state: 'polling', version: v })
      }
    } catch (e) {
      setSave({ state: 'error', error: e.status === 409 ? `不能保存：${e.message}` : `保存失败：${e.message}` })
    }
  }

  const busy = save?.state === 'saving' || save?.state === 'polling'
  const v = save?.version

  return (
    <div className="mv-edit">
      <div className="mv-bar">
        <Link to={`/jobs/${id}`} className="back">← 返回作品</Link>
        <b className="mv-title" title={job?.topic}>{job?.topic || '动效编辑器'}</b>
        <span className="mv-note">{VOICE_NOTE}</span>
        <button className="primary mv-save" disabled={phase !== 'ready' || busy} onClick={saveVersion}>
          {save?.state === 'saving' ? '提交中…' : save?.state === 'polling' ? '渲染中…' : '保存为新版本'}
        </button>
      </div>

      {save && (
        <div className={`mv-status ${save.state}`}>
          {save.state === 'saving' && <span>正在提交工程…</span>}
          {save.state === 'polling' && v && (
            <span>已保存为 v{v.n}，{VERSION_STATUS[v.status] || v.status}。渲染完成前可以继续编辑，下次保存会生成新的版本。</span>
          )}
          {save.state === 'done' && v && (
            <span>v{v.n} 渲染完成。<a href={versionUrl(id, v.n)} target="_blank" rel="noreferrer">播放 v{v.n}</a> · <Link to={`/jobs/${id}`}>在作品页查看所有版本</Link></span>
          )}
          {save.state === 'failed' && <span>v{v?.n} 渲染失败：{save.error || '未知错误'}</span>}
          {save.state === 'error' && <span>{save.error}</span>}
          {v && <VersionWarnings warnings={v.warnings} />}
          {!busy && <button className="mv-x" aria-label="关闭" onClick={() => setSave(null)}>×</button>}
        </div>
      )}

      <div className="mv-stage">
        {phase === 'ready' && url && (
          <iframe ref={frame} className="mv-frame" src={url} title="动效编辑器" />
        )}
        {(phase === 'starting' || phase === 'waiting') && (
          <div className="mv-msg">
            <p>{phase === 'starting' ? '正在为这个作品准备动效编辑器…' : '动效编辑器正在启动，第一次打开大约需要几十秒…'}</p>
          </div>
        )}
        {phase === 'conflict' && (
          <div className="mv-msg">
            <p>只有已完成的 Remotion 作品才能在动效编辑器中打开。</p>
            {job && job.renderer !== 'remotion' && <p className="muted">这个作品用的是 Canvas 绘制。可以在工作台把「渲染方式」选为「Remotion 动效卡」重新制作。</p>}
            {job && job.renderer === 'remotion' && job.status !== 'done' && <p className="muted">这个作品还没有制作完成，完成后再来编辑。</p>}
            {msg && <p className="muted">{msg}</p>}
            <Link className="btn" to={`/jobs/${id}`}>返回作品</Link>
          </div>
        )}
        {phase === 'error' && (
          <div className="mv-msg">
            <p>动效编辑器还没准备好。</p>
            {msg && <p className="muted">{msg}</p>}
            <div className="actions">
              <button className="btn" onClick={() => setAttempt((a) => a + 1)}>重试</button>
              <Link className="btn" to={`/jobs/${id}`}>返回作品</Link>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
