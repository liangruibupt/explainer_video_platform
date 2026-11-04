import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'

// 引导创作: the form mode of the 对话创作 Agent. Step 1 picks what to make, step 2 fills the
// fields (the generation node's param schema from GET /api/workflows/nodes plus a few text
// fields), step 3 confirms: a chat session is created with the mode fixed and a synthesized
// first message, then the page opens it in /chat.

const KINDS = {
  image: {
    label: '图像', desc: '封面、海报、插画（Qwen-Image）', node: 'image.qwen', params: ['ratio'],
    text: [['subject', '画什么', '例如：秋天的杭州西湖，湖边有一棵红枫', true],
      ['style', '风格', '例如：写实摄影、水彩、扁平插画'], ['use', '用途', '例如：小红书封面、公众号头图']],
  },
  video: {
    label: '视频', desc: '短视频镜头（MiniMax H3，带音轨）', node: 'video.h3', params: ['aspect', 'seconds'],
    text: [['subject', '拍什么', '例如：清晨的海边，海浪拍打礁石', true],
      ['style', '风格与镜头', '例如：电影感、慢速推镜头、暖色调'],
      ['reference', '首帧参考', '想要的首帧画面（会先生成一张首帧图）']],
  },
  music: {
    label: '音乐', desc: '歌曲或纯音乐（YuE2）', node: 'music.yue2', params: ['instrumental'],
    text: [['subject', '主题', '例如：给咖啡店写一首轻快的歌', true],
      ['style', '曲风', '例如：city pop、钢琴民谣、lo-fi'], ['lyrics_lang', '歌词语言', '例如：中文、英文']],
  },
  explainer: {
    label: '讲解视频', desc: '原理讲解、幻灯片式讲解，带旁白和字幕', node: 'explainer.video',
    params: ['seconds', 'ratio', 'language', 'captions', 'illustrations', 'clips'],
    text: [['subject', '讲什么', '例如：为什么天空是蓝色的', true],
      ['brief', '要点', '想讲到的要点、受众（可选）']],
  },
}
const PARAM_LABEL = {
  ratio: '比例', aspect: '画幅', seconds: '时长（秒）', instrumental: '纯音乐', language: '旁白语言',
  captions: '逐字字幕', illustrations: 'AI 插图张数', clips: 'AI 视频镜头段数',
}

const CSS = `
.guide-steps { display: flex; gap: 8px; margin: 6px 0 18px; font-size: 13px; color: var(--ink3); }
.guide-steps b { color: var(--accent); }
.guide-kinds { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
.guide-kind { text-align: left; cursor: pointer; padding: 16px; border: 1px solid var(--line);
  border-radius: 12px; background: var(--card); }
.guide-kind:hover, .guide-kind.sel { border-color: var(--accent); }
.guide-kind b { display: block; font-size: 16px; margin-bottom: 4px; }
.guide-form { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 10px 14px; align-items: center; }
.guide-form label { font-size: 13px; color: var(--ink2); }
.guide-form input[type=text], .guide-form input[type=number], .guide-form select, .guide-form textarea {
  border: 1px solid var(--line2); border-radius: 8px; padding: 7px 10px; background: #fff; font: inherit; width: 100%; }
.guide-form textarea { min-height: 64px; resize: vertical; }
.guide-actions { display: flex; gap: 10px; margin-top: 18px; align-items: center; }
.guide-actions .primary { margin-left: auto; }
.guide-msg { white-space: pre-wrap; font-size: 14px; background: #fff; border: 1px solid var(--line);
  border-radius: 10px; padding: 12px 14px; }
`

function Field({ name, schema, value, onChange }) {
  const label = PARAM_LABEL[name] || schema.label || name
  let input
  if (schema.enum) {
    input = (
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {schema.enum.map((v) => <option key={v} value={v}>{v}</option>)}
      </select>
    )
  } else if (schema.type === 'boolean') {
    input = <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
  } else if (schema.type === 'integer' || schema.type === 'number') {
    input = (
      <input type="number" value={value} min={schema.min} max={schema.max}
             step={schema.type === 'integer' ? 1 : 0.5}
             onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))} />
    )
  } else {
    input = <input type="text" value={value} onChange={(e) => onChange(e.target.value)} />
  }
  const range = schema.min != null && schema.max != null ? `（${schema.min}–${schema.max}）` : ''
  return <><label>{label}{range}</label><div>{input}</div></>
}

function clamp(v, s) {
  if (typeof v !== 'number') return s.default
  if (s.min != null && v < s.min) return s.min
  if (s.max != null && v > s.max) return s.max
  return s.type === 'integer' ? Math.round(v) : v
}

function message(kind, k, texts, values, decl) {
  const lines = [`我想做${k.label}：${texts.subject.trim()}`]
  for (const [key, label] of k.text.slice(1)) {
    if (texts[key]?.trim()) lines.push(`${label}：${texts[key].trim()}`)
  }
  for (const p of k.params) {
    const s = decl?.params?.[p]
    if (!s) continue
    let v = s.type === 'integer' || s.type === 'number' ? clamp(values[p], s) : values[p]
    if (typeof v === 'boolean') v = v ? '是' : '否'
    lines.push(`${PARAM_LABEL[p] || p}：${v}`)
  }
  lines.push('以上就是全部要求，没说的按常用默认，直接开始制作。')
  return lines.join('\n')
}

export default function GuidePage() {
  const nav = useNavigate()
  const [step, setStep] = useState(1)
  const [kind, setKind] = useState(null)
  const [nodes, setNodes] = useState(null)
  const [texts, setTexts] = useState({})
  const [values, setValues] = useState({})
  const [web, setWeb] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  useEffect(() => {
    api('/workflows/nodes').then((ls) => setNodes(Object.fromEntries(ls.map((d) => [d.type, d]))))
      .catch((e) => setErr(e.message))
  }, [])

  const k = kind && KINDS[kind]
  const decl = k && nodes?.[k.node]

  const choose = (kd) => {
    setKind(kd); setTexts({}); setErr(null)
    const d = nodes?.[KINDS[kd].node]
    setValues(Object.fromEntries(KINDS[kd].params.filter((p) => d?.params?.[p])
      .map((p) => [p, d.params[p].default ?? ''])))
    setStep(2)
  }

  const msg = useMemo(() => (k && texts.subject ? message(kind, k, texts, values, decl) : ''),
    [kind, k, texts, values, decl])

  const next = () => {
    if (!texts.subject || texts.subject.trim().length < 2) { setErr(`请先填写「${k.text[0][1]}」`); return }
    setErr(null); setStep(3)
  }

  const create = async () => {
    setBusy(true); setErr(null)
    try {
      const r = await api('/chat/sessions', {
        method: 'POST', body: JSON.stringify({ mode: kind, allow_web: web, message: msg }),
      })
      nav(`/chat?session=${r.id}`)
    } catch (e) { setErr(e.message); setBusy(false) }
  }

  return (
    <div className="page">
      <style>{CSS}</style>
      <h1 className="h1-sm">引导创作</h1>
      <p className="sub">按步骤填参数，不用写提示词。确认后交给对话创作的 Agent 生成工作流并开始制作，之后可以在对话里继续修改。</p>
      <div className="guide-steps">
        {['选择类型', '填写内容', '确认'].map((t, i) => (
          <span key={t}>{i > 0 && '→ '}{step === i + 1 ? <b>{i + 1}. {t}</b> : `${i + 1}. ${t}`}</span>
        ))}
      </div>

      {step === 1 && (
        <div className="guide-kinds">
          {Object.entries(KINDS).map(([kd, x]) => (
            <button key={kd} className={`guide-kind ${kind === kd ? 'sel' : ''}`} onClick={() => choose(kd)}
                    disabled={!nodes}>
              <b>{x.label}</b><span className="muted">{x.desc}</span>
            </button>
          ))}
        </div>
      )}

      {step === 2 && k && (
        <div className="panel narrow">
          <h3>{k.label}</h3>
          <div className="guide-form">
            {k.text.map(([key, label, ph, req]) => (
              <FieldText key={key} label={label + (req ? ' *' : '')} ph={ph} long={key === 'brief' || key === 'subject'}
                         value={texts[key] || ''} onChange={(v) => setTexts({ ...texts, [key]: v })} />
            ))}
            {k.params.filter((p) => decl?.params?.[p]).map((p) => (
              <Field key={p} name={p} schema={decl.params[p]} value={values[p]}
                     onChange={(v) => setValues({ ...values, [p]: v })} />
            ))}
            <label>联网检索</label>
            <div>
              <label className="toggle" style={{ marginLeft: 0 }}>
                <input type="checkbox" checked={web} onChange={(e) => setWeb(e.target.checked)} />
                <span />允许联网检索资料
              </label>
            </div>
          </div>
          <div className="guide-actions">
            <button className="btn" onClick={() => setStep(1)}>← 上一步</button>
            <button className="primary" onClick={next}>下一步 <span aria-hidden>→</span></button>
          </div>
        </div>
      )}

      {step === 3 && k && (
        <div className="panel narrow">
          <h3>确认</h3>
          <p className="muted">将以「{k.label}」模式新建一段对话，发送下面这条消息：</p>
          <div className="guide-msg">{msg}</div>
          <p className="muted">Agent 规划工作流约需 30 秒到 1 分钟。</p>
          <div className="guide-actions">
            <button className="btn" disabled={busy} onClick={() => setStep(2)}>← 修改</button>
            <button className="primary" disabled={busy} onClick={create}>
              {busy ? 'Agent 正在规划…' : '开始制作'} <span aria-hidden>→</span>
            </button>
          </div>
        </div>
      )}
      {err && <div className="error">{err}</div>}
    </div>
  )
}

function FieldText({ label, ph, value, onChange, long }) {
  return (
    <>
      <label>{label}</label>
      <div>
        {long
          ? <textarea value={value} placeholder={ph} onChange={(e) => onChange(e.target.value)} />
          : <input type="text" value={value} placeholder={ph} onChange={(e) => onChange(e.target.value)} />}
      </div>
    </>
  )
}
