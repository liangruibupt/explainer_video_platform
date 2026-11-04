import { useEffect, useState } from 'react'
import { api } from '../api.js'

// GPU status pill for the self-managed EC2 workers (/api/<module>/status).
export default function GpuPill({ module, cold }) {
  const [st, setSt] = useState(null)
  useEffect(() => {
    const load = () => api(`/${module}/status`).then(setSt).catch(() => {})
    load(); const t = setInterval(load, 15000); return () => clearInterval(t)
  }, [module])
  const STATE = {
    ready: [`GPU 已就绪${st?.instance_type ? `（${st.instance_type}）` : ''}`, 'done'],
    starting: [`GPU 启动中（从 0 冷启动约 ${cold} 分钟）`, 'running'],
    idle: [`GPU 未运行，提交后自动启动（约 ${cold} 分钟）`, ''],
    failed: ['GPU 故障', 'failed'],
    unavailable: ['无法读取 GPU 状态', 'failed'],
  }
  const [label, cls] = STATE[st?.state] || ['读取 GPU 状态…', '']
  return <span className={`pill ${cls}`}>{label}</span>
}
