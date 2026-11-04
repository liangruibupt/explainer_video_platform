import { useEffect, useState } from 'react'
import { api } from '../api.js'

// 设置 (docs/DESIGN.md section 9), adapted to the EC2 GPU backend (infra/gpu_ec2/).
// Backend: server/app/settings/api.py (/api/settings/*).

const CSS = `
.set-nav { display: flex; gap: 6px; flex-wrap: wrap; margin: 0 0 18px; }
.set-nav a { font-size: 13px; color: var(--ink2); text-decoration: none; padding: 4px 10px;
  border: 1px solid var(--line); border-radius: 99px; background: var(--card); }
.set-nav a:hover { border-color: var(--ink3); }
.set-sec { margin-bottom: 28px; scroll-margin-top: 16px; }
.set-sec > .section-head { margin-top: 8px; }
.set-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }
.set-card h3 { display: flex; align-items: center; gap: 8px; }
.set-card h3 .pill { margin-left: auto; }
.set-kv { display: grid; grid-template-columns: 88px 1fr; gap: 4px 12px; margin: 0 0 10px; font-size: 13px; }
.set-kv dt { color: var(--ink3); } .set-kv dd { margin: 0; word-break: break-all; }
.set-mono { font: 11.5px var(--mono); }
.set-tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.set-tbl th { text-align: left; color: var(--ink3); font-weight: 400; padding: 3px 6px; }
.set-tbl td { border-top: 1px solid var(--line); padding: 3px 6px; }
.set-tbl td.num, .set-tbl th.num { text-align: right; font-variant-numeric: tabular-nums; }
.set-tbl tr.cur td { background: var(--accent-soft); }
details.set-more { margin-top: 8px; font-size: 13px; }
details.set-more summary { cursor: pointer; color: var(--ink2); }
.set-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; align-items: center; }
.set-actions .primary { margin-left: 0; padding: 7px 14px; font-size: 13px; }
.set-grid2 { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }
.set-row { display: flex; align-items: center; gap: 10px; margin: 8px 0; font-size: 13px; }
.set-row select { border: 1px solid var(--line2); border-radius: 8px; padding: 4px 8px; background: var(--card); }
.set-bar { height: 8px; background: var(--line); border-radius: 99px; overflow: hidden; margin: 6px 0 2px; }
.set-bar i { display: block; height: 100%; background: var(--accent); }
`

const STATE = {
  none: ['已缩到 0', ''], launching: ['启动实例中', 'running'], booting: ['开机中', 'running'],
  loading: ['加载模型中', 'running'], ready: ['待命中', 'done'], busy: ['运行中', 'done'],
  unavailable: ['无法读取', 'failed'],
}
const MODULE = { image: '图像', video: 'AI 视频', music: '音乐' }

const usd = (v) => (v == null ? '—' : `$${Number(v).toFixed(v < 10 ? 3 : 2)}`)
const gb = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${(b / 1e6).toFixed(1)} MB`)
const dur = (s) => {
  if (s == null) return '—'
  if (s < 60) return `${s} 秒`
  if (s < 3600) return `${Math.floor(s / 60)} 分钟`
  return `${Math.floor(s / 3600)} 小时 ${Math.floor((s % 3600) / 60)} 分`
}
const when = (ts) => (ts ? new Date(typeof ts === 'number' ? ts * 1000 : ts).toLocaleString('zh-CN') : '—')

function useLoad(path, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const load = async (q = '') => {
    setBusy(true)
    try { setData(await api(path + q)); setError(null) } catch (e) { setError(e.message) }
    setBusy(false)
  }
  useEffect(() => { load() }, deps) // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error, busy, load, setData }
}

function DeployCard({ m, onChanged }) {
  const [log, setLog] = useState(null)
  const [logErr, setLogErr] = useState(null)
  const [msg, setMsg] = useState(null)
  const [acting, setActing] = useState(false)
  const st = m.status || {}
  const [label, cls] = STATE[st.state] || [st.state, '']
  const live = st.state && st.state !== 'none' && st.state !== 'unavailable'
  const first = (m.capacity || []).find((o) => o.usd_per_hour != null)
  const cur = (m.capacity || []).find((o) => o.type === st.instance_type && o.az === st.az
    && o.market === (st.lifecycle === 'spot' ? 'spot' : 'on-demand'))

  const showLog = async () => {
    setLogErr(null)
    try { setLog(await api(`/settings/deploy/${m.module}/log`)) } catch (e) { setLogErr(e.message) }
  }
  const act = async (what) => {
    const text = what === 'start'
      ? `启动 ${MODULE[m.module] || m.module} 的 GPU 实例？\n\n按容量顺序第一个选项：${first ? `${first.type} ${first.az} ${first.market}，约 ${usd(first.usd_per_hour)}/小时` : '价格未知'}。\n闲置 ${m.idle_shutdown_min} 分钟后自动关机，最长 ${m.hard_cap_min} 分钟。`
      : `停止 ${MODULE[m.module] || m.module} 的 GPU 实例？\n\n实例会被终止，正在处理的任务会失败。`
    if (!window.confirm(text)) return
    setActing(true); setMsg(null)
    try {
      const r = await api(`/settings/deploy/${m.module}/${what}`, { method: 'POST' })
      setMsg(what === 'start' ? `已请求启动：${r.instance_id}` : `已终止：${(r.terminated || []).join(', ') || '没有实例'}`)
      onChanged()
    } catch (e) { setMsg(`失败：${e.message}`) }
    setActing(false)
  }

  return (
    <div className="panel set-card">
      <h3>{MODULE[m.module] || m.module}<span className="muted set-mono">{m.region}</span>
        <span className={`pill ${cls}`}>{label}</span></h3>
      {st.error && <div className="error">{st.error}</div>}
      <dl className="set-kv">
        <dt>当前实例</dt>
        <dd>{live ? <>
          <span className="set-mono">{st.instance_id}</span><br />
          {st.instance_type} · {st.az} · {st.lifecycle === 'spot' ? 'Spot' : '按需'}
          {cur && <> · {usd(cur.usd_per_hour)}/小时</>}
        </> : '无（闲置不花钱）'}</dd>
        {live && <><dt>启动时间</dt><dd>{when(st.launch_time)}（已运行 {dur(st.uptime_s)}）</dd></>}
        <dt>心跳</dt>
        <dd>{m.heartbeat ? <>{m.heartbeat.state} · {dur(m.heartbeat.age_s)}前
          {m.heartbeat.age_s > (m.heartbeat_stale_s || 180) && <span className="muted">（已过期）</span>}
          {!live && m.heartbeat.instance_id && <span className="muted"> · 上一台 {m.heartbeat.instance_id}</span>}</>
          : <span className="muted">{m.heartbeat_error || '无'}</span>}</dd>
        <dt>模型</dt><dd>{m.weights_model} <span className="muted set-mono">@{(m.weights_sha || '').slice(0, 12)}</span></dd>
        <dt>镜像</dt><dd className="set-mono">{m.image_tag}</dd>
        <dt>自动关机</dt><dd>闲置 {m.idle_shutdown_min} 分钟 · 硬上限 {m.hard_cap_min} 分钟</dd>
      </dl>
      <details className="set-more">
        <summary>容量顺序（{(m.capacity || []).length} 项，先按需后 Spot）</summary>
        {m.prices_error && <div className="error">价格：{m.prices_error}</div>}
        <table className="set-tbl">
          <thead><tr><th>#</th><th>机型</th><th>可用区</th><th>市场</th><th className="num">$/小时</th></tr></thead>
          <tbody>{(m.capacity || []).map((o, i) => (
            <tr key={i} className={o === cur ? 'cur' : ''}>
              <td>{i + 1}</td><td>{o.type}</td><td>{o.az}</td>
              <td>{o.market === 'spot' ? 'Spot' : '按需'}</td><td className="num">{usd(o.usd_per_hour)}</td>
            </tr>))}</tbody>
        </table>
        <p className="muted">价格来自 AWS 价目表（按需）和 Spot 历史价，缓存 1 小时{m.prices_ts ? `，更新于 ${when(m.prices_ts)}` : ''}。</p>
      </details>
      <details className="set-more" onToggle={(e) => { if (e.target.open && !log) showLog() }}>
        <summary>worker 日志（最后 50 行）</summary>
        {logErr && <div className="error">{logErr}</div>}
        {log && <>
          <p className="muted">{log.instance_id ? <>实例 {log.instance_id}{log.updated ? ` · 上传于 ${when(log.updated)}` : ''}</> : log.note}
            {log.note && log.instance_id && ` · ${log.note}`} · <a href="#" onClick={(e) => { e.preventDefault(); showLog() }}>刷新</a></p>
          {log.lines?.length > 0 && <div className="log">{log.lines.join('\n')}</div>}
        </>}
        {!log && !logErr && <p className="muted">读取中…</p>}
      </details>
      <div className="set-actions">
        <button className="primary" disabled={acting || live} onClick={() => act('start')}>立即启动</button>
        <button className="btn danger" disabled={acting || !live} onClick={() => act('stop')}>停机</button>
        {msg && <span className="muted">{msg}</span>}
      </div>
    </div>
  )
}

function Deploy() {
  const { data, error, busy, load } = useLoad('/settings/deploy')
  useEffect(() => {
    const t = setInterval(() => load(), 30000)
    return () => clearInterval(t)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const sm = data?.sagemaker
  return (
    <section className="set-sec" id="deploy">
      <div className="section-head"><h2>模型部署</h2>
        <button className="btn" disabled={busy} onClick={() => load()}>{busy ? '刷新中…' : '刷新'}</button></div>
      <p className="sub">三个 GPU 模块都跑在平台自己启停的 EC2 实例上（每个模块最多一台）。没有任务时实例自己关机，闲置不花钱。提交任务时会自动启动，这里的「立即启动」用于提前预热。</p>
      {error && <div className="error">{error}</div>}
      {!data && !error && <div className="empty">读取中…</div>}
      <div className="set-cards">{(data?.modules || []).map((m) => <DeployCard key={m.module} m={m} onChanged={() => load()} />)}</div>
      {sm && <div className="panel" style={{ marginTop: 16 }}>
        <h3>SageMaker 后备</h3>
        {sm.state === 'retired' && <p className="muted">已下线：{sm.regions.join('、')} 没有 evp- 开头的端点。代码保留，只用于 g5 / g4dn 这类老 GPU。</p>}
        {sm.endpoints.length > 0 && <table className="set-tbl"><tbody>{sm.endpoints.map((e) => (
          <tr key={e.region + e.name}><td>{e.region}</td><td className="set-mono">{e.name}</td><td>{e.status}</td></tr>))}</tbody></table>}
        {sm.errors.map((e) => <div key={e} className="error">{e}</div>)}
      </div>}
    </section>
  )
}

function Cost() {
  const { data, error, busy, load } = useLoad('/settings/cost')
  const c = data?.cost
  return (
    <section className="set-sec" id="cost">
      <div className="section-head"><h2>成本</h2>
        <button className="btn" disabled={busy} onClick={() => load('?refresh=true')}>{busy ? '读取中…' : '重新读取'}</button></div>
      {error && <div className="error">{error}</div>}
      <div className="set-grid2">
        <div className="panel">
          <h3>本月花费（标签 project=explainer-video）</h3>
          {data?.cost_error && <div className="error">Cost Explorer：{data.cost_error}</div>}
          {c && <>
            <p style={{ font: '500 26px var(--serif)', margin: '0 0 6px' }}>${c.total_usd.toFixed(2)}</p>
            {c.by_module.length === 0 && <p className="muted">本月还没有带这个标签的花费。成本分配标签要在账单控制台激活后才会出现在 Cost Explorer 里。</p>}
            {c.by_module.length > 0 && <table className="set-tbl"><tbody>{c.by_module.map((r) => (
              <tr key={r.module}><td>{MODULE[r.module] || r.module}</td><td className="num">${r.usd.toFixed(2)}</td></tr>))}</tbody></table>}
            <p className="muted">{c.start} 至今 · 缓存 6 小时{data.cost_ts ? `，读取于 ${when(data.cost_ts)}` : ''}</p>
          </>}
        </div>
        <div className="panel">
          <h3>预算告警（AWS Budgets，只读）</h3>
          {data?.budgets_error && <div className="error">Budgets：{data.budgets_error}</div>}
          {(data?.budgets || []).length === 0 && data && !data.budgets_error && <p className="muted">没有预算。</p>}
          {(data?.budgets || []).map((b) => {
            const pct = b.limit && b.actual ? Math.min(100, (100 * b.actual) / b.limit) : 0
            return <div key={b.name}>
              <div className="set-mono">{b.name}</div>
              <div className="set-bar"><i style={{ width: `${pct}%` }} /></div>
              <p className="muted">已花 ${Number(b.actual || 0).toFixed(2)} / 上限 ${Number(b.limit).toFixed(0)}（{b.time_unit === 'MONTHLY' ? '每月' : b.time_unit}，统计整个账号）</p>
              <p className="muted">告警阈值：{b.thresholds.map((t) => `${t.threshold_type === 'PERCENTAGE' ? `${t.threshold}%` : `$${t.threshold}`}${t.state === 'ALARM' ? '（已触发）' : ''}`).join('、') || '无'}</p>
            </div>
          })}
          <p className="muted">告警只发邮件，不会停止资源。收件人不在这里显示。</p>
        </div>
      </div>
    </section>
  )
}

function Notifications() {
  const { data, error, setData } = useLoad('/settings/notifications')
  const [err, setErr] = useState(null)
  const save = async (patch) => {
    const next = { ...data, ...patch }
    if (patch.browser && 'Notification' in window && Notification.permission === 'default') {
      await Notification.requestPermission()
    }
    try { setData(await api('/settings/notifications', { method: 'PUT', body: JSON.stringify(next) })); setErr(null) } catch (e) { setErr(e.message) }
  }
  const perm = 'Notification' in window ? Notification.permission : 'unsupported'
  return (
    <div className="panel" id="notify">
      <h3>完成通知</h3>
      {(error || err) && <div className="error">{error || err}</div>}
      {data && <>
        <label className="toggle"><input type="checkbox" checked={data.in_app} onChange={(e) => save({ in_app: e.target.checked })} /><span />页面内通知（任务完成、待审核）</label>
        <label className="toggle" style={{ marginTop: 10 }}><input type="checkbox" checked={data.browser} onChange={(e) => save({ browser: e.target.checked })} /><span />浏览器通知</label>
        {data.browser && perm !== 'granted' && <p className="muted">浏览器{perm === 'denied' ? '已拒绝' : perm === 'unsupported' ? '不支持' : '还没有允许'}通知权限。</p>}
      </>}
    </div>
  )
}

function applyUi(ui) {
  const d = document.documentElement.dataset
  d.theme = ui.theme
  d.density = ui.density
}

function Appearance() {
  const { data, error, setData } = useLoad('/settings/ui')
  const [err, setErr] = useState(null)
  useEffect(() => { if (data) applyUi(data) }, [data])
  const save = async (patch) => {
    try { setData(await api('/settings/ui', { method: 'PUT', body: JSON.stringify({ ...data, ...patch }) })); setErr(null) } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel" id="ui">
      <h3>外观</h3>
      {(error || err) && <div className="error">{error || err}</div>}
      {data && <>
        <div className="set-row">主题
          <select value={data.theme} onChange={(e) => save({ theme: e.target.value })}>
            <option value="auto">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option>
          </select></div>
        <div className="set-row">密度
          <select value={data.density} onChange={(e) => save({ density: e.target.value })}>
            <option value="comfortable">宽松</option><option value="compact">紧凑</option>
          </select></div>
        <p className="muted">设置写在页面根元素的 data-theme / data-density 上。深色主题的样式还没做，暂时只有浅色效果。</p>
      </>}
    </div>
  )
}

function ModelCache() {
  const { data, error, busy, load } = useLoad('/settings/model-cache')
  const total = (data?.prefixes || []).reduce((s, p) => s + p.size, 0)
  return (
    <section className="set-sec" id="cache">
      <div className="section-head"><h2>模型缓存（S3）</h2>
        <button className="btn" disabled={busy} onClick={() => load('?refresh=true')}>{busy ? '读取中…' : '重新统计'}</button></div>
      <p className="sub">模型权重提前放在 GPU 同区域的 S3 桶里，实例启动时从这里读，不从 Hugging Face 下载。</p>
      {(error || data?.error) && <div className="error">{error || data.error}</div>}
      {data && <div className="panel">
        <table className="set-tbl">
          <thead><tr><th>区域</th><th>模型</th><th>版本</th><th>使用模块</th><th className="num">文件</th><th className="num">大小</th><th>更新</th></tr></thead>
          <tbody>{data.prefixes.map((p) => (
            <tr key={p.bucket + p.prefix} title={`s3://${p.bucket}/${p.prefix}`}>
              <td>{p.region}</td><td>{p.model}</td><td className="set-mono">{p.version.slice(0, 12)}</td>
              <td>{p.module ? (MODULE[p.module] || p.module) : <span className="muted">未使用</span>}</td>
              <td className="num">{p.files}</td><td className="num">{gb(p.size)}</td>
              <td className="muted">{p.modified ? new Date(p.modified).toLocaleDateString('zh-CN') : ''}</td>
            </tr>))}</tbody>
        </table>
        <p className="muted">合计 {gb(total)} · 缓存 1 小时{data.ts ? `，统计于 ${when(data.ts)}` : ''}</p>
      </div>}
    </section>
  )
}

function Account() {
  const { data, error } = useLoad('/settings/account')
  return (
    <div className="panel" id="account">
      <h3>账号</h3>
      {(error || data?.error) && <div className="error">{error || data.error}</div>}
      {data && <dl className="set-kv">
        <dt>AWS 账号</dt><dd className="set-mono">{data.account_id || '—'}</dd>
        <dt>调用身份</dt><dd className="set-mono">{data.arn || '—'}</dd>
        <dt>控制面区域</dt><dd>{data.control_region}</dd>
        <dt>GPU 区域</dt><dd>{Object.entries(data.gpu_regions || {}).map(([m, r]) => `${MODULE[m] || m} ${r}`).join(' · ')}</dd>
        <dt>用到的区域</dt><dd>{(data.regions || []).join('、')}</dd>
      </dl>}
      <p className="muted">单用户，本地运行，不需要登录。凭证来自这台机器的 IAM 角色，不存在代码或配置里。</p>
    </div>
  )
}

export default function SettingsPage() {
  return (
    <div className="page">
      <style>{CSS}</style>
      <h1 className="h1-sm">设置</h1>
      <p className="sub">GPU 部署、成本、通知和外观。</p>
      <nav className="set-nav">
        <a href="#deploy">模型部署</a><a href="#cost">成本</a><a href="#cache">模型缓存（S3）</a>
        <a href="#prefs">通知与外观</a><a href="#account">账号</a>
      </nav>
      <Deploy />
      <Cost />
      <ModelCache />
      <section className="set-sec" id="prefs">
        <div className="section-head"><h2>偏好与账号</h2></div>
        <div className="set-grid2"><Notifications /><Appearance /><Account /></div>
      </section>
    </div>
  )
}
