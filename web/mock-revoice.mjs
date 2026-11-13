// Dev-only mock for the 配音 panel (docs/REVOICE_CONTRACT.md §2–§4). Not part of the build.
// Serves web/dist, fakes /api/jobs/{id}, /editor, /versions, /assist, /consistency, /revoice and a
// dummy /motion/ workbench that keeps its project in localStorage (flushing it on beforeunload).
//   node mock-revoice.mjs [port] [sampleJobDir]
// Reads the sample job read-only; state lives in memory. Test knobs: POST /api/_mock/mode {drift, fail}.
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const PORT = +(process.argv[2] || 8813)
const SAMPLE = process.argv[3] || '/home/ubuntu/workplace/explainer_video_platform/data/jobs/20261111-075413-d48e69'
const DIST = path.join(here, 'dist')
const read = (f) => JSON.parse(fs.readFileSync(path.join(SAMPLE, f), 'utf8'))

const base = read('job.json')
const SCENES = read('scenes.json') // [{id, title, narration, …}]
const TL = read('remotion/src/data/timeline.json')
const CAPS = read('remotion/src/data/captions.json')
const AUD = read('remotion/src/data/audio.json')
const now = () => Date.now() / 1000
const jobs = {
  rm1: { ...base, id: 'rm1', status: 'done', renderer: 'remotion', scenes: SCENES },
}
const idx = (sid) => TL.scenes.findIndex((s) => s.id === sid)
const TOTAL = TL.scenes.reduce((a, s) => a + s.duration, 0)
const clipOf = (id, cardId, start, duration, props, label) => ({
  id, cardId, start, duration, inOffset: 0, speed: 1, opacity: 1, scale: 1, x: 0, y: 0, props, label })

const INITIAL = {
  name: TL.scenes[0].label, fps: TL.fps, width: TL.width, height: TL.height, background: TL.background,
  source: `${TL.scenes[0].label}@${TOTAL}f#${TL.revision}`,
  tracks: [
    { id: 'tr-shots', name: '镜头', clips: TL.scenes.map((s) => clipOf(`clip-${s.id}`, `proj:shot:scene-${s.id}`,
      s.from, s.duration, { title: s.label }, s.label)) },
    { id: 'tr-cap', name: '字幕', clips: CAPS.map((c) => clipOf(`clip-${c.id}`, 'proj:caption:caption', c.from, c.duration,
      { text: c.text, color: '#ffffff' }, c.text)) },
    { id: 'tr-sfx', name: '音效', clips: AUD.sfx.map((x) => clipOf(`clip-${x.id}`, 'audio', x.from, x.duration, { src: x.src }, x.label)) },
    { id: 'tr-music', name: '音乐', clips: [clipOf('clip-voice', 'audio', 0, TOTAL, { src: AUD.voice.src }, '配音（已锁定）')] },
  ],
}

// expected consistency fixes (contract reference numbers)
const NEW_NAR = {
  token_engine: 'Token是模型服务的计费单位。关键是提升推理调用量，让企业接入后天天用起来，预估贡献约百分之三十。',
  migration_engine: '迁移帮助客户把现有系统和数据搬上平台。核心增长点是存量大客户的整体上云与模型替换，预估贡献约百分之十五。',
  recap: '算力打底，Token放量，Agent拉动场景，迁移带来存量。三五、三十、二十、十五，合力实现三年三倍。',
}
const GROW = { recap: 13 } // S7 +0.43 s; S4/S6 shorter -> padding absorbs it

const versions = { rm1: [{ n: 1, file: 'final.mp4', status: 'done', created: now() - 3600, warnings: [], error: null }] }
const takes = { rm1: [] }
const live = { rm1: Object.fromEntries(SCENES.map((s) => [s.id, s.narration])) }
const mode = { drift: false, fail: null } // fail: {path: 'revoice'|'consistency'|'apply', code}
const posts = { versions: [], editor: [], assist: [], consistency: [], revoice: [], apply: [], undo: [], motion: 0 }

const send = (res, code, body, type = 'application/json') => {
  res.writeHead(code, { 'Content-Type': type, 'Cache-Control': 'no-store' })
  res.end(type === 'application/json' ? JSON.stringify(body) : body)
}
const readBody = (req) => new Promise((ok) => {
  let b = ''; req.on('data', (c) => { b += c }); req.on('end', () => { try { ok(JSON.parse(b || '{}')) } catch { ok({}) } })
})
const MIME = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.mp4': 'video/mp4', '.mp3': 'audio/mpeg',
               '.jpg': 'image/jpeg', '.png': 'image/png', '.svg': 'image/svg+xml' }
function serveFile(res, file) {
  if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return send(res, 404, { detail: 'not found' })
  res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' })
  fs.createReadStream(file).pipe(res)
}
const failFor = (what) => (mode.fail && mode.fail.path === what ? mode.fail : null)
const ERR = { 409: '另一次重新配音正在进行，等它完成后再试', 500: '内部错误' }

const MOTION_PAGE = `<!doctype html><meta charset="utf-8"><title>mock workbench</title>
<body style="margin:0;font:13px sans-serif;background:#111;color:#eee;padding:16px">
<h3 style="margin:0 0 6px">Mock Motion Workbench <small id="q"></small></h3>
<pre id="proj" style="white-space:pre-wrap;font:12px monospace"></pre>
<script>
const KEY = 'shotcraft-workbench-project-v1';
let p = null;
try { p = JSON.parse(localStorage.getItem(KEY)); } catch (e) {}
if (!p || !Array.isArray(p.tracks)) { p = ${JSON.stringify(INITIAL)}; localStorage.setItem(KEY, JSON.stringify(p)); }
window.__project = p;
document.getElementById('q').textContent = 'loaded ' + new Date().toISOString().slice(11, 23) + ' ' + p.source;
var lines = p.tracks[0].clips.map(function (c) { return c.id + ' start=' + c.start + ' dur=' + c.duration; });
var cap = p.tracks[1].clips.filter(function (c) { return c.id === 'clip-c029' || c.id === 'clip-c014'; });
cap.forEach(function (c) { lines.push(c.id + ' ' + c.props.text); });
var v = p.tracks[3].clips[0]; lines.push('voice dur=' + v.duration);
document.getElementById('proj').textContent = lines.join('\\n');
window.addEventListener('beforeunload', function () { localStorage.setItem(KEY, JSON.stringify(window.__project)); });
</script>`

function consistency(body) {
  const ins = String(body.instruction || '')
  if (ins.includes('500')) return [500, { detail: 'Opus 调用失败' }]
  if (ins.includes('409')) return [409, { detail: '编辑器没有连到这个作品' }]
  return [200, {
    summary: '画面里的拆分已改成 35/30/20/15，旁白和回顾字幕还是旧数字，有 3 段需要改。',
    issues: [
      { scene_id: 'token_engine', where: 'narration', found: '约百分之二十五', expected: '约百分之三十', why: 'S2 新的拆分 0.6/2.0 = 30%' },
      { scene_id: 'migration_engine', where: 'narration', found: '约百分之二十', expected: '约百分之十五', why: 'S2 新的拆分 0.3/2.0 = 15%' },
      { scene_id: 'recap', where: 'narration', found: '三五、二五、二十、二十', expected: '三五、三十、二十、十五', why: '回顾要和前面四段一致' },
      { scene_id: 'recap', where: 'caption', found: '三五二五二十二十', expected: '三五三十二十十五', why: '字幕跟旁白一致' },
    ],
    narrations: NEW_NAR,
    props: [{ op: 'set_props', clip_id: 'clip-c029', props: { text: '三五三十二十十五' } }],
    rejected: ['gpu_engine 改写超过 40% 的字，没有采用'],
  }]
}

function newTake(id, b) {
  const list = takes[id]
  const n = list.length + 1
  const nar = b.narrations || {}
  const ids = b.all ? SCENES.map((s) => s.id) : Object.keys(nar)
  const scenes = ids.map((sid) => {
    const t = TL.scenes[idx(sid)]
    const d = nar[sid] != null ? (GROW[sid] || 0) : 0
    return { id: sid, old_text: live[id][sid], new_text: nar[sid] ?? live[id][sid],
      old_frames: t.duration, new_frames: t.duration + d, delta_frames: d }
  })
  const delta = scenes.reduce((a, s) => a + s.delta_frames, 0)
  const first = scenes.find((s) => s.delta_frames)
  const r = { n, status: 'queued', created: now(), ended: null, scenes: [], shift_from: null,
    total_frames_old: TOTAL, total_frames_new: null, voice_drift: false, levels: null, error: null, timing: null }
  list.push(r)
  setTimeout(() => { r.status = 'running'; r.stage = `合成配音 1/${scenes.length}` }, 1200)
  setTimeout(() => {
    Object.assign(r, { status: 'ready', ended: now(), scenes, shift_from: first ? first.id : null,
      total_frames_new: TOTAL + delta, voice_drift: !b.all && mode.drift,
      levels: { voice_rms_db: -19.4, music_gap_db: -33.1 }, timing: { polly_s: 2.1, whisper_s: 9.4, mix_s: 3.2, total_s: 15.0 } })
    delete r.stage
  }, 4500)
  return r
}

// shift/extend clips like §2 Apply (enough for the mock: one growing scene at most per take)
function transform(project, r, sign) {
  const p = JSON.parse(JSON.stringify(project))
  let total = 0
  for (const s of r.scenes) {
    if (!s.delta_frames) continue
    const t = TL.scenes[idx(s.id)]
    const d = sign * s.delta_frames
    const end = t.from + t.duration
    for (const tr of p.tracks) {
      for (const c of tr.clips) {
        if (c.cardId === `proj:shot:scene-${s.id}`) c.duration += d
        else if (c.id !== 'clip-voice' && c.start >= end) c.start += d
      }
    }
    total += d
  }
  const v = p.tracks.flatMap((t) => t.clips).find((c) => c.id === 'clip-voice')
  v.start = 0; v.duration += total
  for (const s of r.scenes) {
    const cap = s.id === 'recap' && p.tracks[1].clips.find((c) => c.id === 'clip-c029')
    if (cap) cap.props.text = sign > 0 ? '三五三十二十十五' : '三五二五二十二十'
  }
  p.source = `${p.name}@${v.duration}f#${TL.revision}`
  return p
}

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://x')
  const p = u.pathname
  let m
  if (p === '/api/_mock/posts') return send(res, 200, posts)
  if (p === '/api/_mock/mode' && req.method === 'POST') { Object.assign(mode, await readBody(req)); return send(res, 200, mode) }
  if (p.startsWith('/motion')) { posts.motion += 1; return send(res, 200, MOTION_PAGE, 'text/html') }
  if ((m = /^\/api\/jobs\/([^/]+)$/.exec(p))) return jobs[m[1]] ? send(res, 200, jobs[m[1]]) : send(res, 404, { detail: 'job not found' })
  if (!jobs[(/^\/api\/jobs\/([^/]+)/.exec(p) || [])[1]] && p.startsWith('/api/jobs/')) return send(res, 404, { detail: 'job not found' })
  if ((m = /^\/api\/jobs\/([^/]+)\/editor$/.exec(p)) && req.method === 'POST') {
    posts.editor.push(m[1])
    return send(res, 200, { job_id: m[1], url: '/motion/?import=project' })
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/versions$/.exec(p))) {
    const list = versions[m[1]]
    if (req.method === 'POST') {
      const b = await readBody(req); posts.versions.push(b)
      const v = { n: list.length + 1, status: 'queued', created: now(), warnings: [], error: null }
      list.push(v)
      setTimeout(() => { v.status = 'done' }, 3000)
      return send(res, 202, v)
    }
    return send(res, 200, list)
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/assist$/.exec(p)) && req.method === 'POST') {
    const b = await readBody(req); posts.assist.push(b)
    if (String(b.message).includes('旁白')) {
      return send(res, 200, { reply: '旁白和配音在这里改不了：配音轨已锁定。', changes: [], project: null, ops: [],
        rejected: ['narration: 配音轨已锁定'], scene_edit: null })
    }
    const project = JSON.parse(JSON.stringify(b.project))
    const c = project.tracks[0].clips[1]
    c.props.title = '三年三倍的含义'
    return send(res, 200, { reply: '标题已经改好，点「应用修改」放进编辑器。', changes: [`${c.label} 标题 → 三年三倍的含义`],
      project, ops: [{ op: 'set_props', clip_id: c.id, props: { title: '三年三倍的含义' } }], rejected: [], scene_edit: null })
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/consistency$/.exec(p)) && req.method === 'POST') {
    const b = await readBody(req); posts.consistency.push(b)
    const f = failFor('consistency')
    if (f) return send(res, f.code, { detail: f.code === 502 ? 'Opus 调用失败' : ERR[f.code] || '出错了' })
    const [code, body] = consistency(b)
    return send(res, code, body)
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/revoice$/.exec(p))) {
    const list = takes[m[1]]
    if (req.method === 'POST') {
      const b = await readBody(req); posts.revoice.push(b)
      const f = failFor('revoice')
      if (f) return send(res, f.code, { detail: ERR[f.code] || '出错了' })
      if (list.some((r) => r.status === 'queued' || r.status === 'running')) return send(res, 409, { detail: ERR[409] })
      return send(res, 202, newTake(m[1], b))
    }
    return send(res, 200, [...list].reverse())
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/revoice\/(\d+)\/audio\/([a-z_0-9]+)\.mp3$/.exec(p))) {
    const i = idx(m[3])
    if (i < 0) return send(res, 404, { detail: '没有这段配音' })
    return serveFile(res, path.join(SAMPLE, 'audio', `s${String(i).padStart(2, '0')}.mp3`))
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/revoice\/(\d+)(?:\/(apply|undo))?$/.exec(p))) {
    const list = takes[m[1]]
    const r = list.find((x) => x.n === +m[2])
    if (!r) return send(res, 404, { detail: '没有这次配音' })
    if (!m[3]) return send(res, 200, r)
    if (req.method !== 'POST') return send(res, 405, { detail: 'POST only' })
    const b = await readBody(req)
    if (m[3] === 'apply') {
      posts.apply.push(b)
      const f = failFor('apply')
      if (f) return send(res, f.code, { detail: f.code === 409 ? '只有配好的配音才能应用' : '应用时出错' })
      if (r.status !== 'ready') return send(res, 409, { detail: '只有配好的配音才能应用' })
      r.status = 'applied'
      for (const s of r.scenes) live[m[1]][s.id] = s.new_text
      const changes = r.scenes.filter((s) => s.delta_frames).map((s) => `${s.id} 变长 ${s.delta_frames} 帧，后面的片段顺延`)
      return send(res, 200, { status: 'applied', project: transform(b.project, r, 1), changes })
    }
    posts.undo.push(b)
    const last = [...list].reverse().find((x) => x.status === 'applied')
    if (!last || last.n !== r.n) return send(res, 409, { detail: '只能撤销最近一次应用的配音' })
    last.status = 'undone'
    for (const s of last.scenes) live[m[1]][s.id] = s.old_text
    return send(res, 200, { status: 'undone', project: transform(b.project, last, -1) })
  }
  if ((m = /^\/api\/jobs\/([^/]+)\/files\/(.+)$/.exec(p))) return serveFile(res, path.join(SAMPLE, path.basename(m[2])))
  if (p.startsWith('/api/')) return send(res, 200, [])
  const f = path.join(DIST, path.normalize(p).replace(/^(\.\.[/\\])+/, ''))
  if (p !== '/' && fs.existsSync(f) && fs.statSync(f).isFile()) return serveFile(res, f)
  return serveFile(res, path.join(DIST, 'index.html'))
})
server.listen(PORT, '127.0.0.1', () => console.log(`mock on http://127.0.0.1:${PORT}`))
