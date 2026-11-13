import { useEffect, useRef, useState } from 'react'
import { useParams, useSearchParams, Link } from 'react-router-dom'
import { VERSION_STATUS, versionLive, versionUrl, VersionWarnings } from '../components/MotionVersions.jsx'
import MotionChat, { call } from '../components/MotionChat.jsx'
import MotionVoice from '../components/MotionVoice.jsx'

// Motion editor for a Remotion job (docs/REMOTION_CONTRACT.md §7–§8). The studio starts the
// shotcraft workbench for this job and proxies it under /motion/, so the iframe is same-origin
// and the page can read the workbench project straight from its localStorage.

const PROJECT_KEY = 'shotcraft-workbench-project-v1'
const VOICE_NOTE = '配音轨已锁定，改旁白请用『配音』面板重新配音'
const READY_TRIES = 40 // × 1.5 s ≈ 1 minute for the dev server to come up

const CHAT_OPEN_KEY = 'evp-motion-chat-open'
const SIDE_TAB_KEY = 'evp-motion-side-tab'

const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms))

export default function MotionEditPage() {
  const { id } = useParams()
  // ?version=n (from the job page's version picker): open the editor on that version
  const [params] = useSearchParams()
  const askedVersion = /^\d+$/.test(params.get('version') || '') ? Number(params.get('version')) : null
  const [base, setBase] = useState(null) // {n, has_scenes} once the editor opened that version
  const frame = useRef(null)
  const [job, setJob] = useState(null)
  // phase: starting | waiting | ready | conflict | error
  const [phase, setPhase] = useState('starting')
  const [msg, setMsg] = useState('')
  const [url, setUrl] = useState(null)
  const [attempt, setAttempt] = useState(0)
  const [save, setSave] = useState(null) // {state: saving|polling|done|failed|error, version, error}
  const [chatOpen, setChatOpen] = useState(() => localStorage.getItem(CHAT_OPEN_KEY) !== '0')
  const toggleChat = (open) => {
    setChatOpen(open)
    try { localStorage.setItem(CHAT_OPEN_KEY, open ? '1' : '0') } catch { /* ignore */ }
  }
  // right panel tab: chat | voice
  const [tab, setTabState] = useState(() => (localStorage.getItem(SIDE_TAB_KEY) === 'voice' ? 'voice' : 'chat'))
  const setTab = (t) => {
    setTabState(t)
    try { localStorage.setItem(SIDE_TAB_KEY, t) } catch { /* ignore */ }
  }

  // Current workbench project. The workbench debounces its autosave by 800 ms; its beforeunload
  // listener writes the current project immediately, so fire it to include the very last edit.
  const readProject = () => {
    let raw
    try {
      frame.current?.contentWindow?.dispatchEvent(new Event('beforeunload'))
      raw = frame.current?.contentWindow?.localStorage?.getItem(PROJECT_KEY)
    } catch {
      throw new Error('读不到编辑器里的工程（编辑器页面不是同源，无法读取）')
    }
    if (!raw) throw new Error('编辑器里还没有可用的工程。等编辑器加载完成，或先做一处修改再试。')
    try { return JSON.parse(raw) } catch { throw new Error('编辑器里的工程数据损坏，无法解析') }
  }

  // Put a project into the workbench and reload it. Navigate the iframe away first: its unload
  // flush would otherwise overwrite the stored project with the old in-memory one. The page and
  // the iframe are same-origin, so they share localStorage. The manifest is unchanged, so the
  // workbench keeps the stored project on ?import=project.
  const writeProject = async (project) => {
    const f = frame.current
    if (!f || !url) throw new Error('编辑器还没加载完成')
    const nav = (src) => new Promise((ok) => { f.addEventListener('load', ok, { once: true }); f.src = src })
    await nav('about:blank')
    try {
      window.localStorage.setItem(PROJECT_KEY, JSON.stringify(project))
    } finally {
      await nav(url)
    }
  }

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
        res = await call(`/jobs/${id}/editor`, askedVersion
          ? { method: 'POST', body: JSON.stringify({ version: askedVersion }) }
          : { method: 'POST' })
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
      // Seed the workbench with the chosen version before its page loads (same origin, shared
      // localStorage): v2+ -> that version's saved project; v1 -> clear it, so the workbench
      // imports the original film from the manifest.
      if (res?.version) {
        try {
          if (res.project) {
            const p = await call(res.project.replace(/^\/api/, ''))
            window.localStorage.setItem(PROJECT_KEY, JSON.stringify(p))
          } else {
            window.localStorage.removeItem(PROJECT_KEY)
          }
        } catch (e) {
          if (!alive) return
          setPhase('error'); setMsg(`读不到 v${res.version} 的工程：${e.message}`)
          return
        }
        setBase({ n: res.version, has_scenes: res.has_scenes })
      }
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
  }, [id, attempt, askedVersion]) // eslint-disable-line react-hooks/exhaustive-deps

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
    let project
    try { project = readProject() } catch (e) {
      setSave({ state: 'error', error: e.message })
      return
    }
    setSave({ state: 'saving' })
    try {
      const v = await call(`/jobs/${id}/versions`, {
        method: 'POST', body: JSON.stringify(base ? { project, base: base.n } : { project }) })
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
        {base && (
          <span className="pill mv-base" title={base.has_scenes ? '' : '这个版本保存时还没有记录场景代码，用的是当前的场景代码'}>
            基于 v{base.n} 编辑{base.has_scenes ? '' : '（场景代码为当前版本）'}
            {' · '}<Link to={`/jobs/${id}`}>换版本</Link>
          </span>
        )}
        <span className="mv-note">{VOICE_NOTE}</span>
        {phase === 'ready' && !chatOpen && (
          <button className="btn mc-toggle" onClick={() => toggleChat(true)}>Opus 助手 / 配音</button>
        )}
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
            <span>v{v.n}{v.base ? `（基于 v${v.base}）` : ''} 渲染完成。<a href={versionUrl(id, v.n)} target="_blank" rel="noreferrer">播放 v{v.n}</a> · <Link to={`/jobs/${id}`}>在作品页查看所有版本</Link></span>
          )}
          {save.state === 'failed' && <span>v{v?.n} 渲染失败：{save.error || '未知错误'}</span>}
          {save.state === 'error' && <span>{save.error}</span>}
          {v && <VersionWarnings warnings={v.warnings} />}
          {!busy && <button className="mv-x" aria-label="关闭" onClick={() => setSave(null)}>×</button>}
        </div>
      )}

      <div className="mv-body">
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
      {phase === 'ready' && (
        <aside className="mc-panel" aria-label="Opus 助手和配音" hidden={!chatOpen}>
          <div className="mc-head mv-tabs" role="tablist">
            <button role="tab" aria-selected={tab === 'chat'} className={tab === 'chat' ? 'on' : ''} onClick={() => setTab('chat')}>Opus 助手</button>
            <button role="tab" aria-selected={tab === 'voice'} className={tab === 'voice' ? 'on' : ''} onClick={() => setTab('voice')}>配音</button>
            <button className="mc-close" aria-label="收起助手" title="收起" onClick={() => toggleChat(false)}>›</button>
          </div>
          <MotionChat id={id} scenes={job?.scenes || []} ready={!!url} hidden={tab !== 'chat'}
                      readProject={readProject} writeProject={writeProject} onVoice={() => setTab('voice')} />
          <MotionVoice id={id} scenes={job?.scenes || []} ready={!!url} hidden={tab !== 'voice'}
                       readProject={readProject} writeProject={writeProject} />
        </aside>
      )}
      </div>
    </div>
  )
}
