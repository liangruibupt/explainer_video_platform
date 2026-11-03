import { useEffect, useState } from 'react'

export async function api(path, opts = {}) {
  const r = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (!r.ok) {
    let msg = `${r.status}`
    try { msg = (await r.json()).detail || msg } catch { /* not json */ }
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return r.json()
}

export const fileUrl = (id, name) => `/api/jobs/${id}/files/${name}`

// Poll a loader every `ms` while `active(data)` is true (or until first load).
export function usePoll(load, ms, active, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    let alive = true
    let timer
    const tick = async () => {
      try {
        const d = await load()
        if (!alive) return
        setData(d); setError(null)
        if (active(d)) timer = setTimeout(tick, ms)
      } catch (e) {
        if (!alive) return
        setError(e.message); timer = setTimeout(tick, ms * 2)
      }
    }
    tick()
    return () => { alive = false; clearTimeout(timer) }
  }, deps) // eslint-disable-line react-hooks/exhaustive-deps
  return [data, error, setData]
}

export const isLive = (j) => j && (j.status === 'queued' || j.status === 'running')

export function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return '刚刚'
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`
  return new Date(ts * 1000).toLocaleDateString('zh-CN')
}

export const STATUS = {
  queued: '排队中', running: '制作中', done: '已完成', failed: '失败',
}
