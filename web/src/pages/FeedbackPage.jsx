import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, usePoll, ago } from '../api.js'

// 反馈与改进 (docs/PHASE5_CONTRACTS.md, DESIGN.md section 8): feedback items with their analysis,
// clusters vs RULE_MIN, skill-change proposals (diff, before/after QC, PR) and the skill version.

const RULE_MIN = 3
const TABS = [['items', '反馈'], ['clusters', '聚类'], ['proposals', '改进提案'], ['skill', 'skill 版本']]
const FB_STATUS = {
  new: ['新反馈', ''], analyzing: ['分析中', 'running'], rerun: ['已重跑', 'done'],
  clustered: ['已归类', 'soon'], proposed: ['已起草改进', 'waiting_review'], merged: ['已合并', 'done'],
  resolved: ['已解决', 'done'], dismissed: ['已忽略', ''],
}
const VERDICT = { one_off: '偶发问题', rule: '规则问题', unclear: '还不确定' }
const PR_STATE = {
  drafting: ['起草中', 'running'], checking: ['对比中', 'running'], draft: ['草稿（未开 PR）', ''],
  open: ['PR 待合并', 'waiting_review'], merged: ['已合并', 'done'], closed: ['已关闭', ''], failed: ['失败', 'failed'],
}
const KIND = { job: '讲解视频', workflow: '工作流', image: '图像', video: 'AI 视频', music: '音乐', avatar: '数字人' }
const TARGET_URL = {
  job: (t) => `/jobs/${t.id}`, workflow: (t) => `/workflow?run=${t.id}`, image: () => '/image',
  video: () => '/video', music: () => '/music', avatar: () => '/avatar',
}
const QC_LABEL = {
  duration_s: '成片时长 (s)', target_s: '目标时长 (s)', duration_ratio: '时长比', lufs: '整体响度 LUFS',
  voice_lufs: '人声 LUFS', music_lufs: '音乐 LUFS', voice_minus_music_db: '人声高于音乐 (dB)',
  caption_count: '字幕条数', caption_max_cps: '字幕最快 字/秒', caption_overlap_s: '字幕重叠 (s)',
  scene_count: '分镜数', silence_ratio: '静音占比', failed_steps: '失败步骤', node_errors: '节点错误',
  avatar_identity_min: '数字人一致性最低',
}
const liveFb = (s) => s === 'new' || s === 'analyzing'
const livePr = (s) => s === 'drafting' || s === 'checking'

const CSS = `
.fb-tabs { display: flex; gap: 6px; margin: 14px 0 16px; flex-wrap: wrap; }
.fb-tabs button { border: 1px solid var(--line); background: var(--card); border-radius: 99px;
  padding: 4px 14px; cursor: pointer; font-size: 13px; color: var(--ink2); }
.fb-tabs button.on { background: var(--accent-soft); color: var(--accent); border-color: transparent; }
.fb-tabs .n { margin-left: 4px; opacity: .7; }
.fb-note { background: #f3e6c9; color: #7a5612; border-radius: 10px; padding: 9px 14px; font-size: 13px; margin-bottom: 14px; }
.fb-list { display: flex; flex-direction: column; gap: 12px; }
.fb-item.hl { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-soft); }
.fb-top { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.fb-top a { font-weight: 500; text-decoration: none; }
.fb-top .when { margin-left: auto; }
.fb-reasons { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.fb-text { margin: 6px 0 0; font-size: 13.5px; }
.fb-an { border-top: 1px solid var(--line); margin-top: 10px; padding-top: 8px; font-size: 13px; }
.fb-an ul { margin: 4px 0 0; padding-left: 18px; color: var(--ink2); font: 12px/1.6 var(--mono); }
.fb-prog { width: 120px; height: 6px; background: var(--line); border-radius: 99px; overflow: hidden; display: inline-block; vertical-align: middle; }
.fb-prog > div { height: 100%; background: var(--warn); }
.fb-prog.full > div { background: var(--ok); }
.fb-diff { background: #1f1b18; color: #d9cfc4; font: 12px/1.5 var(--mono); border-radius: 10px; padding: 10px 0;
  overflow: auto; max-height: 420px; margin: 8px 0 0; }
.fb-diff div { padding: 0 12px; white-space: pre; }
.fb-diff .add { background: rgba(61,122,90,.35); color: #c9f0d6; }
.fb-diff .del { background: rgba(179,54,43,.35); color: #f6cfc9; }
.fb-diff .hunk { color: #9fb7d0; }
.fb-diff .file { color: #e6d8a8; font-weight: 600; }
table.fb-qc { border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 6px; }
.fb-qc th, .fb-qc td { padding: 6px 10px; border-top: 1px solid var(--line); text-align: left; }
.fb-qc th { color: var(--ink3); font-weight: 400; font-size: 12px; }
.fb-qc tr.tgt td { background: var(--accent-soft); font-weight: 600; }
.fb-qc td.num { font-family: var(--mono); }
.fb-prs { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 16px; align-items: start; }
.fb-prs ul { list-style: none; margin: 0; padding: 0; background: var(--card); border: 1px solid var(--line); border-radius: 12px; }
.fb-prs li { padding: 10px 14px; border-top: 1px solid var(--line); cursor: pointer; font-size: 13px; }
.fb-prs li:first-child { border-top: 0; }
.fb-prs li:hover, .fb-prs li.on { background: #f3ede5; }
.fb-page .btn:disabled { opacity: .45; cursor: default; }
.fb-h4 { font-size: 12px; color: var(--ink3); margin: 14px 0 0; letter-spacing: .5px; }
@media (max-width: 1000px) { .fb-prs { grid-template-columns: 1fr; } }
`

function Pill({ map, k }) {
  const [label, cls] = map[k] || [k || '—', '']
  return <span className={`pill ${cls}`}>{label}</span>
}

function TargetLink({ t }) {
  if (!t) return <span className="muted">未知对象</span>
  const url = TARGET_URL[t.kind]?.(t)
  const text = `${KIND[t.kind] || t.kind} ${t.id}${t.node_id ? ` · 节点 ${t.node_id}` : ''}`
  return url ? <Link to={url}>{text}</Link> : <span>{text}</span>
}

function Cluster({ name, count }) {
  if (!name) return null
  const n = count ?? 0
  return (
    <span className="muted">聚类 <code>{name}</code> · {n}/{RULE_MIN}
      {' '}<span className={`fb-prog ${n >= RULE_MIN ? 'full' : ''}`}><div style={{ width: `${Math.min(100, (n / RULE_MIN) * 100)}%` }} /></span>
      {n >= RULE_MIN ? ' 已够起草规则改进' : ` 还差 ${RULE_MIN - n} 条同类反馈才会自动起草`}</span>
  )
}

// GET /api/feedback/{id} returns "item + analysis + proposal": accept either
// {item, analysis, proposal} or the item itself with analysis/proposal keys.
const unpack = (d) => (d?.item ? { ...d.item, analysis: d.analysis, proposal: d.proposal } : d)

function FeedbackItem({ base, highlight, onChanged, onOpenProposal }) {
  const [detail, setDetail] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState(null)
  const ref = useRef(null)
  const it = { ...base, ...(detail || {}) }
  const needDetail = base.analysis_id || base.proposal_id || liveFb(base.status) || highlight
  const [d] = usePoll(() => (needDetail ? api(`/feedback/${base.id}`).then(unpack) : Promise.resolve(null)),
    3000, (x) => x && liveFb(x.status), [base.id, base.status, base.analysis_id, base.proposal_id])
  useEffect(() => { if (d) setDetail(d) }, [d])
  useEffect(() => { if (highlight && ref.current) ref.current.scrollIntoView({ block: 'center' }) }, [highlight])

  const an = it.analysis
  const act = (label, path) => async () => {
    setBusy(label); setErr(null)
    try {
      await api(`/feedback/${it.id}/${path}`, { method: 'POST', body: '{}' })
      onChanged()
    } catch (e) { setErr(e.message === '501' ? '这个操作的后端还没接上（501）' : e.message) }
    setBusy('')
  }
  const closed = it.status === 'dismissed' || it.status === 'resolved' || it.status === 'merged'
  return (
    <div ref={ref} className={`panel fb-item ${highlight ? 'hl' : ''}`}>
      <div className="fb-top">
        <Pill map={FB_STATUS} k={it.status} />
        <TargetLink t={it.target} />
        <span className="muted when">{it.id}{it.created ? ` · ${ago(it.created)}` : ''}</span>
      </div>
      <div className="fb-reasons">{(it.reasons || []).map((r) => <span key={r} className="pill failed">{r}</span>)}</div>
      {it.text ? <p className="fb-text">「{it.text}」</p> : <p className="fb-text muted">（没有填写说明）</p>}
      <div className="fb-an">
        {liveFb(it.status) && !an && <span className="muted">正在分析原因…</span>}
        {an && <>
          <div><b>{VERDICT[an.verdict] || an.verdict}</b>
            {an.layer && <span className="muted"> · 层 {an.layer}</span>}
            {an.symptom && <span className="muted"> · 症状 <code>{an.symptom}</code></span>}</div>
          {an.explanation && <div style={{ marginTop: 4 }}>{an.explanation}</div>}
          {an.evidence?.length > 0 && <ul>{an.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>}
          {an.action?.type === 'rerun' && <div className="muted" style={{ marginTop: 4 }}>建议：重跑 {an.action.node_id || an.action.step}
            {an.action.changes && ` · 改动 ${JSON.stringify(an.action.changes)}`}</div>}
          {an.action?.type === 'propose' && <div className="muted" style={{ marginTop: 4 }}>建议：修改 <code>{an.action.target_file}</code> · {an.action.intent}</div>}
          {an.action?.type === 'ask' && <div className="muted" style={{ marginTop: 4 }}>需要你补充：{an.action.question}</div>}
          <div style={{ marginTop: 6 }}><Cluster name={an.cluster} count={an.cluster_count} /></div>
        </>}
        {!an && !liveFb(it.status) && <span className="muted">还没有分析结果。</span>}
        {it.resolution && <div className="muted" style={{ marginTop: 4 }}>处理结果：{typeof it.resolution === 'string' ? it.resolution : JSON.stringify(it.resolution)}</div>}
        {it.proposal_id && <div style={{ marginTop: 4 }}>改进提案：<a href="#" onClick={(e) => { e.preventDefault(); onOpenProposal(it.proposal_id) }}>{it.proposal_id}</a></div>}
      </div>
      {err && <div className="error">{err}</div>}
      {!closed && (
        <div className="actions">
          <button className="btn" disabled={!!busy || !an || an.action?.type === 'ask'} onClick={act('act', 'act')}
                  title="按分析结果执行：偶发问题重跑，规则问题在聚类够数时起草改进">
            {busy === 'act' ? '执行中…' : '按建议重跑'}</button>
          <button className="btn" disabled={!!busy} onClick={act('draft', 'draft')} title="不等聚类够 3 条，直接为这条反馈起草规则改进">
            {busy === 'draft' ? '起草中…' : '立即起草改进'}</button>
          <button className="btn" disabled={!!busy} onClick={act('analyze', 'analyze')}>{busy === 'analyze' ? '分析中…' : '重新分析'}</button>
          <button className="btn danger" disabled={!!busy} onClick={act('dismiss', 'dismiss')}>{busy === 'dismiss' ? '…' : '忽略'}</button>
        </div>
      )}
    </div>
  )
}

function ItemsTab({ items, focus, onChanged, onOpenProposal }) {
  const [filter, setFilter] = useState('')
  const shown = (items || []).filter((x) => !filter || x.status === filter)
  return (
    <>
      <div className="quick" style={{ marginTop: 0, marginBottom: 12 }}><span>状态</span>
        <button className={`chip ${filter === '' ? 'on' : ''}`} onClick={() => setFilter('')}>全部</button>
        {Object.entries(FB_STATUS).map(([k, [l]]) => (
          <button key={k} className={`chip ${filter === k ? 'on' : ''}`} onClick={() => setFilter(k)}>{l}</button>
        ))}
      </div>
      {shown.length === 0
        ? <div className="empty">还没有反馈。在作品、任务或工作流运行旁点「不满意」就会出现在这里。</div>
        : <div className="fb-list">{shown.map((it) => (
            <FeedbackItem key={it.id} base={it} highlight={it.id === focus} onChanged={onChanged} onOpenProposal={onOpenProposal} />
          ))}</div>}
    </>
  )
}

function ClustersTab({ clusters, onOpenProposal, onOpenItem }) {
  if (!clusters?.length) return <div className="empty">还没有聚类。分析把同一对象、同一症状的反馈归到一起，够 {RULE_MIN} 条才会起草规则改进。</div>
  return (
    <table className="tasks">
      <tbody>
        <tr><td className="muted">聚类（层:症状）</td><td className="muted">数量 / {RULE_MIN}</td><td className="muted">反馈</td><td className="muted">改进提案</td></tr>
        {clusters.map((c) => (
          <tr key={c.cluster}>
            <td><code>{c.cluster}</code></td>
            <td><span className={`fb-prog ${c.count >= RULE_MIN ? 'full' : ''}`}><div style={{ width: `${Math.min(100, (c.count / RULE_MIN) * 100)}%` }} /></span>
              <span className="muted"> {c.count}/{RULE_MIN}{c.count >= RULE_MIN ? ' 已够' : ''}</span></td>
            <td className="muted">{(c.feedback_ids || []).map((id, i) => (
              <span key={id}>{i > 0 && ' · '}<a href="#" onClick={(e) => { e.preventDefault(); onOpenItem(id) }}>{id}</a></span>))}</td>
            <td>{c.proposal_id
              ? <a href="#" onClick={(e) => { e.preventDefault(); onOpenProposal(c.proposal_id) }}>{c.proposal_id}</a>
              : <span className="muted">{c.count >= RULE_MIN ? '待起草' : '未达到'}</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Diff({ text }) {
  if (!text) return <div className="muted">还没有 diff。</div>
  return (
    <div className="fb-diff">
      {text.split('\n').map((l, i) => {
        const cls = l.startsWith('+++') || l.startsWith('---') || l.startsWith('diff ') ? 'file'
          : l.startsWith('@@') ? 'hunk' : l.startsWith('+') ? 'add' : l.startsWith('-') ? 'del' : ''
        return <div key={i} className={cls}>{l || ' '}</div>
      })}
    </div>
  )
}

const fmt = (v) => (v == null ? '—' : typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(2)) : Array.isArray(v) ? (v.length ? v.join(', ') : '0') : String(v))

function QcTable({ before, after, metric, improved }) {
  const keys = [...new Set([...Object.keys(before || {}), ...Object.keys(after || {})])]
  if (!keys.length) return <div className="muted">还没有对比数据。点「重新对比」用复现输入跑一遍改前和改后。</div>
  keys.sort((a, b) => (a === metric ? -1 : b === metric ? 1 : 0))
  return (
    <>
      <table className="fb-qc">
        <thead><tr><th>指标</th><th>改前（master）</th><th>改后（提案）</th></tr></thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k} className={k === metric ? 'tgt' : ''}>
              <td>{QC_LABEL[k] || k}{k === metric && ' ← 目标指标'}</td>
              <td className="num">{fmt(before?.[k])}</td><td className="num">{fmt(after?.[k])}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {metric && after && Object.keys(after).length > 0 && (
        <div style={{ marginTop: 6 }}>目标指标 <code>{metric}</code>：
          {improved ? <span className="pill done">改善了</span> : <span className="pill failed">没有改善，只保留为草稿，不开 PR</span>}</div>
      )}
    </>
  )
}

function ProposalDetail({ id, onChanged }) {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState(null)
  const [tick, setTick] = useState(0)
  const [p, perr] = usePoll(() => api(`/feedback/proposals/${id}`), 4000, (x) => x && livePr(x.state), [id, tick])
  if (perr && !p) return <div className="error">{perr}</div>
  if (!p) return <div className="panel muted">加载中…</div>
  const act = (label, path) => async () => {
    setBusy(label); setErr(null)
    try { await api(`/feedback/proposals/${id}/${path}`, { method: 'POST', body: '{}' }); setTick((t) => t + 1); onChanged() }
    catch (e) { setErr(e.message === '501' ? '这个操作的后端还没接上（501）' : e.message) }
    setBusy('')
  }
  const hasAfter = p.qc_after && Object.keys(p.qc_after).length > 0
  return (
    <div className="panel">
      <h3>{p.id} <Pill map={PR_STATE} k={p.state} /></h3>
      <div className="muted">聚类 <code>{p.cluster}</code> · {(p.feedback_ids || []).length} 条反馈 · 分支 <code>{p.branch}</code>
        {p.created ? ` · ${ago(p.created)}` : ''}</div>
      <div className="fb-note" style={{ marginTop: 10 }}>
        这个改动<b>只有在有人审查并合并 PR 之后才会生效</b>。在那之前线上的 skill 和节点默认值都不变；
        合并后系统用同一个复现输入重跑一次，结果会附到原来的反馈上。</div>
      {p.reason && <><div className="fb-h4">原因</div><div>{p.reason}</div></>}
      <div className="fb-h4">改动文件</div>
      <div>{(p.target_files || []).map((f) => <code key={f} style={{ marginRight: 10 }}>{f}</code>)}</div>
      <div className="fb-h4">DIFF</div>
      <Diff text={p.diff} />
      <div className="fb-h4">改前 / 改后 QC 对比</div>
      <QcTable before={p.qc_before} after={p.qc_after} metric={p.target_metric} improved={p.improved} />
      {p.repro && <>
        <div className="fb-h4">复现输入（{p.repro.kind === 'workflow' ? '工作流' : '讲解视频'}）</div>
        <pre className="pre wf-text">{JSON.stringify(p.repro.input, null, 2)}</pre>
      </>}
      <div className="fb-h4">PR</div>
      <div>{p.pr_url
        ? <a href={p.pr_url} target="_blank" rel="noreferrer">PR #{p.pr_number ?? ''} · {p.pr_url}</a>
        : <span className="muted">还没有开 PR{p.state === 'draft' ? (hasAfter && !p.improved ? '（目标指标没改善，保留为草稿）' : '（草稿，可以开 PR）') : ''}</span>}</div>
      {p.verify && <>
        <div className="fb-h4">合并后验证</div>
        <div>{p.verify.ok ? <span className="pill done">验证通过</span> : <span className="pill failed">验证未通过</span>}
          {p.verify.rerun_id && <span className="muted"> · 重跑 {p.verify.rerun_id}</span>}</div>
        {p.verify.qc && <QcTable before={p.qc_before} after={p.verify.qc} metric={p.target_metric} improved={p.verify.ok} />}
      </>}
      {err && <div className="error">{err}</div>}
      <div className="actions">
        <button className="btn" disabled={!!busy || livePr(p.state)} onClick={act('check', 'check')}>{busy === 'check' ? '对比中…' : '重新对比'}</button>
        <button className="btn" disabled={!!busy || p.state !== 'draft' || !p.improved} onClick={act('open', 'open')}
                title={p.improved ? '推送分支并开 PR（不会合并）' : '目标指标没改善，不能开 PR'}>{busy === 'open' ? '开 PR 中…' : '开 PR'}</button>
        <button className="btn" disabled={!!busy || !p.pr_url} onClick={act('sync', 'sync')}>{busy === 'sync' ? '同步中…' : '同步状态'}</button>
      </div>
    </div>
  )
}

function ProposalsTab({ proposals, sel, setSel, onChanged }) {
  useEffect(() => { if (!sel && proposals?.length) setSel(proposals[0].id) }, [proposals]) // eslint-disable-line
  if (!proposals?.length) return <div className="empty">还没有改进提案。同类反馈够 {RULE_MIN} 条，或在某条反馈上点「立即起草改进」，就会在这里起草。</div>
  return (
    <div className="fb-prs">
      <ul>
        {proposals.map((p) => (
          <li key={p.id} className={p.id === sel ? 'on' : ''} onClick={() => setSel(p.id)}>
            <Pill map={PR_STATE} k={p.state} /> <b>{p.id}</b>
            <div className="muted"><code>{p.cluster}</code> · {(p.feedback_ids || []).length} 条{p.pr_number ? ` · PR #${p.pr_number}` : ''}</div>
          </li>
        ))}
      </ul>
      {sel && <ProposalDetail key={sel} id={sel} onChanged={onChanged} />}
    </div>
  )
}

function SkillTab({ skill, err }) {
  if (err && !skill) return <div className="error">{err}</div>
  if (!skill) return <div className="muted">加载中…</div>
  return (
    <>
      <div className="panel narrow">
        <h3>explainer-video-pipeline</h3>
        <dl className="kv"><dt>版本</dt><dd><b>{skill.version}</b></dd><dt>commit</dt><dd><code>{skill.commit || '—'}</code></dd></dl>
        <p className="hint">每个任务和工作流运行都记下创建时的版本和 commit。版本只会在有人合并 skillfix PR 后升级。</p>
      </div>
      <div className="section-head"><h2>已合并的改进</h2></div>
      {!skill.history?.length ? <div className="empty">还没有合并过改进。</div> : (
        <table className="tasks"><tbody>
          {skill.history.map((h) => (
            <tr key={h.id}>
              <td><b>{h.id}</b><div className="muted"><code>{h.cluster}</code></div></td>
              <td className="muted">{h.reason}</td>
              <td>{h.pr_url ? <a href={h.pr_url} target="_blank" rel="noreferrer">PR #{h.pr_number}</a> : '—'}</td>
              <td>{h.verify ? (h.verify.ok ? <span className="pill done">验证通过</span> : <span className="pill failed">验证未通过</span>) : <span className="muted">未验证</span>}</td>
              <td className="muted">{h.created ? ago(h.created) : ''}</td>
            </tr>
          ))}
        </tbody></table>
      )}
    </>
  )
}

export default function FeedbackPage() {
  const [params, setParams] = useSearchParams()
  const focus = params.get('item')
  const tab = params.get('tab') || 'items'
  const [prSel, setPrSel] = useState(params.get('proposal'))
  const [tick, setTick] = useState(0)
  const refresh = () => setTick((t) => t + 1)
  const [items, ierr] = usePoll(() => api('/feedback'), 5000, (xs) => (xs || []).some((x) => liveFb(x.status)), [tick])
  const [clusters] = usePoll(() => api('/feedback/clusters'), 10000, () => false, [tick])
  const [proposals] = usePoll(() => api('/feedback/proposals'), 6000, (xs) => (xs || []).some((x) => livePr(x.state)), [tick])
  const [skill, serr] = usePoll(() => api('/feedback/skill'), 10000, () => false, [tick])

  const go = (t, extra = {}) => setParams({ tab: t, ...extra })
  const openProposal = (id) => { setPrSel(id); go('proposals', { proposal: id }) }
  const openItem = (id) => go('items', { item: id })
  const counts = { items: items?.length, clusters: clusters?.length, proposals: proposals?.length }
  const backendMissing = ierr === '501' || ierr === '404'

  return (
    <div className="page fb-page">
      <style>{CSS}</style>
      <h1 className="h1-sm">反馈与改进 {skill?.version && <span className="pill">skill v{skill.version}</span>}</h1>
      <p className="sub">对作品点「不满意」后，系统保存当时的运行记录并分析原因：偶发问题用新 seed 或提示词重跑那一步；
        同类问题够 {RULE_MIN} 条就起草规则改进，用复现输入对比改前改后的指标，再开 GitHub PR。</p>
      <div className="fb-note">规则改进<b>只在有人合并 PR 之后生效</b>，系统不会自己合并，也不会改线上代码。</div>
      {ierr && <div className="error">{backendMissing ? '反馈后端还没有接上，暂时没有数据。' : ierr}</div>}
      <div className="fb-tabs">
        {TABS.map(([k, l]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => go(k)}>{l}
            {counts[k] != null && <span className="n">{counts[k]}</span>}</button>
        ))}
      </div>
      {tab === 'items' && <ItemsTab items={items} focus={focus} onChanged={refresh} onOpenProposal={openProposal} />}
      {tab === 'clusters' && <ClustersTab clusters={clusters} onOpenProposal={openProposal} onOpenItem={openItem} />}
      {tab === 'proposals' && <ProposalsTab proposals={proposals} sel={prSel} setSel={setPrSel} onChanged={refresh} />}
      {tab === 'skill' && <SkillTab skill={skill} err={serr} />}
    </div>
  )
}
